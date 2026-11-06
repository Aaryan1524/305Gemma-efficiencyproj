"""In-memory, single-window capture using ScreenCaptureKit (macOS 14+)."""
import threading
import time

from Foundation import NSDate, NSRunLoop

import Quartz
import ScreenCaptureKit as SC


def _wait(event, timeout):
    # ScreenCaptureKit and AppKit need a running main loop in the tracker CLI too.
    if threading.current_thread() is not threading.main_thread():
        return event.wait(timeout)
    deadline = time.monotonic() + timeout
    while not event.is_set() and time.monotonic() < deadline:
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.05))
    return event.is_set()


def capture_window(window_id, timeout=10):
    """Return a CGImage or None. Denied, disappeared, or timed-out windows fail closed."""
    if not window_id or not Quartz.CGPreflightScreenCaptureAccess():
        return None
    ready = threading.Event()
    result = []

    def received_content(content, error):
        if content is not None and error is None:
            result.append(content)
        ready.set()

    SC.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
        True, True, received_content)
    if not _wait(ready, timeout) or not result:
        return None
    window = next((w for w in result[0].windows() if w.windowID() == window_id), None)
    if window is None:
        return None
    content_filter = SC.SCContentFilter.alloc().initWithDesktopIndependentWindow_(window)
    config = SC.SCStreamConfiguration.alloc().init()
    frame = window.frame()
    config.setWidth_(max(1, int(frame.size.width * 2)))
    config.setHeight_(max(1, int(frame.size.height * 2)))
    config.setShowsCursor_(False)
    config.setIgnoreShadowsSingleWindow_(True)
    image_ready = threading.Event()
    images = []

    def received_image(image, error):
        if image is not None and error is None:
            images.append(image)
        image_ready.set()

    SC.SCScreenshotManager.captureImageWithFilter_configuration_completionHandler_(
        content_filter, config, received_image)
    if not _wait(image_ready, timeout) or not images:
        return None
    return images[0]
