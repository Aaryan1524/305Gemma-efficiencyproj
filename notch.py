"""Notch companion — FocusLedger's home on the Mac.

A borderless, always-on-top black panel that sits flush with the top-center
of the screen so it visually merges with the notch. Click to expand.

First open of the day it asks for your goals (the ledger remembers, so it
only asks once), then launches the tracker and shows live status. "End day"
flushes the last checkpoint and opens the WHOOP-style report.

Run:            python notch.py
Login launch:   python notch.py --install-login   (writes a LaunchAgent)
"""

import os
import signal
import subprocess
import sys
import threading
from datetime import date

import AppKit
import objc
from PyObjCTools import AppHelper

import focusledger as fl

BASE = os.path.dirname(os.path.abspath(__file__))
PYTHON = sys.executable

# Panel geometry (Cocoa: origin is bottom-left of the screen).
# The collapsed pill spans the menu-bar/notch band PLUS a small lip that hangs
# below it — on notched Macs anything drawn inside that band is physically
# invisible behind the notch, so all content lives in the lip / below the band.
COLLAPSED_W = 230
LIP = 20
EXPANDED_W, EXPANDED_H = 400, 250

BG = AppKit.NSColor.blackColor()
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


class NotchPanel(AppKit.NSPanel):
    """Borderless panels can't become key by default; the goals field needs it."""
    def canBecomeKeyWindow(self):
        return True


