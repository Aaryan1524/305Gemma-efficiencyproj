#!/bin/bash
# Build the speech-to-text helper.
#
# Produces two things from one binary:
#   dictate      — CLI, for running by hand from a shell
#   Dictate.app  — the same binary in a bundle, so the notch can launch it
#                  with `open`. That matters: TCC blames the parent process
#                  for a mic request, and a helper spawned by `python` gets
#                  killed (SIGABRT) because python has no usage description.
#                  `open` hands the launch to LaunchServices instead, so the
#                  helper becomes its own responsible process.
set -euo pipefail
cd "$(dirname "$0")"

echo "==> compiling dictate"
swiftc dictate.swift -o dictate \
  -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist \
  -Xlinker dictate-Info.plist
codesign --force --sign - dictate

echo "==> assembling Dictate.app"
APP="Dictate.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp dictate "$APP/Contents/MacOS/dictate"
cp dictate-Info.plist "$APP/Contents/Info.plist"
# The bundle needs to name its executable and stay out of the Dock.
/usr/libexec/PlistBuddy -c "Add :CFBundleExecutable string dictate" \
  "$APP/Contents/Info.plist" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :CFBundlePackageType string APPL" \
  "$APP/Contents/Info.plist" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" \
  "$APP/Contents/Info.plist" 2>/dev/null || true
codesign --force --deep --sign - "$APP"

echo "==> done: ./dictate and ./$APP"
