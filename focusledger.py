"""Session manager + capture loop.

Polls macOS idle time to open/close honest work sessions (no phantom
hours from a laptop left sitting idle), and while a session is open,
gates the capture pipeline below it.
"""

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from datetime import date, datetime, timedelta

from AppKit import NSWorkspace
import Quartz

import gemma
import paths
import vision_ocr
from window_capture import capture_window

BASE = os.path.dirname(os.path.abspath(__file__))
DEV_MODE = bool(os.environ.get("FL_DEV"))

if DEV_MODE:
    POLL_INTERVAL = 2
    ACTIVE_WINDOW = 10
    IDLE_CLOSE = 20
    CHECKPOINT_INTERVAL = 60      # 1 min in dev so you can watch verdicts land
else:
    POLL_INTERVAL = 10
    ACTIVE_WINDOW = 60
    IDLE_CLOSE = 600
    CHECKPOINT_INTERVAL = 1800    # 30 min in real use

LEDGER_PATH = paths.LEDGER_PATH


def idle_seconds():
    """Seconds since the last keyboard/mouse input, via ioreg. No permissions needed."""
    out = subprocess.check_output(
        "ioreg -c IOHIDSystem | awk '/HIDIdleTime/ {print $NF/1000000000; exit}'",
        shell=True, text=True
    )
    return float(out.strip())


def frontmost():
    """Return (app_name, window_title, window_id) of the focused window.

    Capturing focus explicitly is what stops Gemma from calling background
    audio (a music tab you aren't looking at) the "activity" — we only ever
    judge the app the user is actually in front of.
    """
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    name = app.localizedName() if app else "Unknown"
    pid = app.processIdentifier() if app else None
    wins = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID)
    for w in wins or []:
        if (w.get("kCGWindowOwnerPID") == pid and w.get("kCGWindowLayer") == 0
                and w.get("kCGWindowName") and w.get("kCGWindowNumber")):
            return name, w["kCGWindowName"], w["kCGWindowNumber"]
    return name, "", None


# Refuse to look at anything sensitive. When blocked we capture nothing at all.
BLOCKED_APPS = {"1Password", "Keychain Access", "Bitwarden", "Messages", "Signal", "FaceTime"}
BLOCKED_TITLE_WORDS = ["bank", "chase", "wells fargo", "password", "login",
                       "sign in", "checkout", "payment", "incognito", "private browsing"]


def is_blocked(app, title):
    """True if this app/window should never be captured (secrets, private browsing, DMs)."""
    if app in BLOCKED_APPS:
        return True
    t = title.lower()
    return any(w in t for w in BLOCKED_TITLE_WORDS)


CAPTURE_DIR = os.path.join(BASE, "captures")

# In-memory buffer of OCR'd samples awaiting the next 30-min checkpoint.
# Each item: {"t": "HH:MM:SS", "app": ..., "title": ..., "text": ...}
# No image ever lives here — pixels are gone before this list grows.
BUFFER = []
_last_hash = None
_flush_requested = False

# A 30-min block at POLL_INTERVAL=10 is up to 180 ticks; that many samples
# makes a prompt Gemma can't finish inside its 180s timeout. Cap the buffer
# so the checkpoint stays answerable — and so a checkpoint that fails (which
# keeps the buffer for a retry) can't ratchet the next prompt even larger.
MAX_BUFFER = 20


def _on_sigusr1(signum, frame):
    """Signal handler for live on-demand checkpoint flushing (e.g. 'View report')."""
    global _flush_requested
    _flush_requested = True