class App(AppKit.NSObject):
    def applicationDidFinishLaunching_(self, note):
        self.tracker = None
        self.status = {"line": "idle", "session": "no session"}
        self.expanded = False

        # Menu-bar band height (38 on notched Macs, 24 otherwise): everything in
        # it hides behind the notch, so content is laid out below it.
        screen = AppKit.NSScreen.screens()[0]
        f, v = screen.frame(), screen.visibleFrame()
        self.mb = int((f.origin.y + f.size.height) - (v.origin.y + v.size.height))
        self.collapsed_h = self.mb + LIP

        rect = AppKit.NSMakeRect(0, 0, COLLAPSED_W, self.collapsed_h)
        self.panel = NotchPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, AppKit.NSWindowStyleMaskBorderless, AppKit.NSBackingStoreBuffered, False)
        self.panel.setLevel_(AppKit.NSStatusWindowLevel)
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(AppKit.NSColor.clearColor())
        self.panel.setHidesOnDeactivate_(False)  # panels hide on deactivate by default
        self.panel.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorStationary)
        self.panel.setHasShadow_(True)

        self.root = AppKit.NSView.alloc().initWithFrame_(rect)
        self.root.setWantsLayer_(True)
        layer = self.root.layer()
        layer.setBackgroundColor_(BG.CGColor())
        layer.setCornerRadius_(14.0)
        # Round only the bottom corners so the top edge merges with the notch.
        layer.setMaskedCorners_(1 | 2)  # kCALayerMinXMinYCorner | kCALayerMaxXMinYCorner
        self.panel.setContentView_(self.root)

        click = AppKit.NSClickGestureRecognizer.alloc().initWithTarget_action_(
            self, "togglePanel:")
        self.root.addGestureRecognizer_(click)

        self._place(COLLAPSED_W, self.collapsed_h, animate=False)
        self.panel.orderFrontRegardless()

        # First open of the day → ask goals; otherwise straight to tracking.
        self.goals = fl.load_todays_goals()
        if self.goals:
            self.startTracker()
            self.setExpanded_(False)
        else:
            self.setExpanded_(True)

        AppKit.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0, self, "tick:", None, True)

    # ---------- geometry ----------

    @objc.python_method
    def _place(self, w, h, animate=True):
        screen = AppKit.NSScreen.screens()[0].frame()  # primary display, not key-focus one
        x = screen.origin.x + (screen.size.width - w) / 2.0
        y = screen.origin.y + screen.size.height - h
        frame = AppKit.NSMakeRect(x, y, w, h)
        self.panel.setFrame_display_animate_(frame, True, animate)
        self.root.setFrame_(AppKit.NSMakeRect(0, 0, w, h))

    def setExpanded_(self, on):
        self.expanded = bool(on)
        if self.expanded:
            self._place(EXPANDED_W, EXPANDED_H)
        else:
            self._place(COLLAPSED_W, self.collapsed_h)
        self.rebuild()

    def togglePanel_(self, sender):
        self.setExpanded_(not self.expanded)

    # ---------- UI states ----------

    def rebuild(self):
        for v in list(self.root.subviews()):
            v.removeFromSuperview()

        if not self.expanded:
            # Only the lip below the menu-bar band is physically visible.
            dot = _label("●", 9, False, GREEN if self.tracker else MUTED,
                         AppKit.NSMakeRect(48, 2, 14, 14))
            title = _label("FocusLedger", 11, True, INK,
                           AppKit.NSMakeRect(64, 1, 90, 16))
            hint = _label("▾", 11, False, MUTED,
                          AppKit.NSMakeRect(160, 1, 20, 16))
            for v in (dot, title, hint):
                self.root.addSubview_(v)
            return

        W, H = EXPANDED_W, EXPANDED_H
        top = H - self.mb  # content ceiling: everything above hides behind the notch
        self.root.addSubview_(_label("Focus", 15, True, INK, AppKit.NSMakeRect(20, top - 28, 60, 20)))
        self.root.addSubview_(_label("●", 11, False, GREEN, AppKit.NSMakeRect(63, top - 26, 14, 16)))
        self.root.addSubview_(_label("Ledger", 15, True, INK, AppKit.NSMakeRect(74, top - 28, 70, 20)))
        self.root.addSubview_(_label("100% local · zero network", 10, False, MUTED,
                                     AppKit.NSMakeRect(W - 170, top - 25, 160, 14)))

        if self.tracker is None and self.goals is None:
            self.root.addSubview_(_label("What are your goals today?", 14, True, INK,
                                         AppKit.NSMakeRect(20, top - 66, W - 40, 20)))
            self.field = AppKit.NSTextField.alloc().initWithFrame_(
                AppKit.NSMakeRect(20, top - 102, W - 40, 26))
            self.field.setFont_(AppKit.NSFont.systemFontOfSize_(13))
            self.field.setPlaceholderString_("Ship the demo; study calc; keep Slack short")
            self.field.setTarget_(self); self.field.setAction_("startDay:")
            self.root.addSubview_(self.field)
            btn = AppKit.NSButton.alloc().initWithFrame_(AppKit.NSMakeRect(20, top - 146, 110, 30))
            btn.setTitle_("Start day"); btn.setBezelStyle_(AppKit.NSBezelStyleRounded)
            btn.setTarget_(self); btn.setAction_("startDay:")
            self.root.addSubview_(btn)
            self.panel.makeKeyAndOrderFront_(None)
            AppKit.NSApp.activateIgnoringOtherApps_(True)
            self.panel.makeFirstResponder_(self.field)
        elif self.tracker is not None:
            self.sessLabel = _label(self.status["session"], 13, True, GREEN,
                                    AppKit.NSMakeRect(20, top - 62, W - 40, 18))
            self.lineLabel = _label(self.status["line"], 12, False, MUTED,
                                    AppKit.NSMakeRect(20, top - 86, W - 40, 18))
            self.lineLabel.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
            self.root.addSubview_(self.sessLabel)
            self.root.addSubview_(self.lineLabel)
            goals = _label("Goals: " + (self.goals or ""), 11, False, MUTED,
                           AppKit.NSMakeRect(20, top - 116, W - 40, 26))
            goals.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
            self.root.addSubview_(goals)
            btn = AppKit.NSButton.alloc().initWithFrame_(AppKit.NSMakeRect(20, 16, 120, 30))
            btn.setTitle_("End my day"); btn.setBezelStyle_(AppKit.NSBezelStyleRounded)
            btn.setTarget_(self); btn.setAction_("endDay:")
            self.root.addSubview_(btn)
        else:
            self.root.addSubview_(_label("Day closed — report is open. 🎉", 13, True, INK,
                                         AppKit.NSMakeRect(20, top - 70, W - 40, 20)))
            btn = AppKit.NSButton.alloc().initWithFrame_(AppKit.NSMakeRect(20, 16, 90, 30))
            btn.setTitle_("Quit"); btn.setBezelStyle_(AppKit.NSBezelStyleRounded)
            btn.setTarget_(AppKit.NSApp); btn.setAction_("terminate:")
            self.root.addSubview_(btn)

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

    def _reader(self):
        for line in self.tracker.stdout:
            line = line.strip()
            if not line:
                continue
            if line.startswith("▶"):
                self.status["session"] = line
            elif line.startswith("■"):
                self.status["session"] = line
            elif line.startswith(("👁", "📒", "🧠", "⛔")):
                self.status["line"] = line

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
        subprocess.run([PYTHON, os.path.join(BASE, "report.py"),
                        os.path.join(BASE, "ledger.jsonl"), "--open"], cwd=BASE)
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(self.rebuild)

    def tick_(self, timer):
        # Refresh the two live labels without rebuilding the whole view.
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
