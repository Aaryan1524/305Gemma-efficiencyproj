# Architecture

`notch.py` owns the PyObjC UI and spawns a tracker. `focusledger.py` polls idle time,
selects and captures a window, buffers Vision OCR, and asks `gemma.py` for verdicts.
`vision_ocr.py` wraps Apple Vision. `paths.py` resolves shared data locations and
legacy migration. `report.py` reconstructs sessions for a selected day and renders
`templates/report.html`, optionally asking Gemma for a reflection.

Ledger rows use types goals, session_open, checkpoint, and session_close. New rows
include an ISO date; time fields retain HH:MM:SS. Checkpoints contain minutes and a
verdict. Readers retain compatibility with legacy rows whose dates follow goals.
The writer is append-only; reports never modify history. Failed periodic checkpoints
retain their start time and buffer. Idle close and midnight retry failures using the original interval. Shutdown
surfaces failure rather than acknowledging success; unclassified memory cannot survive exit.

The UI requests a flush with SIGUSR1. The tracker returns FL_FLUSH_OK or
FL_FLUSH_FAILED on its stdout control stream. Reports wait for acknowledgement
and stop on failure/timeout. End day sends SIGINT and checks the exit result.
Model synthesis failure still permits a report of recorded facts, visibly labeled.

The window capture uses CoreGraphics CGWindowListCreateImage with an explicit
window ID. This API is deprecated; ScreenCaptureKit migration is on the roadmap.
Never replace it with display capture as a compatibility fallback.

The optional Swift Dictate.app owns microphone permission and requires on-device
recognition. FocusLedger.app is an ad-hoc signed launcher tied to the checkout.
No server or remotely hosted UI is required.
