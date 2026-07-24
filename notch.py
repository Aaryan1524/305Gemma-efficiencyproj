"""Notch companion — FocusLedger's home on the Mac.

The panel is a single CAShapeLayer drawn in the classic dynamic-notch
silhouette: the top corners flare OUTWARD so the black surface flows into
the real notch with no visible seam, the bottom corners carry a deep
radius, and every state change is spring-animated (design inspired by the
open-source DynamicNotch project; implementation is original).

Collapsed, it hugs the physical notch with slim wings and a status dot.
Click it any time: first open of the day it asks your goals; afterwards it
shows the live session, lets you open the WHOOP report mid-day, or end the
day. "End my day" flushes the final checkpoint and opens the report.

Run:            python notch.py
Login launch:   python notch.py --install-login   (writes a LaunchAgent)
"""

import math
import os
import signal
import subprocess
import sys
import threading
from datetime import date

import AppKit
import objc
import Quartz
from PyObjCTools import AppHelper

import focusledger as fl

BASE = os.path.dirname(os.path.abspath(__file__))
PYTHON = sys.executable

# The window itself is a fixed, transparent stage; only the black shape
# inside it animates. That is what makes the motion buttery — we never
# resize the window, we spring the layer.
FRAME_W, FRAME_H = 760, 420

# Spring tuned to the "balanced" dynamic-notch feel: response ~0.47s,
# damping fraction 0.77.
SPRING_STIFFNESS = (2 * math.pi / 0.47) ** 2   # ≈ 179
SPRING_DAMPING = 2 * 0.77 * math.sqrt(SPRING_STIFFNESS)  # ≈ 21
SPRING_DURATION = 0.8

INK = AppKit.NSColor.whiteColor()
MUTED = AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.60, 1.0)
FAINT = AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.38, 1.0)
GREEN = AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.19, 0.78, 0.51, 1.0)
PILL_GREY = AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.16, 1.0)
PILL_GREEN = AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.11, 0.52, 0.34, 1.0)
FIELD_BG = AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.11, 1.0)

_WEIGHTS = {
    "regular": AppKit.NSFontWeightRegular,
    "medium": AppKit.NSFontWeightMedium,
    "semibold": AppKit.NSFontWeightSemibold,
    "bold": AppKit.NSFontWeightBold,
}


def _font(size, weight="regular", rounded=False, mono=False):
    """System SF Pro; `rounded` swaps in SF Rounded, `mono` uses tabular digits
    (the live-timer look from the reference gallery)."""
    w = _WEIGHTS[weight]
    f = (AppKit.NSFont.monospacedDigitSystemFontOfSize_weight_(size, w)
         if mono else AppKit.NSFont.systemFontOfSize_weight_(size, w))
    if rounded:
        try:
            d = f.fontDescriptor().fontDescriptorWithDesign_(
                AppKit.NSFontDescriptorSystemDesignRounded)
            rf = AppKit.NSFont.fontWithDescriptor_size_(d, size)
            if rf is not None:
                f = rf
        except AttributeError:
            pass
    return f


def _label(text, frame, font, color):
    lb = AppKit.NSTextField.alloc().initWithFrame_(frame)
    lb.setStringValue_(text)
    lb.setBezeled_(False); lb.setDrawsBackground_(False)
    lb.setEditable_(False); lb.setSelectable_(False)
    lb.setFont_(font)
    lb.setTextColor_(color)
    return lb


def notch_path(x0, x1, ytop, ybot, tr, br):
    """The notch silhouette (bottom-left coords): top corners flare outward
    (concave, so the shape melts into the menu bar), bottom corners are
    convex with a deep radius."""
    p = Quartz.CGPathCreateMutable()
    Quartz.CGPathMoveToPoint(p, None, x0, ytop)
    Quartz.CGPathAddQuadCurveToPoint(p, None, x0 + tr, ytop, x0 + tr, ytop - tr)
    Quartz.CGPathAddLineToPoint(p, None, x0 + tr, ybot + br)
    Quartz.CGPathAddQuadCurveToPoint(p, None, x0 + tr, ybot, x0 + tr + br, ybot)
    Quartz.CGPathAddLineToPoint(p, None, x1 - tr - br, ybot)
    Quartz.CGPathAddQuadCurveToPoint(p, None, x1 - tr, ybot, x1 - tr, ybot + br)
    Quartz.CGPathAddLineToPoint(p, None, x1 - tr, ytop - tr)
    Quartz.CGPathAddQuadCurveToPoint(p, None, x1, ytop, x1, ytop)
    Quartz.CGPathCloseSubpath(p)
    return p


