"""Session manager + capture loop.

Polls macOS idle time to open/close honest work sessions (no phantom
hours from a laptop left sitting idle), and while a session is open,
gates the capture pipeline below it.
"""

import os
import subprocess
import time
from datetime import datetime

from AppKit import NSWorkspace
import Quartz

DEV_MODE = bool(os.environ.get("FL_DEV"))

if DEV_MODE:
    POLL_INTERVAL = 2
    ACTIVE_WINDOW = 10
    IDLE_CLOSE = 20
else:
    POLL_INTERVAL = 10
    ACTIVE_WINDOW = 60
    IDLE_CLOSE = 600


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


def capture_tick():
    """One capture opportunity: identify the focused window, respect the blocklist."""
    app, title = frontmost()
    if is_blocked(app, title):
        print(f"⛔ blocked: {app}")
        return
    print(f"👁 {app} — {title}")


def _hms(t):
    return t.strftime("%H:%M:%S")


def _duration(start, end):
    secs = int((end - start).total_seconds())
    h, rem = divmod(secs, 3600)
    m, _ = divmod(rem, 60)
    return f"{h}h {m}m"


def main():
    """Session state machine: poll idle time, open/close sessions, drive capture_tick()."""
    session = None  # {"start": datetime} while open, else None

    print(f"FocusLedger session manager starting (DEV_MODE={DEV_MODE})")

    try:
        while True:
            idle = idle_seconds()
            now = datetime.now()

            if session is None:
                if idle < ACTIVE_WINDOW:
                    session = {"start": now}
                    print(f"▶ session opened {_hms(now)}")
            else:
                if idle >= IDLE_CLOSE:
                    print(f"■ session closed {_hms(now)} (duration {_duration(session['start'], now)})")
                    session = None
                elif idle < ACTIVE_WINDOW:
                    capture_tick()

            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        if session is not None:
            now = datetime.now()
            print(f"\n■ session closed {_hms(now)} (duration {_duration(session['start'], now)})")
        print("FocusLedger stopped.")


if __name__ == "__main__":
    main()