def capture_tick(force=False):
    """One capture opportunity: identify the focused window, respect the blocklist,
    capture one window on change, OCR in memory, and buffer the text.

    Pixels never reach disk. Text survives in memory until a checkpoint succeeds.
    """
    global _last_hash
    app, title, window_id = frontmost()
    if window_id is None or is_blocked(app, title):
        return
    img = capture_window(window_id)
    if img is None:
        return
    pixels = Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(img))
    digest = hashlib.sha256(bytes(pixels)).hexdigest()
    if not force and digest == _last_hash:
        return
    text = vision_ocr.ocr_image(img)
    _last_hash = digest
    now = datetime.now()
    BUFFER.append({"t": _hms(now), "app": app, "title": title, "text": text})
    if len(BUFFER) > MAX_BUFFER:
        # Thin by half rather than dropping the oldest: the verdict should
        # describe the whole block, not just its last few minutes.
        del BUFFER[::2]
    print("🔎 window read")


def _hms(t):
    return t.strftime("%H:%M:%S")


def _duration(start, end):
    secs = max(0, int((end - start).total_seconds()))
    h, rem = divmod(secs, 3600)
    m, _ = divmod(rem, 60)
    if h == 0 and m == 0 and secs > 0:
        return "<1m"
    return f"{h}h {m}m"


def append_ledger(obj):
    """Append one JSON object as a line to the ledger. This is the only thing
    that ever touches disk — verdicts and session markers, never pixels or raw text."""
    os.makedirs(os.path.dirname(LEDGER_PATH), exist_ok=True)
    obj.setdefault("date", date.today().isoformat())
    with open(LEDGER_PATH, "a") as f:
        f.write(json.dumps(obj) + "\n")


def reset_todays_ledger():
    """Preserve history when the UI starts another session."""
    # History is append-only; a subsequent goals row starts the new session.
    return


def load_todays_goals():
    """Return today's goals string if the ledger already has one for today, else None.

    Lets you stop and restart the tracker during the day without being re-prompted
    or duplicating the goals line.
    """
    if not os.path.exists(LEDGER_PATH):
        return None
    today = date.today().isoformat()
    goals = None
    with open(LEDGER_PATH) as f:
        for line in f:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("type") == "goals" and row.get("date") == today:
                goals = row.get("goals")
    return goals


def prompt_goals():
    """Get today's goals: reuse today's if present, otherwise ask once and record them."""
    existing = load_todays_goals()
    if existing:
        print("📌 today's goals loaded")
        return existing
    print("What are your goals for today? (one line, be specific — vague goals read as drift)")
    goals = input("goals> ").strip()
    append_ledger({"type": "goals", "date": date.today().isoformat(), "goals": goals})
    return goals


def run_checkpoint(goals, since=None, until=None, reason=""):
    """Judge the buffered samples against today's goals, append the verdict, clear the buffer.

    This is Gemma call site #1: a 30-minute block of messy screen text becomes one
    structured verdict. After this the raw text is discarded — only the verdict persists.

    `since`/`until` bound the block in wall-clock time (minutes = until − since), not a
    per-sample estimate — so change-gating (skipped identical frames) doesn't make calm
    focused work (reading, watching a lecture) look shorter, and the idle stretch before
    a session closes isn't counted as work.
    """
    if not BUFFER:
        return False
    n = len(BUFFER)
    now = datetime.now()
    end = until or now
    raw_min = (end - since).total_seconds() / 60 if since else (n * POLL_INTERVAL / 60)
    minutes = max(0.5, round(raw_min, 1))
    tag = f" ({reason})" if reason else ""
    print(f"🧠 checkpoint{tag}: judging {n} samples with Gemma…")
    try:
        verdict = gemma.checkpoint(goals, BUFFER)
    except Exception:
        print("⚠ checkpoint failed; keeping buffer for next attempt")
        return False
    append_ledger({
        "type": "checkpoint",
        "date": (since or now).date().isoformat(),
        "t": _hms(now),
        "samples": n,
        "minutes": minutes,
        "verdict": verdict,
    })
    BUFFER.clear()
    print("📒 verdict recorded")
    return True


