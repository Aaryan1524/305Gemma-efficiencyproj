# Alpha implementation verification

Checked 2026-11-06 on arm64 macOS 27.2 using a fresh Python 3.12.14 environment
at `/tmp/focusledger-alpha-venv`. The repository's pre-existing Python 3.14 venv
was preserved. macOS 26 remains the intended initial target, not a newly verified
configuration. CI uses macOS 15 for build/unit checks; it is not evidence of full
UI support on that OS.

| Check | Result |
| --- | --- |
| Install pinned requirements-dev.txt in fresh Python 3.12 | Passed |
| pip check | Passed |
| make check with temporary Python | Passed: 20 tests, Ruff, shell syntax, local documentation links |
| make demo | Passed: synthetic report, 3 sessions, 240 minutes; no model calls |
| Native AppKit/Quartz/Vision/tracker/UI imports | Passed |
| Both app bundle builds in isolated temporary directory | Passed; Swift audio tap API deprecation warning on macOS 27 |
| Apple Vision OCR of generated text image | Passed outside sandbox; sandbox execution returned no text |
| git diff --check | Passed |
| Browser visual preview | Opened synthetic report in native Chrome, inspected layout and session totals; fixed offline empty-state headline and unsupported goal completion percentage |
| Native single-window capture, OCR, neighbor exclusion | Passed with generated overlapping windows; missing windows fail closed |
| Local Gemma checkpoint and daily synthesis | Passed with installed gemma3:4b (Ollama 0.32.3), synthetic data only |
| Clean checkout launcher/onboarding | Started from exported checkout with temporary data; dependencies, both app builds, and UI process startup succeeded |
| Voice, permission granting/denial, login and uninstall | Not exercised interactively; optional features / follow-up compatibility checks |
| GitHub Actions | Passed all steps: [run 42299210572](https://github.com/Aaryan1524/FocusLedger/actions/runs/42299210572) |
| Private vulnerability reporting | Enabled and verified |

Tests cover PID-based window selection, blocked/missing windows, single-window
capture arguments, buffer retries and idle accounting, midnight closure, path
precedence, migration preservation/failure, synthetic demo isolation, day selection,
HTML escaping, report acknowledgement failures, and model errors. They use mocked
capture and model calls; they do not establish classification quality or native
permission behavior.

CoreGraphics returned no image during native verification. The implementation now
uses ScreenCaptureKit with a desktop-independent window filter and services the
main run loop for callbacks. The native smoke uses only its own generated windows
and temporary ledger; no personal activity is read or uploaded.

This is an early source alpha, not a signed/notarized standalone distribution.
See releasing.md for reproduction and remaining compatibility coverage.
