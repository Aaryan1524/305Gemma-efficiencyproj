"""Session manager + capture loop.

Polls macOS idle time to open/close honest work sessions (no phantom
hours from a laptop left sitting idle), and while a session is open,
gates the capture pipeline below it.
"""

import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta

from AppKit import NSWorkspace
import mss
import mss.tools
import Quartz

import gemma
import vision_ocr

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

LEDGER_PATH = "ledger.jsonl"


def idle_seconds():
    """Seconds since the last keyboard/mouse input, via ioreg. No permissions needed."""
    out = subprocess.check_output(
        "ioreg -c IOHIDSystem | awk '/HIDIdleTime/ {print $NF/1000000000; exit}'",
        shell=True, text=True
    )
    return float(out.strip())


def frontmost():
    """Return (app_name, window_title) of the focused window.

    Capturing focus explicitly is what stops Gemma from calling background
    audio (a music tab you aren't looking at) the "activity" — we only ever
    judge the app the user is actually in front of.
    """
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    name = app.localizedName()
    title = ""
    wins = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID)
    for w in wins:
        if w.get("kCGWindowOwnerName") == name and w.get("kCGWindowName"):
            title = w["kCGWindowName"]
            break
    return name, title


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


CAPTURE_DIR = "captures"

# In-memory buffer of OCR'd samples awaiting the next 30-min checkpoint.
# Each item: {"t": "HH:MM:SS", "app": ..., "title": ..., "text": ...}
# No image ever lives here — pixels are gone before this list grows.
BUFFER = []
_last_hash = None


def capture_tick():
    """One capture opportunity: identify the focused window, respect the blocklist,
    screenshot only on change, OCR it, delete the image immediately, buffer the text.

    The image exists on disk for the length of one OCR call and no longer. What
    survives is text, and only until the next checkpoint turns it into a verdict.
    """
    global _last_hash
    app, title = frontmost()
    if is_blocked(app, title):
        print(f"⛔ blocked: {app}")
        return
    print(f"👁 {app} — {title}")

    with mss.MSS() as sct:
        img = sct.grab(sct.monitors[1])
    digest = hashlib.md5(img.rgb).hexdigest()
    if digest == _last_hash:
        print("· unchanged")
        return
    _last_hash = digest

    now = datetime.now()
    path = os.path.join(CAPTURE_DIR, f"capture_{now:%Y%m%d_%H%M%S_%f}.png")
    mss.tools.to_png(img.rgb, img.size, output=path)
    try:
        text = vision_ocr.ocr(path)
    finally:
        # Delete the image no matter what — an OCR failure must not leave pixels behind.
        try:
            os.remove(path)
        except OSError:
            pass

    BUFFER.append({"t": _hms(now), "app": app, "title": title, "text": text})
    print(f"🔎 ocr'd ({len(text)} chars) → image deleted | buffer={len(BUFFER)}")


def _hms(t):
    return t.strftime("%H:%M:%S")


def _duration(start, end):
    secs = int((end - start).total_seconds())
    h, rem = divmod(secs, 3600)
    m, _ = divmod(rem, 60)
    return f"{h}h {m}m"


def append_ledger(obj):
    """Append one JSON object as a line to the ledger. This is the only thing
    that ever touches disk — verdicts and session markers, never pixels or raw text."""
    with open(LEDGER_PATH, "a") as f:
        f.write(json.dumps(obj) + "\n")


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
        print(f"📌 today's goals (from ledger): {existing}")
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
        return
    n = len(BUFFER)
    now = datetime.now()
    end = until or now
    minutes = round((end - since).total_seconds() / 60, 1) if since else round(n * POLL_INTERVAL / 60, 1)
    tag = f" ({reason})" if reason else ""
    print(f"🧠 checkpoint{tag}: judging {n} samples with Gemma…")
    try:
        verdict = gemma.checkpoint(goals, BUFFER)
    except Exception as e:
        print(f"⚠ checkpoint failed ({e}); keeping buffer for next attempt")
        return
    append_ledger({
        "type": "checkpoint",
        "t": _hms(now),
        "samples": n,
        "minutes": minutes,
        "verdict": verdict,
    })
    BUFFER.clear()
    print(f"📒 verdict: {verdict.get('category')} | aligned={verdict.get('aligned')} "
          f"| {verdict.get('activity')}")


def main():
    """Session state machine: poll idle time, open/close sessions, drive capture_tick()."""
    session = None  # {"start": datetime} while open, else None
    last_checkpoint = None  # datetime of the last checkpoint within the current session

    # Line-buffer so status prints appear live even when piped (e.g. tee during a demo).
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass

    os.makedirs(CAPTURE_DIR, exist_ok=True)
    print(f"FocusLedger session manager starting (DEV_MODE={DEV_MODE})")

    goals = prompt_goals()
    print("🔥 warming up Gemma…")
    try:
        gemma.warmup()
    except Exception as e:
        print(f"⚠ Gemma warmup failed ({e}). Is `ollama serve` running? Continuing anyway.")
    print("ready. Tracking sessions — Ctrl-C to stop.\n")

    try:
        while True:
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
                    last_active = now - timedelta(seconds=idle)
                    run_checkpoint(goals, since=last_checkpoint, until=last_active,
                                   reason="session close")  # flush before closing
                    dur = _duration(session["start"], last_active)
                    append_ledger({"type": "session_close", "t": _hms(last_active), "duration": dur})
                    print(f"■ session closed {_hms(last_active)} (duration {dur})")
                    session = None
                elif idle < ACTIVE_WINDOW:
                    capture_tick()
                    if (now - last_checkpoint).total_seconds() >= CHECKPOINT_INTERVAL:
                        run_checkpoint(goals, since=last_checkpoint, until=now)
                        last_checkpoint = now

            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        if session is not None:
            now = datetime.now()
            run_checkpoint(goals, since=last_checkpoint, until=now, reason="shutdown")  # don't lose the last buffer
            dur = _duration(session["start"], now)
            append_ledger({"type": "session_close", "t": _hms(now), "duration": dur})
            print(f"\n■ session closed {_hms(now)} (duration {dur})")
        print("FocusLedger stopped.")


if __name__ == "__main__":
    main()
