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
MUTED = AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.62, 1.0)
GREEN = AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.13, 0.63, 0.42, 1.0)


def _label(text, size, bold, color, frame):
    lb = AppKit.NSTextField.alloc().initWithFrame_(frame)
    lb.setStringValue_(text)
    lb.setBezeled_(False); lb.setDrawsBackground_(False)
    lb.setEditable_(False); lb.setSelectable_(False)
    weight = AppKit.NSFontWeightBold if bold else AppKit.NSFontWeightRegular
    lb.setFont_(AppKit.NSFont.systemFontOfSize_weight_(size, weight))
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

        # Status dot living on the right wing of the collapsed notch.
        self.dot = Quartz.CALayer.layer()
        self.dot.setBackgroundColor_(MUTED.CGColor())
        self.dot.setCornerRadius_(3.5)
        self.root.layer().addSublayer_(self.dot)

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
            w, h = 470, self.mb + 240
        else:
            # 14px chin below the menu-bar band: the visible, clickable lip.
            w, h = self.notch_w + 96, self.mb + 14
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
        tr, br = (10.0, 24.0) if self.expanded else (8.0, 11.0)
        new_path = notch_path(r.origin.x, r.origin.x + r.size.width,
                              FRAME_H, r.origin.y, tr, br)
        old_path = self.shape.path()
        self.shape.setPath_(new_path)
        if animate and old_path is not None:
            self.shape.addAnimation_forKey_(
                self._spring("path", old_path, new_path), "path")

        # Wing dot: visible only when collapsed.
        dr = self.shape_rect(False)
        dot_frame = AppKit.NSMakeRect(
            dr.origin.x + dr.size.width - 26, FRAME_H - self.mb / 2.0 - 3.5, 7, 7)
        Quartz.CATransaction.begin()
        Quartz.CATransaction.setDisableActions_(True)
        self.dot.setFrame_(dot_frame)
        Quartz.CATransaction.commit()
        self.dot.setOpacity_(0.0 if self.expanded else 1.0)

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
        top = H - self.mb - 6   # stay clear of the physical notch band
        pad = 26

        self.content.addSubview_(_label("Focus", 15, True, INK, AppKit.NSMakeRect(pad, top - 28, 60, 20)))
        self.content.addSubview_(_label("●", 11, False, GREEN, AppKit.NSMakeRect(pad + 43, top - 26, 14, 16)))
        self.content.addSubview_(_label("Ledger", 15, True, INK, AppKit.NSMakeRect(pad + 54, top - 28, 70, 20)))
        self.content.addSubview_(_label("100% local · zero network", 10, False, MUTED,
                                        AppKit.NSMakeRect(W - 172, top - 25, 150, 14)))

        if self.tracker is None and self.goals is None and not self.day_done:
            self.content.addSubview_(_label("What are your goals today?", 14, True, INK,
                                            AppKit.NSMakeRect(pad, top - 68, W - 2 * pad, 20)))
            self.field = AppKit.NSTextField.alloc().initWithFrame_(
                AppKit.NSMakeRect(pad, top - 104, W - 2 * pad, 26))
            self.field.setFont_(AppKit.NSFont.systemFontOfSize_(13))
            self.field.setPlaceholderString_("Ship the demo; study calc; keep Slack short")
            self.field.setTarget_(self); self.field.setAction_("startDay:")
            self.content.addSubview_(self.field)
            btn = self._button("Start day", "startDay:", AppKit.NSMakeRect(pad, top - 148, 110, 30))
            self.content.addSubview_(btn)
            self.panel.makeKeyAndOrderFront_(None)
            AppKit.NSApp.activateIgnoringOtherApps_(True)
            self.panel.makeFirstResponder_(self.field)
        elif self.tracker is not None:
            self.sessLabel = _label(self.status["session"], 13, True, GREEN,
                                    AppKit.NSMakeRect(pad, top - 62, W - 2 * pad, 18))
            self.lineLabel = _label(self.status["line"], 12, False, MUTED,
                                    AppKit.NSMakeRect(pad, top - 86, W - 2 * pad, 18))
            self.lineLabel.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
            self.content.addSubview_(self.sessLabel)
            self.content.addSubview_(self.lineLabel)
            goals = _label("Goals: " + (self.goals or ""), 11, False, MUTED,
                           AppKit.NSMakeRect(pad, top - 116, W - 2 * pad, 26))
            goals.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
            self.content.addSubview_(goals)
            self.content.addSubview_(self._button("View report", "viewReport:",
                                                  AppKit.NSMakeRect(pad, 18, 110, 30)))
            self.content.addSubview_(self._button("End my day", "endDay:",
                                                  AppKit.NSMakeRect(pad + 122, 18, 110, 30)))
        else:
            msg = "Day closed — report is open. 🎉" if self.day_done else "Not tracking."
            self.content.addSubview_(_label(msg, 13, True, INK,
                                            AppKit.NSMakeRect(pad, top - 66, W - 2 * pad, 20)))
            self.content.addSubview_(self._button("View report", "viewReport:",
                                                  AppKit.NSMakeRect(pad, 18, 110, 30)))
            q = self._button("Quit", None, AppKit.NSMakeRect(pad + 122, 18, 80, 30))
            q.setTarget_(AppKit.NSApp); q.setAction_("terminate:")
            self.content.addSubview_(q)

    @objc.python_method
    def _button(self, title, action, frame):
        btn = AppKit.NSButton.alloc().initWithFrame_(frame)
        btn.setTitle_(title)
        btn.setBezelStyle_(AppKit.NSBezelStyleRounded)
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
        for line in self.tracker.stdout:
            line = line.strip()
            if not line:
                continue
            if line.startswith(("▶", "■")):
                self.status["session"] = line
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
        if self.expanded and self.tracker is not None:
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
