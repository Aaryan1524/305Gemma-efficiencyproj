#!/bin/bash
# Builds the notch companion as a real .app bundle and registers it with
# Launch Services. Both steps are required for voice input to work:
#
# 1. py2app: Speech Recognition / Microphone access is gated by TCC on the
#    *bundle's* Info.plist declaring NSSpeechRecognitionUsageDescription /
#    NSMicrophoneUsageDescription. A bare `python notch.py` process can
#    never satisfy this — see SpeechManager in notch.py.
# 2. lsregister: a freshly built, never-before-seen bundle isn't known to
#    Launch Services yet, and TCC's authorization check crashes the process
#    (SIGABRT) rather than denying gracefully if the bundle isn't
#    registered. Registering it once fixes this for all future launches.
#
# Run this after cloning, and again any time you edit notch.py.
set -e
cd "$(dirname "${BASH_SOURCE[0]}")"

./venv/bin/pip install -q py2app
rm -rf build dist
./venv/bin/python setup.py py2app

/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/LaunchServices.framework/Versions/A/Support/lsregister -f dist/FocusLedger.app

echo
echo "Built dist/FocusLedger.app — launch it with:"
echo "  open dist/FocusLedger.app"
