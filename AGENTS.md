# Working on FocusLedger

Read README.md and docs/architecture.md first. Keep the Python/PyObjC application
and optional Swift voice helper; avoid unrelated rewrites.

- Use Python 3.12; `make dev`, then `make check`. `make demo` renders synthetic HTML.
- Capture only an identified frontmost window; never fall back to a display.
- Never log screen text, titles, goals, transcripts, or raw model responses.
- Keep writable user files under paths.py and preserve ledger history.
- Demo and tests must not read personal history or contact models without explicit intent.
- Native UI/permission changes require the manual release checklist; report unrun checks honestly.
- Use synthetic fixtures and mock capture/model calls in tests.
- Do not publish releases, change remote settings, or upload private data without authorization.
