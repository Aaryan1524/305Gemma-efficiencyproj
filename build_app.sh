#!/bin/bash
# Build FocusLedger.app — double-clickable macOS companion bundle.
#
# Wraps the notch companion in a signed .app bundle with proper macOS
# privacy usage descriptions (Screen Capture, Microphone, Speech Recognition)
# and LSUIElement=true so it lives cleanly in the MacBook notch.
set -euo pipefail
cd "$(dirname "$0")"
PROJECT_DIR="$(pwd)"

APP="FocusLedger.app"
echo "==> assembling $APP"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
mkdir -p "$APP/Contents/Resources"

cat << 'PLIST' > "$APP/Contents/Info.plist"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>
  <string>FocusLedger</string>
  <key>CFBundleDisplayName</key>
  <string>FocusLedger</string>
  <key>CFBundleIdentifier</key>
  <string>com.focusledger.app</string>
  <key>CFBundleVersion</key>
  <string>0.1.0</string>
  <key>CFBundleShortVersionString</key>
  <string>0.1.0</string>
  <key>CFBundleExecutable</key>
  <string>FocusLedger</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>LSUIElement</key>
  <true/>
  <key>NSHighResolutionCapable</key>
  <true/>
  <key>NSScreenCaptureUsageDescription</key>
  <string>FocusLedger reads the focused window with on-device Apple Vision OCR with screenshot pixels kept in memory.</string>
  <key>NSMicrophoneUsageDescription</key>
  <string>FocusLedger listens briefly when you click Speak goals to transcribe your daily goals on-device.</string>
  <key>NSSpeechRecognitionUsageDescription</key>
  <string>FocusLedger transcribes your spoken goals on-device using Apple Speech. Audio never leaves this Mac.</string>
</dict>
</plist>
PLIST

cat << 'LAUNCHER' > "$APP/Contents/MacOS/FocusLedger"
#!/bin/bash
# Resolve the repo from where this bundle lives, so moving the repo folder never breaks it.
export PATH="/opt/homebrew/bin:/usr/local/bin:/Applications/Ollama.app/Contents/Resources:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
PROJECT_DIR="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$PROJECT_DIR"

# Start the local Ollama daemon if it is installed and not yet listening.
if command -v ollama >/dev/null 2>&1; then
  if ! curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
    nohup ollama serve >/dev/null 2>&1 &
    sleep 1
  fi
fi

if [ ! -x "$PROJECT_DIR/venv/bin/python" ]; then
  osascript -e 'display alert "FocusLedger isn'"'"'t set up yet" message "Open Terminal in the FocusLedger folder and run ./start.sh once."' >/dev/null 2>&1
  exit 1
fi
exec "$PROJECT_DIR/venv/bin/python" "$PROJECT_DIR/notch.py" "$@"
LAUNCHER

chmod +x "$APP/Contents/MacOS/FocusLedger"
codesign --force --deep --sign - "$APP"
echo "==> done: ./$APP"
