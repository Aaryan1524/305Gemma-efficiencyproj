# Contributing to FocusLedger

Bug reports, documentation, accessibility improvements, and code contributions are welcome.
The initial support target is Apple Silicon macOS 26 or later with Python 3.12.

1. Fork and clone the repository; create a focused branch.
2. Install Python 3.12 and run `make dev`. Ollama and screen permissions are not needed for unit tests.
3. Run `make check` before submitting. Run `make demo` for report changes.
4. For native changes, run `make build` and the manual checklist in [releasing](docs/releasing.md).
5. Open a pull request explaining the problem, resulting behavior, checks run, and remaining limitations.

Keep changes small. Preserve legacy ledger compatibility, local-only inference,
synthetic demo isolation, and fail-closed capture. Add regression tests for changed
behavior, especially privacy boundaries and time accounting. Do not include real
ledgers, screenshots of private work, transcripts, or raw model responses in issues.

Use `make lint`, `make test`, and `make preflight` individually. Preflight checks
local services and optional native helpers; it is separate from deterministic CI.
Use a temporary `FL_DATA_DIR` for manual experiments. Never change another user's
permissions, models, or login items during automated tests.

Maintainers review contributions; this small project has no guaranteed response
schedule. Discuss major redesigns in an issue first. Contributions are under MIT.
See [the roadmap](docs/roadmap.md) for starter tasks and [the code of conduct](CODE_OF_CONDUCT.md).
