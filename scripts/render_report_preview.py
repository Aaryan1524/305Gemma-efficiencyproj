"""Render generated HTML in an isolated, nonpersistent WebKit test view.

Optional developer dependency: pyobjc-framework-WebKit==12.2.2.
This renders its own content; it does not access browsers or capture the desktop.
"""
import argparse
from pathlib import Path
import threading
import time

import AppKit
from Foundation import NSDate, NSRunLoop, NSURL
import WebKit


def wait(event, seconds=20):
    deadline = time.monotonic() + seconds
    while not event.is_set() and time.monotonic() < deadline:
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.05))
    if not event.is_set():
        raise RuntimeError('WebKit test view timed out')


def evaluate(view, code):
    done = threading.Event()
    values = []
    def complete(value, error):
        values.append((value, error))
        done.set()
    view.evaluateJavaScript_completionHandler_(code, complete)
    wait(done)
    value, error = values[0]
    if error:
        raise RuntimeError('WebKit could not evaluate the test expression')
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('html')
    parser.add_argument('png')
    parser.add_argument('--width', type=int, default=900)
    parser.add_argument('--dark', action='store_true')
    args = parser.parse_args()
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    config = WebKit.WKWebViewConfiguration.alloc().init()
    config.setWebsiteDataStore_(WebKit.WKWebsiteDataStore.nonPersistentDataStore())
    view = WebKit.WKWebView.alloc().initWithFrame_configuration_(AppKit.NSMakeRect(0, 0, args.width, 900), config)
    view.setAppearance_(AppKit.NSAppearance.appearanceNamed_(AppKit.NSAppearanceNameDarkAqua if args.dark else AppKit.NSAppearanceNameAqua))
    window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(AppKit.NSMakeRect(0, 0, args.width, 900), AppKit.NSWindowStyleMaskBorderless, AppKit.NSBackingStoreBuffered, False)
    window.setContentView_(view)
    window.orderFrontRegardless()
    try:
        html = Path(args.html).read_text()
        view.loadHTMLString_baseURL_(html, NSURL.fileURLWithPath_(str(Path(args.html).resolve())))
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.1))
            if not view.isLoading() and view.estimatedProgress() == 1:
                if evaluate(view, 'document.fonts.status === "loaded"'):
                    break
        else:
            raise RuntimeError('HTML/font rendering timed out')
        assert not evaluate(view, 'document.documentElement.scrollWidth > window.innerWidth'), 'Horizontal overflow'
        assert evaluate(view, 'getComputedStyle(document.querySelector(".score-value")).fontFamily.includes("Instrument Serif")')
        height = int(evaluate(view, 'document.documentElement.scrollHeight'))
        view.setFrameSize_(AppKit.NSMakeSize(args.width, height))
        window.setContentSize_(AppKit.NSMakeSize(args.width, height))
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.3))
        shot = WebKit.WKSnapshotConfiguration.alloc().init()
        shot.setRect_(AppKit.NSMakeRect(0, 0, args.width, height))
        done = threading.Event()
        images = []
        def complete(image, error):
            if image is not None and error is None:
                images.append(image)
            done.set()
        view.takeSnapshotWithConfiguration_completionHandler_(shot, complete)
        wait(done)
        assert images, 'No rendered image returned'
        bitmap = AppKit.NSBitmapImageRep.imageRepWithData_(images[0].TIFFRepresentation())
        data = bitmap.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, None)
        assert data.writeToFile_atomically_(str(Path(args.png).resolve()), True)
        print(f'Rendered {args.width}px report; no horizontal overflow; fonts loaded; {height}px tall')
    finally:
        window.orderOut_(None)


if __name__ == '__main__':
    main()
