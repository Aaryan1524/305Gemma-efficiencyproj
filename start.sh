#!/bin/bash
# FocusLedger — One-command bootstrap, preflight, and launcher.
#
# Usage:
#   ./start.sh               # Set up everything (if needed) and launch the Notch UI
#   ./start.sh --quick       # Launch in Quick Test mode (1-min checkpoints, 2s polling)
#   ./start.sh --onboarding  # Launch directly into the Notch First-Run Onboarding Wizard
#   ./start.sh --verify      # Run the 14-point automated preflight verification suite
#   ./start.sh --demo-report # Render and open the full sample daily report immediately
set -euo pipefail
cd "$(dirname "$0")"

GREEN="\033[32m"
YELLOW="\033[33m"
CYAN="\033[36m"
BOLD="\033[1m"
RESET="\033[0m"

echo -e "${BOLD}FocusLedger${RESET} · 100% Local On-Device Work Session Tracker"
echo "------------------------------------------------------------"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "FocusLedger requires macOS." >&2
  exit 1
fi
PYTHON_BIN="${FL_PYTHON:-python3.12}"
if [ ! -x "venv/bin/python" ]; then
  command -v "$PYTHON_BIN" >/dev/null || { echo "Install Python 3.12, then retry." >&2; exit 1; }
  "$PYTHON_BIN" -m venv venv
fi
./venv/bin/python -c 'import sys; assert sys.version_info[:2] == (3, 12), "Recreate venv with Python 3.12"'
./venv/bin/python -m pip install -q -r requirements-lock.txt
MODE="${1:-}"
if [ "$MODE" = "--verify" ] || [ "$MODE" = "--check" ]; then
  exec ./venv/bin/python verify_setup.py
elif [ "$MODE" = "--demo-report" ]; then
  OUT="$(./venv/bin/python -c 'import paths; print(paths.DATA_DIR / "report.demo.html")')"
  exec ./venv/bin/python report.py ledger.sample.jsonl --out "$OUT" --demo --offline --open
fi
./venv/bin/python -c 'import paths; paths.initialize()'

# 2. Build on-device voice helper (Dictate.app) if missing
if [ ! -x "Dictate.app/Contents/MacOS/dictate" ]; then
  echo -e "${CYAN}==> Building on-device Speech helper (Dictate.app)...${RESET}"
  ./build_dictate.sh || echo -e "${YELLOW}⚠ Voice goals unavailable (needs Xcode Command Line Tools: xcode-select --install). Typing goals still works.${RESET}"
fi

# 3. Build macOS app bundle (FocusLedger.app) if missing
if [ ! -x "FocusLedger.app/Contents/MacOS/FocusLedger" ]; then
  echo -e "${CYAN}==> Building macOS application bundle (FocusLedger.app)...${RESET}"
  ./build_app.sh || echo -e "${YELLOW}⚠ Couldn't build FocusLedger.app; ./start.sh still launches the notch.${RESET}"
fi

# 4. Make sure the local Ollama server is running. The model itself is downloaded
#    from inside the app (with a progress bar), so there is no 8 GB wait here.
export PATH="/Applications/Ollama.app/Contents/Resources:$PATH"
MODEL="$(./venv/bin/python -c 'import gemma; print(gemma.get_model())')"   # env > saved choice > RAM-based default
if command -v ollama >/dev/null 2>&1; then
  if ! curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
    echo -e "${CYAN}==> Starting local Ollama server...${RESET}"
    nohup ollama serve >/dev/null 2>&1 &
    sleep 2
  fi
  if ! ollama list 2>/dev/null | awk '{print $1}' | grep -qx "$MODEL"; then
    echo -e "${YELLOW}==> $MODEL isn't downloaded yet. The setup screen will offer a one-click download.${RESET}"
  fi
else
  echo -e "${YELLOW}⚠ Ollama isn't installed. The setup screen will link to https://ollama.com/download${RESET}"
fi

# 5. Handle CLI flags
MODE="${1:-}"
if [ "$MODE" = "--quick" ]; then
  echo -e "${GREEN}==> Launching FocusLedger Notch in ⚡ Quick Test Mode (1-min checkpoints)...${RESET}"
  echo "    Look at the top of your screen (under the notch)!"
  pkill -f "notch.py" 2>/dev/null || true
  FL_DEV=1 exec ./venv/bin/python notch.py
elif [ "$MODE" = "--onboarding" ]; then
  echo -e "${GREEN}==> Launching FocusLedger Notch with First-Run Onboarding Wizard...${RESET}"
  pkill -f "notch.py" 2>/dev/null || true
  FL_ONBOARDING=1 exec ./venv/bin/python notch.py --onboarding
else
  echo -e "${GREEN}==> Launching FocusLedger Notch Companion...${RESET}"
  echo "    Click the notch at the top of your screen to interact."
  echo "    (Tip: run './start.sh --quick' for 1-minute test checkpoints, or './start.sh --onboarding' to view setup wizard)"
  pkill -f "notch.py" 2>/dev/null || true
  exec ./venv/bin/python notch.py "$@"
fi