def main():
    """Session state machine: poll idle time, open/close sessions, drive capture_tick()."""
    global _flush_requested
    session = None  # {"start": datetime} while open, else None
    last_checkpoint = None  # datetime of the last checkpoint within the current session

    # Line-buffer so status prints appear live even when piped (e.g. tee during a demo).
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass

    signal.signal(signal.SIGUSR1, _on_sigusr1)

    paths.initialize()
    print(f"FocusLedger session manager starting (DEV_MODE={DEV_MODE})")

    goals = prompt_goals()
    print("🔥 warming up Gemma…")
    try:
        gemma.warmup()
    except Exception as e:
        print("⚠ Gemma warmup failed. Check Ollama; continuing.")
    print("ready. Tracking sessions — Ctrl-C to stop.\n")

    tracking_day = date.today()
    try:
        while True:
            if date.today() != tracking_day:
                boundary = datetime.combine(date.today(), datetime.min.time())
                if BUFFER and not run_checkpoint(goals, since=last_checkpoint, until=boundary, reason="day boundary"):
                    time.sleep(POLL_INTERVAL)
                    continue
                if session:
                    append_ledger({"type": "session_close", "date": tracking_day.isoformat(), "t": "24:00:00", "duration": _duration(session["start"], boundary)})
                print("FL_DAY_ENDED", flush=True)
                return
            idle = idle_seconds()
            now = datetime.now()

            if session is None:
                if idle < ACTIVE_WINDOW:
                    session = {"start": now}
                    last_checkpoint = now
                    append_ledger({"type": "session_open", "t": _hms(now)})
                    print(f"▶ session opened {_hms(now)}")
            else:
                if idle >= IDLE_CLOSE:
                    # Work ended ~idle seconds ago; don't count the idle gap as activity.
                    last_active = max(now - timedelta(seconds=idle), session["start"])
                    if BUFFER and not run_checkpoint(goals, since=last_checkpoint, until=last_active, reason="session close"):
                        print("FL_FLUSH_FAILED", flush=True)
                        time.sleep(POLL_INTERVAL)
                        continue
                    dur = _duration(session["start"], last_active)
                    append_ledger({"type": "session_close", "t": _hms(last_active), "duration": dur})
                    print(f"■ session closed {_hms(last_active)} (duration {dur})")
                    session = None
                elif idle < ACTIVE_WINDOW:
                    capture_tick()
                    if (now - last_checkpoint).total_seconds() >= CHECKPOINT_INTERVAL:
                        if run_checkpoint(goals, since=last_checkpoint, until=now):
                            last_checkpoint = now

            # Sleep in 0.25s slices so SIGUSR1 (live report flush) is serviced immediately
            waited = 0.0
            while waited < POLL_INTERVAL:
                if _flush_requested:
                    _flush_requested = False
                    now = datetime.now()
                    if session is None:
                        session = {"start": now}
                        last_checkpoint = now
                        append_ledger({"type": "session_open", "t": _hms(now)})
                        print(f"▶ session opened {_hms(now)}")
                    if not BUFFER:
                        capture_tick(force=True)
                    ok = not BUFFER or run_checkpoint(goals, since=last_checkpoint or now, until=now, reason="live report")
                    if ok:
                        last_checkpoint = now
                    print("FL_FLUSH_OK" if ok else "FL_FLUSH_FAILED", flush=True)
                    break
                time.sleep(0.25)
                waited += 0.25
    except KeyboardInterrupt:
        if session is not None:
            now = datetime.now()
            if not BUFFER:
                capture_tick(force=True)
            ok = not BUFFER or run_checkpoint(goals, since=last_checkpoint, until=now, reason="shutdown")
            if not ok:
                print("FL_FLUSH_FAILED", flush=True)
                raise SystemExit(2)
            dur = _duration(session["start"], now)
            append_ledger({"type": "session_close", "t": _hms(now), "duration": dur})
            print(f"\n■ session closed {_hms(now)} (duration {dur})")
        print("FocusLedger stopped.")


if __name__ == "__main__":
    main()
