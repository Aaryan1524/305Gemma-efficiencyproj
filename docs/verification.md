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
| make check with temporary Python | Passed: 18 tests, Ruff, shell syntax, local documentation links |
| make demo | Passed: synthetic report, 3 sessions, 240 minutes; no model calls |
| Native AppKit/Quartz/Vision/tracker/UI imports | Passed |
| Both app bundle builds in isolated temporary directory | Passed; Swift audio tap API deprecation warning on macOS 27 |
| Apple Vision OCR of generated text image | Passed outside sandbox; sandbox execution returned no text |
| git diff --check | Passed |
| Browser visual preview | Unavailable: browser access host unavailable |
| Live permissions, window capture, voice, Ollama inference, login and uninstall | Not run; manual release gates |
| GitHub Actions execution and repository settings | Not run/changed |

Tests cover PID-based window selection, blocked/missing windows, single-window
capture arguments, buffer retries and idle accounting, midnight closure, path
precedence, migration preservation/failure, synthetic demo isolation, day selection,
HTML escaping, report acknowledgement failures, and model errors. They use mocked
capture and model calls; they do not establish classification quality or native
permission behavior.

The roadmap and release checklist retain the remaining gates. No public release,
remote push, signed/notarized distribution, or data upload was performed.
