"""py2app build script for the notch companion.

Only the notch companion needs a real .app bundle — Speech Recognition and
Microphone access are gated by TCC on the *bundle's* Info.plist declaring
usage-description strings, which a bare `python notch.py` invocation can
never satisfy (see SpeechManager in notch.py for the long explanation).
focusledger.py / report.py / seed.py stay plain scripts; nothing else here
needs bundling.

Build once with:  ./venv/bin/python setup.py py2app
Rebuild after editing notch.py the same way. `build/` and `dist/` are
gitignored — this is a local build artifact, not something to commit.
"""

from setuptools import setup

APP = ["notch.py"]
DATA_FILES = []
OPTIONS = {
    "argv_emulation": False,
    "iconfile": None,
    "plist": {
        "CFBundleName": "FocusLedger",
        "CFBundleIdentifier": "com.focusledger.notch",
        "CFBundleShortVersionString": "1.0",
        "LSUIElement": True,  # no Dock icon — matches the accessory-app behavior
        "NSSpeechRecognitionUsageDescription": (
            "FocusLedger uses on-device speech recognition so you can speak "
            "your goals each morning instead of typing them."
        ),
        "NSMicrophoneUsageDescription": (
            "FocusLedger listens only while you're using the mic button, to "
            "transcribe your stated goals for the day."
        ),
    },
    "packages": [],
}

setup(
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
