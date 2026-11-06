# Releasing an alpha

Release from a reviewed commit after CI passes. No automated publishing is configured.
Before public release, enable GitHub private vulnerability reporting and branch rules
requiring review and CI. Confirm MIT attribution and dependency/model license notices;
model weights are downloaded separately and are not distributed in this repository.

## Manual macOS acceptance checklist

Core capture/OCR/model integration can be reproduced with scripts/native_smoke.py
using disposable data paths (see setup.md). For early source alphas, record any
unexercised optional voice/login flows as limitations in the release notes.
Full stable releases require all applicable checks below.

Record commit, hardware, macOS, Python, Ollama, and model versions with results.
Use a clean checkout and temporary FL_DATA_DIR; never attach personal activity.

- Install Python 3.12, run ./start.sh, and complete onboarding from a fresh environment.
- Confirm denied Screen Recording fails clearly; grant, reopen, and capture an allowed window.
- Put synthetic text in a neighboring window; verify only the selected window reaches OCR.
- Confirm blocked, missing, untitled, and protected windows do not cause display fallback.
- Enter typed goals, run a quick checkpoint, View report, then End day.
- Stop Ollama during a checkpoint; confirm failure is visible and no false success appears.
- Restart, verify history survives, start a new day, and select a previous report date.
- Test midnight using mocked dates in tests and a controlled native session before release.
- Try optional voice permission denial and on-device transcription; typed input stays usable.
- Render a demo while tracking; verify it remains synthetic and personal history is unchanged.
- Verify login/uninstall instructions and inspect generated HTML in a browser.

Run make check, make demo, and make build. Update CHANGELOG.md and the verification
record. Tag 0.1.0a1 only after these gates pass; do not describe an unverified build
as stable or notarized. Revert the source revision for rollback; user data is kept
outside the checkout and must never be deleted as part of rollback.