class NotchPanel(AppKit.NSPanel):
    def canBecomeKeyWindow(self):
        return True


class RootView(AppKit.NSView):
    """Transparent stage; only the current shape rect accepts clicks so the
    rest of the window never steals mouse events from apps beneath it."""
    def initWithFrame_(self, frame):
        self = objc.super(RootView, self).initWithFrame_(frame)
        if self is not None:
            self.hotRect = AppKit.NSMakeRect(0, 0, 0, 0)
            self.onClick = None
        return self

    def acceptsFirstMouse_(self, event):
        return True  # respond to the first click even when the app is inactive

    def hitTest_(self, point):
        p = self.convertPoint_fromView_(point, None) if self.superview() is None else point
        if AppKit.NSPointInRect(p, self.hotRect):
            return objc.super(RootView, self).hitTest_(point)
        return None

    def mouseDown_(self, event):
        if self.onClick is not None:
            self.onClick()


class App(AppKit.NSObject):
    def applicationDidFinishLaunching_(self, note):
        self.tracker = None
        self.status = {"line": "idle", "session": "no session"}
        self.expanded = False
        self.day_done = False

        screen = AppKit.NSScreen.screens()[0]
        f, v = screen.frame(), screen.visibleFrame()
        self.mb = int((f.origin.y + f.size.height) - (v.origin.y + v.size.height))

        # Real notch width when the API is available; sane fallback otherwise.
        self.notch_w = 200
        try:
            tl, tr = screen.auxiliaryTopLeftArea(), screen.auxiliaryTopRightArea()
            if tl is not None and tr is not None:
                self.notch_w = int(f.size.width - tl.size.width - tr.size.width)
        except AttributeError:
            pass

        rect = AppKit.NSMakeRect(0, 0, FRAME_W, FRAME_H)
        style = (AppKit.NSWindowStyleMaskBorderless
                 | AppKit.NSWindowStyleMaskNonactivatingPanel)  # clicks land without app activation
        self.panel = NotchPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, AppKit.NSBackingStoreBuffered, False)
        self.panel.setLevel_(AppKit.NSPopUpMenuWindowLevel)  # reliably above the menu bar
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(AppKit.NSColor.clearColor())
        self.panel.setHasShadow_(False)  # the shape layer carries its own shadow
        self.panel.setHidesOnDeactivate_(False)
        self.panel.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorStationary)

        x = f.origin.x + (f.size.width - FRAME_W) / 2.0
        y = f.origin.y + f.size.height - FRAME_H
        self.panel.setFrame_display_(AppKit.NSMakeRect(x, y, FRAME_W, FRAME_H), False)

        self.root = RootView.alloc().initWithFrame_(rect)
        self.root.setWantsLayer_(True)
        self.panel.setContentView_(self.root)

        self.shape = Quartz.CAShapeLayer.layer()
        self.shape.setFillColor_(AppKit.NSColor.blackColor().CGColor())
        self.shape.setShadowColor_(AppKit.NSColor.blackColor().CGColor())
        self.shape.setShadowOpacity_(0.55)
        self.shape.setShadowRadius_(14.0)
        self.shape.setShadowOffset_(AppKit.NSMakeSize(0, -5))
        self.root.layer().addSublayer_(self.shape)

        # Live-activity wings: status dot on the left, session timer on the
        # right — the reference gallery's "Charging … 67%" layout.
        self.dot = Quartz.CALayer.layer()
        self.dot.setBackgroundColor_(MUTED.CGColor())
        self.dot.setCornerRadius_(3.5)
        self.root.layer().addSublayer_(self.dot)

        self.timeLabel = _label("", AppKit.NSMakeRect(0, 0, 10, 10),
                                _font(11.5, "semibold", rounded=True, mono=True), GREEN)
        self.timeLabel.setAlignment_(AppKit.NSTextAlignmentRight)
        self.root.addSubview_(self.timeLabel)
        self.session_start = None

        # Content sits inside the expanded shape, below the menu-bar band
        # (anything inside the band hides behind the physical notch).
        er = self.shape_rect(True)
        self.content = AppKit.NSView.alloc().initWithFrame_(er)
        self.content.setWantsLayer_(True)
        self.content.setAlphaValue_(0.0)
        self.root.addSubview_(self.content)

        # Clicking the notch expands it; clicking the expanded body does nothing
        # (buttons handle themselves); clicking anywhere OUTSIDE collapses —
        # the global monitor only sees events destined for other apps.
        self.root.onClick = self._notchClicked
        self.globalMonitor = AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            AppKit.NSEventMaskLeftMouseDown, self._outsideClick)

        self.apply_state(animate=False)
        self.panel.orderFrontRegardless()

        self.goals = fl.load_todays_goals()
        if self.goals:
            self.startTracker()
        else:
            self.setExpanded_(True)

        AppKit.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0, self, "tick:", None, True)

    # ---------- geometry & animation ----------

    @objc.python_method
    def shape_rect(self, expanded):
        """Shape bounds in window coords (bottom-left origin, top edge = FRAME_H)."""
        if expanded:
            w, h = 440, self.mb + 220
        else:
            # Live-activity bar: wings wide enough for the dot and the session
            # timer, a slim chin below the menu-bar band for clicks.
            w, h = self.notch_w + 150, self.mb + 12
        x0 = (FRAME_W - w) / 2.0
        return AppKit.NSMakeRect(x0, FRAME_H - h, w, h)

    @objc.python_method
    def _spring(self, key, from_v, to_v):
        a = Quartz.CASpringAnimation.animationWithKeyPath_(key)
        a.setMass_(1.0)
        a.setStiffness_(SPRING_STIFFNESS)
        a.setDamping_(SPRING_DAMPING)
        a.setDuration_(SPRING_DURATION)
        a.setFromValue_(from_v)
        a.setToValue_(to_v)
        return a

    @objc.python_method
    def apply_state(self, animate=True):
        r = self.shape_rect(self.expanded)
        tr, br = (12.0, 26.0) if self.expanded else (9.0, 12.0)
        new_path = notch_path(r.origin.x, r.origin.x + r.size.width,
                              FRAME_H, r.origin.y, tr, br)
        old_path = self.shape.path()
        self.shape.setPath_(new_path)
        if animate and old_path is not None:
            self.shape.addAnimation_forKey_(
                self._spring("path", old_path, new_path), "path")

        # Wing content: dot on the left wing, timer on the right — collapsed only.
        dr = self.shape_rect(False)
        wing = (dr.size.width - self.notch_w) / 2.0
        band_mid = FRAME_H - self.mb / 2.0
        dot_frame = AppKit.NSMakeRect(dr.origin.x + wing / 2.0 - 3.5, band_mid - 3.5, 7, 7)
        Quartz.CATransaction.begin()
        Quartz.CATransaction.setDisableActions_(True)
        self.dot.setFrame_(dot_frame)
        Quartz.CATransaction.commit()
        self.dot.setOpacity_(0.0 if self.expanded else 1.0)

        notch_right = dr.origin.x + dr.size.width - wing
        self.timeLabel.setFrame_(AppKit.NSMakeRect(
            notch_right + 4, band_mid - 8, wing - 20, 16))
        self.timeLabel.setAlphaValue_(0.0 if self.expanded else 1.0)

        self.root.hotRect = r
        self.content.setFrame_(self.shape_rect(True))
        if self.expanded:
            self.rebuild()
            if animate:
                self.content.setAlphaValue_(0.0)
                AppKit.NSAnimationContext.runAnimationGroup_completionHandler_(
                    lambda ctx: (ctx.setDuration_(0.32),
                                 self.content.animator().setAlphaValue_(1.0)), None)
            else:
                self.content.setAlphaValue_(1.0)
        else:
            self.content.setAlphaValue_(0.0)

    def setExpanded_(self, on):
        self.expanded = bool(on)
        self.apply_state(animate=True)

    @objc.python_method
    def _notchClicked(self):
        if not self.expanded:
            self.setExpanded_(True)

    @objc.python_method
    def _outsideClick(self, event):
        if self.expanded:
            AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(
                lambda: self.setExpanded_(False))

    # ---------- content states ----------

    def rebuild(self):
        for v in list(self.content.subviews()):
            v.removeFromSuperview()

        H = self.content.frame().size.height
        W = self.content.frame().size.width
        top = H - self.mb - 10   # stay clear of the physical notch band
        pad = 24

        # Header row: green dot glyph + semibold wordmark, faint tagline right.
        self.content.addSubview_(_label("●", AppKit.NSMakeRect(pad, top - 24, 14, 16),
                                        _font(10, "regular"), GREEN))
        self.content.addSubview_(_label("FocusLedger", AppKit.NSMakeRect(pad + 17, top - 26, 120, 18),
                                        _font(14, "semibold", rounded=True), INK))
        tag = _label("100% local", AppKit.NSMakeRect(W - pad - 90, top - 24, 90, 14),
                     _font(10, "medium"), FAINT)
        tag.setAlignment_(AppKit.NSTextAlignmentRight)
        self.content.addSubview_(tag)

        if self.tracker is None and self.goals is None and not self.day_done:
            self.content.addSubview_(_label("What are your goals today?",
                                            AppKit.NSMakeRect(pad, top - 58, W - 2 * pad, 18),
                                            _font(13, "semibold"), INK))
            self.content.addSubview_(_label("One line. Specific beats noble.",
                                            AppKit.NSMakeRect(pad, top - 76, W - 2 * pad, 14),
                                            _font(11, "regular"), MUTED))
            self.field = AppKit.NSTextField.alloc().initWithFrame_(
                AppKit.NSMakeRect(pad, top - 112, W - 2 * pad, 28))
            self.field.setFont_(_font(12.5, "regular"))
            self.field.setTextColor_(INK)
            self.field.setBezeled_(False)
            self.field.setWantsLayer_(True)
            self.field.layer().setBackgroundColor_(FIELD_BG.CGColor())
            self.field.layer().setCornerRadius_(8.0)
            self.field.setFocusRingType_(AppKit.NSFocusRingTypeNone)
            self.field.setBackgroundColor_(FIELD_BG)
            self.field.setDrawsBackground_(True)
            self.field.setPlaceholderString_("Ship the demo · study calc · keep Slack short")
            self.field.setTarget_(self); self.field.setAction_("startDay:")
            self.content.addSubview_(self.field)
            self.content.addSubview_(self._pill("Start day", "startDay:", PILL_GREEN,
                                                AppKit.NSMakeRect(W - pad - 96, 18, 96, 28)))
            self.panel.makeKeyAndOrderFront_(None)
            AppKit.NSApp.activateIgnoringOtherApps_(True)
            self.panel.makeFirstResponder_(self.field)
        elif self.tracker is not None:
            self.sessLabel = _label(self.status["session"],
                                    AppKit.NSMakeRect(pad, top - 58, W - 2 * pad, 18),
                                    _font(12.5, "semibold", mono=True), GREEN)
            self.lineLabel = _label(self.status["line"],
                                    AppKit.NSMakeRect(pad, top - 78, W - 2 * pad, 15),
                                    _font(11, "regular"), MUTED)
            self.lineLabel.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
            self.content.addSubview_(self.sessLabel)
            self.content.addSubview_(self.lineLabel)
            goals = _label("Goals · " + (self.goals or ""),
                           AppKit.NSMakeRect(pad, top - 100, W - 2 * pad, 15),
                           _font(11, "regular"), FAINT)
            goals.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
            self.content.addSubview_(goals)
            self.content.addSubview_(self._pill("View report", "viewReport:", PILL_GREY,
                                                AppKit.NSMakeRect(W - pad - 208, 18, 100, 28)))
            self.content.addSubview_(self._pill("End my day", "endDay:", PILL_GREEN,
                                                AppKit.NSMakeRect(W - pad - 100, 18, 100, 28)))
        else:
            msg = "Day closed — report is open." if self.day_done else "Not tracking."
            self.content.addSubview_(_label(msg, AppKit.NSMakeRect(pad, top - 58, W - 2 * pad, 18),
                                            _font(13, "semibold"), INK))
            self.content.addSubview_(self._pill("View report", "viewReport:", PILL_GREY,
                                                AppKit.NSMakeRect(W - pad - 208, 18, 100, 28)))
            q = self._pill("Quit", None, PILL_GREY,
                           AppKit.NSMakeRect(W - pad - 100, 18, 100, 28))
            q.setTarget_(AppKit.NSApp); q.setAction_("terminate:")
            self.content.addSubview_(q)

    @objc.python_method
    def _pill(self, title, action, bg, frame):
        """Rounded pill button, reference-gallery style: borderless, layer-
        backed capsule with a semibold white label."""
        btn = AppKit.NSButton.alloc().initWithFrame_(frame)
        btn.setBordered_(False)
        btn.setWantsLayer_(True)
        btn.layer().setBackgroundColor_(bg.CGColor())
        btn.layer().setCornerRadius_(frame.size.height / 2.0)
        attrs = {
            AppKit.NSFontAttributeName: _font(12, "semibold"),
            AppKit.NSForegroundColorAttributeName: INK,
        }
        btn.setAttributedTitle_(
            AppKit.NSAttributedString.alloc().initWithString_attributes_(title, attrs))
        if action:
            btn.setTarget_(self); btn.setAction_(action)
        return btn

    # ---------- actions ----------

    def startDay_(self, sender):
        goals = self.field.stringValue().strip()
        if not goals:
            return
        self.goals = goals
        fl.append_ledger({"type": "goals", "date": date.today().isoformat(), "goals": goals})
        self.startTracker()
        self.setExpanded_(False)

    def startTracker(self):
        self.tracker = subprocess.Popen(
            [PYTHON, "-u", os.path.join(BASE, "focusledger.py")],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=BASE)
        threading.Thread(target=self._reader, daemon=True).start()
        self.status["session"] = "tracking"
        self.dot.setBackgroundColor_(GREEN.CGColor())

    def _reader(self):
        from datetime import datetime
        for line in self.tracker.stdout:
            line = line.strip()
            if not line:
                continue
            if line.startswith("▶"):
                self.status["session"] = line
                self.session_start = datetime.now()
            elif line.startswith("■"):
                self.status["session"] = line
                self.session_start = None
            elif line.startswith(("👁", "📒", "🧠", "⛔")):
                self.status["line"] = line

    def viewReport_(self, sender):
        """Render the report from today's ledger — any time of day."""
        ledger = os.path.join(BASE, "ledger.jsonl")
        if not os.path.exists(ledger):
            self.status["line"] = "no ledger yet — start your day first"
            self.rebuild()
            return
        self.status["line"] = "🧠 generating report with Gemma…"
        self.rebuild()
        threading.Thread(target=self._renderReport, daemon=True).start()

    def _renderReport(self):
        subprocess.run([PYTHON, os.path.join(BASE, "report.py"),
                        os.path.join(BASE, "ledger.jsonl"), "--open"], cwd=BASE)
        self.status["line"] = "report opened in your browser"
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(self.rebuild)

    def endDay_(self, sender):
        self.status["session"] = "closing day…"
        self.status["line"] = "flushing final checkpoint (Gemma is judging)"
        self.rebuild()
        threading.Thread(target=self._closeAndReport, daemon=True).start()

    def _closeAndReport(self):
        proc, self.tracker = self.tracker, None
        if proc is not None:
            try:
                proc.send_signal(signal.SIGINT)
                proc.wait(timeout=180)
            except Exception:
                proc.kill()
        self.day_done = True
        self.dot.setBackgroundColor_(MUTED.CGColor())
        subprocess.run([PYTHON, os.path.join(BASE, "report.py"),
                        os.path.join(BASE, "ledger.jsonl"), "--open"], cwd=BASE)
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(self.rebuild)

    def tick_(self, timer):
        from datetime import datetime
        if not self.expanded:
            if self.session_start is not None:
                mins = int((datetime.now() - self.session_start).total_seconds() // 60)
                self.timeLabel.setStringValue_(f"{mins // 60}:{mins % 60:02d}")
                self.timeLabel.setTextColor_(GREEN)
            else:
                self.timeLabel.setStringValue_("–" if self.tracker else "")
                self.timeLabel.setTextColor_(FAINT)
        elif self.tracker is not None:
            if getattr(self, "sessLabel", None):
                self.sessLabel.setStringValue_(self.status["session"])
            if getattr(self, "lineLabel", None):
                self.lineLabel.setStringValue_(self.status["line"])


PLIST = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.focusledger.notch</string>
  <key>ProgramArguments</key>
  <array><string>{PYTHON}</string><string>{os.path.join(BASE, "notch.py")}</string></array>
  <key>RunAtLoad</key><true/>
  <key>WorkingDirectory</key><string>{BASE}</string>
</dict></plist>
"""


def install_login():
    path = os.path.expanduser("~/Library/LaunchAgents/com.focusledger.notch.plist")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(PLIST)
    subprocess.run(["launchctl", "load", path])
    print(f"Installed LaunchAgent → {path}")
    print("FocusLedger's notch companion will greet you at every login.")


def main():
    if "--install-login" in sys.argv:
        install_login()
        return
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)  # no Dock icon
    delegate = App.alloc().init()
    app.setDelegate_(delegate)
    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
