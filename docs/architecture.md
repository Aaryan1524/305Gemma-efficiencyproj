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

Window capture uses ScreenCaptureKit SCScreenshotManager with a desktop-independent
single-window filter. The helper services the main run loop while awaiting callbacks.
Missing windows, denied access, or timeouts skip capture; never use a display fallback.

The optional Swift Dictate.app owns microphone permission and requires on-device
recognition. FocusLedger.app is an ad-hoc signed launcher tied to the checkout.
No server or remotely hosted UI is required.

## Live report data flow

`report_metrics.py` computes the three headline metrics, previous-seven-recorded-day
baseline, 30-minute timeline bins, and per-goal time. `goals.py` shares goal parsing,
optional minute targets, and user completion state with the native UI and classifier.
AI reflection is separate from deterministic accounting. An aligned verdict without
a matching stated goal is counted as unmatched time.

New checkpoint rows add `start_at`, `end_at`, and `recorded_at` ISO local timestamps;
`t` now describes the interval end. Legacy end-time/minutes rows retain approximate
timeline support. Missing time is not called idle, and missing days are excluded
from baselines. Goal rows optionally include `goal_plans: [{goal, target_minutes}]`;
`goal_status` rows append explicit `{goal, completed}` decisions. A new goals row
resets completion assessment for that plan without deleting history.

After a saved checkpoint, the tracker publishes an offline live snapshot. Report
publishers serialize through a file lock and atomically replace the HTML. Model
failures preserve the previous unclassified buffer; report I/O failures leave saved
verdicts intact. Browser refresh is local reload only, with no server or telemetry.
View report adds a reflection and live reload while tracking; End day removes reload.
Demos and selected-date exports never auto-refresh. Concurrent manual report actions
are gated so repeated clicks do not queue overlapping model summaries.
