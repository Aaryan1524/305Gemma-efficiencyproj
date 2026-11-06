# Setup and troubleshooting

Use Apple Silicon macOS 26 or later and Python 3.12. Install Ollama separately. Run
`./start.sh`; allow Python under Privacy & Security → Screen & System Audio
Recording, then quit and reopen. Download a model in setup. Voice is optional;
install Xcode Command Line Tools and run `./build_dictate.sh` to enable it.

If an existing `venv` uses another Python version, rename it and run `./start.sh`
again. Developer setup: `make dev`; checks: `make check`; bundles: `make build`.
The pinned environment is in requirements-lock.txt; requirements.txt lists direct
dependencies. Update and validate both together using Python 3.12 on macOS.

A safe sample report without tracking or model access:

```bash
./venv/bin/python report.py ledger.sample.jsonl --out /tmp/focusledger-demo.html --demo --offline
```

Run `make preflight` for local dependency/service/native checks. It uses temporary
ledgers, may call the local model, and does not launch tracking or request mic access.
A permission check is not proof that a live window capture works.

If setup stays blocked, check `ollama list`, `curl -s localhost:11434/api/tags`, and
Screen Recording permissions. If inference times out, select a smaller model.
Missing or untitled windows are skipped. Protected windows may not be readable.
An incomplete checkpoint is reported as a failure rather than a successful flush.
Idle-close and midnight failures retry with the original interval in memory.
A final-checkpoint failure stops the tracker and marks the report incomplete.
Unclassified in-memory samples do not survive exit.

`FL_DATA_DIR` selects an alternate data directory. `FL_LEDGER_PATH` selects a ledger
and disables automatic legacy migration. Explicit report positional paths override
that variable. `report.py --date YYYY-MM-DD` selects a day; default is the latest
recorded day. `FL_MODEL` overrides the model preference. `FL_DEV=1` shortens timing.

Optional launch at login: `./venv/bin/python notch.py --install-login`.
To uninstall, unload and remove `~/Library/LaunchAgents/com.focusledger.notch.plist`
if installed, remove the checkout, and remove FocusLedger's Application Support
folder if you want to erase history. Legacy ledger copies in the checkout remain
until you delete them. Remove the Python Screen Recording permission if no other
application needs it. Remove Ollama/models separately if desired.

## Native integration check

Run only against disposable paths with existing Screen Recording permission:

```bash
FL_DATA_DIR=/tmp/focusledger-smoke FL_LEDGER_PATH=/tmp/focusledger-smoke/ledger.jsonl ./venv/bin/python scripts/native_smoke.py --model gemma3:4b
```

It creates two synthetic windows, checks single-window capture and neighbor exclusion,
then optionally runs an installed local model and renders a report. Omitting `--model`
checks native capture/OCR only. It never starts general activity tracking or changes
permissions. The model must already be installed.
