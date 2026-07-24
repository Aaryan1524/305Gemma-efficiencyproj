"""Session manager + capture loop.

Polls macOS idle time to open/close honest work sessions (no phantom
hours from a laptop left sitting idle), and while a session is open,
gates the capture pipeline below it.
"""

import os
import subprocess
import time
from datetime import datetime

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


def capture_tick():
    """Stub — wired up in step 2/3."""
    pass


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
