"""Opt-in native smoke: capture only our generated windows; optionally use local Gemma.

Run with FL_DATA_DIR and FL_LEDGER_PATH pointing to disposable locations.
Never runs as part of unit tests or launches general activity tracking.
"""
import argparse
from datetime import datetime, timedelta
import os
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import AppKit
from Foundation import NSDate, NSRunLoop
import Quartz

import focusledger as fl
import gemma
import report
from window_capture import capture_window
import vision_ocr


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', help='Optional installed local model for end-to-end inference')
    args = parser.parse_args()
    if not os.environ.get('FL_DATA_DIR') or not os.environ.get('FL_LEDGER_PATH'):
        parser.error('Set disposable FL_DATA_DIR and FL_LEDGER_PATH first')
    if not Quartz.CGPreflightScreenCaptureAccess():
        raise SystemExit('Screen Recording is not allowed. No permissions were changed.')
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    windows = []
    try:
        for title, text in [('FocusLedger synthetic target', 'FocusLedger synthetic focus check'),
                            ('FocusLedger synthetic neighbor', 'Banana neighboring sample')]:
            window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                AppKit.NSMakeRect(100, 100, 700, 200), AppKit.NSWindowStyleMaskTitled,
                AppKit.NSBackingStoreBuffered, False)
            window.setTitle_(title)
            label = AppKit.NSTextField.labelWithString_(text)
            label.setFont_(AppKit.NSFont.systemFontOfSize_(28))
            label.setFrame_(AppKit.NSMakeRect(20, 60, 650, 80))
            window.contentView().addSubview_(label)
            window.orderFrontRegardless()
            window.display()
            windows.append(window)
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.5))
        target = windows[0]
        image = capture_window(target.windowNumber())
        assert image is not None, 'No window image returned'
        text = vision_ocr.ocr_image(image)
        assert 'FocusLedger' in text, 'Target text missing'
        assert 'Banana' not in text, 'Neighbor content leaked into capture'
        assert capture_window(2147483647) is None, 'Missing window did not fail closed'
        fl.BUFFER.clear()
        with patch.object(fl, 'frontmost', return_value=('SyntheticEditor', target.title(), target.windowNumber())):
            fl.capture_tick(force=True)
        assert fl.BUFFER, 'Capture pipeline did not produce a sample'
        print('PASS: native window capture, OCR, neighbor exclusion, and missing-window skip')
        if args.model:
            os.environ['FL_MODEL'] = args.model
            now = datetime.now()
            fl.append_ledger({'type': 'goals', 'goals': 'Verify the FocusLedger synthetic capture pipeline'})
            fl.append_ledger({'type': 'session_open', 't': (now - timedelta(minutes=1)).strftime('%H:%M:%S')})
            assert fl.run_checkpoint('Verify the FocusLedger synthetic capture pipeline', now - timedelta(minutes=1), now)
            fl.append_ledger({'type': 'session_close', 't': now.strftime('%H:%M:%S'), 'duration': '1m'})
            data = report.build_report(fl.LEDGER_PATH)
            assert data['total_min'] >= 1 and data['sessions']
            assert 'unavailable' not in data['synthesis'].get('headline', '').lower(), 'Model synthesis failed'
            output = str(Path(os.environ['FL_DATA_DIR']) / 'native-smoke.html')
            report.render(data, output)
            print(f'PASS: {gemma.get_model()} checkpoint, ledger, synthesis, report: {output}')
    finally:
        fl.BUFFER.clear()
        for window in windows:
            window.orderOut_(None)


if __name__ == '__main__':
    main()
