"""Notch companion — FocusLedger's home on the Mac.

The panel is a single CAShapeLayer drawn in the classic dynamic-notch
silhouette: the top corners flare OUTWARD so the black surface flows into
the real notch with no visible seam, the bottom corners carry a deep
radius, and every state change is spring-animated (design inspired by the
open-source DynamicNotch project; implementation is original).

Collapsed, it hugs the physical notch with slim wings and a status dot.
Click it any time:
- On first launch (or via "⚙ Setup"), a native Onboarding Wizard verifies
  Ollama + Gemma, Screen Recording permission, and Voice Dictation.
- Each morning it asks your goals (with optional ⚡ Quick 1m test mode);
- Afterwards it shows live session status, flushes live checkpoints on
  "View report", generates full hybrid demo reports on "Demo day", or lets
  you reset with "New day" / close out with "End my day".

Run:            python notch.py
Onboarding:     python notch.py --onboarding
Quick test:     FL_DEV=1 python notch.py
Login launch:   python notch.py --install-login   (writes a LaunchAgent)
"""

import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from datetime import date

import AppKit
import mss
import objc
import Quartz
from PyObjCTools import AppHelper

import paths
import focusledger as fl
import gemma
from typography import register_native_fonts

BASE = os.path.dirname(os.path.abspath(__file__))
PYTHON = sys.executable
DICTATE_APP = os.path.join(BASE, "Dictate.app")   # Swift speech-to-text helper
DICTATE_EXE = "Dictate.app/Contents/MacOS/dictate"
ONBOARDING_FLAG = str(paths.DATA_DIR / ".onboarding_done")
MODEL_NOTES = {"gemma3:1b": "Fastest, but less accurate"}
IDLE_STATUS = {"session": "Tracking today", "line": "Waiting for you to get started"}
SAMPLE_GOALS = "Ship the onboarding; study calculus integrals; keep Slack under 20 minutes"

# The window itself is a fixed, transparent stage; only the black shape
# inside it animates. That is what makes the motion buttery — we never
# resize the window, we spring the layer.
FRAME_W, FRAME_H = 840, 400

# Expanded content height per screen (below the menu-bar band).
VIEW_HEIGHTS = {"setup": 296, "goals": 170, "tracking": 148, "done": 148}

# Spring tuned to the "balanced" dynamic-notch feel: response ~0.47s,
# damping fraction 0.77.
SPRING_STIFFNESS = (2 * math.pi / 0.47) ** 2   # ≈ 179
SPRING_DAMPING = 2 * 0.77 * math.sqrt(SPRING_STIFFNESS)  # ≈ 21
SPRING_DURATION = 0.8

# Warm ink, paper, and restrained botanical accents; the outer notch stays black.
def _white(a):
    return AppKit.NSColor.colorWithCalibratedWhite_alpha_(1.0, a)


def _rgb(r, g, b):
    return AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(r / 255, g / 255, b / 255, 1)


INK = _rgb(238, 233, 223)
MUTED = _rgb(176, 177, 165)
FAINT = _rgb(148, 151, 137)
FILL = _rgb(23, 25, 20)
FILL_HOVER = _rgb(39, 42, 34)
SURFACE = _rgb(15, 17, 13)
BORDER = _rgb(47, 50, 41)
PRIMARY = _rgb(232, 228, 216)
PRIMARY_HOVER = _rgb(209, 213, 192)
ON_PRIMARY = _rgb(30, 33, 25)
GREEN = _rgb(179, 194, 161)
ORANGE = _rgb(196, 161, 126)
RED = _rgb(211, 153, 136)

_WEIGHTS = {
    "regular": AppKit.NSFontWeightRegular,
    "medium": AppKit.NSFontWeightMedium,
    "semibold": AppKit.NSFontWeightSemibold,
    "bold": AppKit.NSFontWeightBold,
}


def _font(size, weight="regular", rounded=False, mono=False):
    """Geist for controls and tabular numbers, with a native fallback."""
    face = {"regular": "Regular", "medium": "Medium", "semibold": "SemiBold", "bold": "Bold"}[weight]
    font = AppKit.NSFont.fontWithName_size_("Geist-" + face, size)
    if font is not None:
        descriptor = font.fontDescriptor().fontDescriptorByAddingAttributes_({
            AppKit.NSFontTraitsAttribute: {AppKit.NSFontWeightTrait: _WEIGHTS[weight]},
            AppKit.NSFontFeatureSettingsAttribute: [{
                AppKit.NSFontFeatureTypeIdentifierKey: 6,
                AppKit.NSFontFeatureSelectorIdentifierKey: 0,
            }] if mono else [],
        })
        return AppKit.NSFont.fontWithDescriptor_size_(descriptor, size) or font
    return AppKit.NSFont.systemFontOfSize_weight_(size, _WEIGHTS[weight])


def _serif(size):
    return (AppKit.NSFont.fontWithName_size_("InstrumentSerif-Regular", size)
            or AppKit.NSFont.fontWithName_size_("Georgia", size)
            or _font(size))


def _symbol(name, size=13, weight="medium"):
    """SF Symbol image, or None when the symbol is unavailable (older macOS)."""
    img = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
    if img is None:
        return None
    cfg = AppKit.NSImageSymbolConfiguration.configurationWithPointSize_weight_(size, _WEIGHTS[weight])
    return img.imageWithSymbolConfiguration_(cfg)


def _label(text, frame, font, color):
    lb = AppKit.NSTextField.alloc().initWithFrame_(frame)
    lb.setStringValue_(text)
    lb.setBezeled_(False); lb.setDrawsBackground_(False)
    lb.setEditable_(False); lb.setSelectable_(False)
    lb.setFont_(font)
    lb.setTextColor_(color)
    return lb


class PillButton(AppKit.NSButton):
    """Capsule button with hover and press feedback (borderless buttons give none)."""

    def initWithFrame_(self, frame):
        self = objc.super(PillButton, self).initWithFrame_(frame)
        if self is not None:
            self.base_bg = None
            self.hover_bg = None
            self.addTrackingArea_(AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
                AppKit.NSZeroRect,
                AppKit.NSTrackingMouseEnteredAndExited | AppKit.NSTrackingActiveAlways
                | AppKit.NSTrackingInVisibleRect, self, None))
        return self

    @objc.python_method
    def _paint(self, color):
        if color is not None:
            self.layer().setBackgroundColor_(color.CGColor())

    def mouseEntered_(self, event):
        self._paint(self.hover_bg)

    def mouseExited_(self, event):
        self._paint(self.base_bg)

    def mouseDown_(self, event):
        self.layer().setOpacity_(0.55)
        objc.super(PillButton, self).mouseDown_(event)   # tracks until mouse-up
        self.layer().setOpacity_(1.0)

    def acceptsFirstMouse_(self, event):
        return True


def ollama_installed():
    return bool(shutil.which("ollama")) or os.path.isdir("/Applications/Ollama.app")


def check_system_readiness():
    """Fast readiness check for the setup screen."""
    ai = gemma.check_ollama(timeout=1.2)
    ai_ok = ai["server_ok"] and ai["model_ok"]

    # Check permission without capturing the desktop.
    screen_ok = bool(Quartz.CGPreflightScreenCaptureAccess())

    voice_ok = os.path.exists(os.path.join(DICTATE_APP, "Contents", "MacOS", "dictate"))

    return {
        "ai_ok": ai_ok,
        "ai_server_ok": ai["server_ok"],
        "ai_model": ai["model"],
        "ai_installed": ollama_installed(),
        "screen_ok": screen_ok,
        "voice_ok": voice_ok,
        "all_critical_ok": ai_ok and screen_ok,
    }


def _corner(p, c, s, e, ratio=0.68):
    """Cubic 'continuous' corner from s to e around corner point c (Apple-style squircle)."""
    c1 = (s[0] + (c[0] - s[0]) * ratio, s[1] + (c[1] - s[1]) * ratio)
    c2 = (e[0] + (c[0] - e[0]) * ratio, e[1] + (c[1] - e[1]) * ratio)
    Quartz.CGPathAddCurveToPoint(p, None, c1[0], c1[1], c2[0], c2[1], e[0], e[1])


def notch_path(x0, x1, ytop, ybot, tr, br):
    """Notch silhouette (bottom-left coords). Top corners flare outward in a long,
    shallow concave sweep so the black melts into the menu bar; bottom corners use
    continuous (squircle) curves instead of circular arcs."""
    p = Quartz.CGPathCreateMutable()
    d = br * 1.3
    xl, xr = x0 + tr, x1 - tr
    Quartz.CGPathMoveToPoint(p, None, x0, ytop)
    # concave top-left flare: tangent to the menu bar, then down the side
    Quartz.CGPathAddCurveToPoint(p, None, x0 + tr * 0.75, ytop, xl, ytop - tr * 0.25, xl, ytop - tr)
    Quartz.CGPathAddLineToPoint(p, None, xl, ybot + d)
    _corner(p, (xl, ybot), (xl, ybot + d), (xl + d, ybot))
    Quartz.CGPathAddLineToPoint(p, None, xr - d, ybot)
    _corner(p, (xr, ybot), (xr - d, ybot), (xr, ybot + d))
    Quartz.CGPathAddLineToPoint(p, None, xr, ytop - tr)
    Quartz.CGPathAddCurveToPoint(p, None, xr, ytop - tr * 0.25, x1 - tr * 0.75, ytop, x1, ytop)
    Quartz.CGPathCloseSubpath(p)
    return p


class _PreviewTracker:
    def poll(self): return None
    def send_signal(self, *_): pass
    def terminate(self): pass
    def wait(self, *_, **__): pass
    def kill(self): pass


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
        register_native_fonts()
        self.tracker = None
        self.status = dict(IDLE_STATUS)
        self.expanded = False
        self.day_done = False
        self.quick_mode = bool(os.environ.get("FL_DEV"))
        self.flush_event = threading.Event()
        self.flush_ok = False
        self.goals = fl.load_todays_goals()
        self.preview = os.environ.get("FL_PREVIEW")   # setup | goals | tracking | done (design review)
        self.pull_pct = None
        self.pull_error = None
        self.pull_label = None
        self.field = None
        self.readiness = check_system_readiness()

        force_onboarding = ("--onboarding" in sys.argv) or bool(os.environ.get("FL_ONBOARDING"))
        first_run = not os.path.exists(ONBOARDING_FLAG)
        self.onboarding_mode = force_onboarding or first_run or (not self.readiness["all_critical_ok"])

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
        self.panel.setAppearance_(AppKit.NSAppearance.appearanceNamed_(AppKit.NSAppearanceNameDarkAqua))
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
        self.shape.setStrokeColor_(None)
        self.shape.setLineWidth_(0)
        self.shape.setShadowOpacity_(0.55)
        self.shape.setShadowRadius_(14.0)
        self.shape.setShadowOffset_(AppKit.NSMakeSize(0, -5))
        self.root.layer().addSublayer_(self.shape)

        # Live-activity wings: status dot on the left, session timer on the right.
        self.dot = Quartz.CALayer.layer()
        self.dot.setBackgroundColor_(FAINT.CGColor())
        self.dot.setCornerRadius_(3.5)
        self.root.layer().addSublayer_(self.dot)

        self.timeLabel = _label("", AppKit.NSMakeRect(0, 0, 10, 10),
                                _font(12, "semibold", rounded=True, mono=True), INK)
        self.timeLabel.setAlignment_(AppKit.NSTextAlignmentRight)
        self.root.addSubview_(self.timeLabel)
        self.session_start = None

        # Content sits inside the expanded shape, below the menu-bar band.
        er = self.shape_rect(True)
        self.content = AppKit.NSView.alloc().initWithFrame_(er)
        self.content.setWantsLayer_(True)
        self.content.setAlphaValue_(0.0)
        self.root.addSubview_(self.content)

        # Clicking the notch expands it; clicking outside collapses it.
        self.root.onClick = self._notchClicked
        self.globalMonitor = AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            AppKit.NSEventMaskLeftMouseDown, self._outsideClick)

        self.apply_state(animate=False)
        self.panel.orderFrontRegardless()

        if self.preview:
            self._apply_preview(self.preview)
        elif self.onboarding_mode:
            self.setExpanded_(True)
        elif self.goals:
            self.startTracker()
        else:
            self.setExpanded_(True)

        AppKit.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0, self, "tick:", None, True)

    @objc.python_method
    def _apply_preview(self, name):
        """Render one screen with canned state, for screenshots. Never touches the ledger."""
        self.onboarding_mode = name == "setup"
        self.goals = None if name == "goals" else (SAMPLE_GOALS if name != "setup" else None)
        self.day_done = name == "done"
        if name == "tracking":
            self.tracker = _PreviewTracker()
            self.status = {"session": "Tracking today", "line": "Watching Cursor"}
        self.setExpanded_(True)
        if os.environ.get("FL_SNAPSHOT"):
            AppKit.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                2.5, self, "snapshot:", None, False)
        if os.environ.get("FL_PREVIEW_MENU"):
            AppKit.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                2.0, self, "previewMenu:", None, False)

    def snapshot_(self, timer):
        """Capture only this panel's window (never the rest of the screen), then quit."""
        subprocess.run(["screencapture", "-x", "-o", f"-l{self.panel.windowNumber()}",
                        os.environ["FL_SNAPSHOT"]], check=False)
        AppKit.NSApp.terminate_(None)

    def previewMenu_(self, timer):
        self.showMenu_(self.menuBtn)

    # ---------- geometry & animation ----------

    @objc.python_method
    def view_name(self):
        if getattr(self, "onboarding_mode", False):
            return "setup"
        if self.tracker is not None:
            return "tracking"
        if self.goals is None and not self.day_done:
            return "goals"
        return "done"

    @objc.python_method
    def shape_rect(self, expanded):
        """Shape bounds in window coords (bottom-left origin, top edge = FRAME_H)."""
        if expanded:
            w = 640
            h = self.mb + VIEW_HEIGHTS[self.view_name()]
        else:
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
        tr, br = (14.0, 28.0) if self.expanded else (8.0, 11.0)
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
        if not self.expanded and getattr(self, "dictating", False):
            self._stop_dictation()  # never keep the mic open behind a collapsed panel
        self.apply_state(animate=True)

    @objc.python_method
    def _notchClicked(self):
        if not self.expanded:
            self.setExpanded_(True)

    @objc.python_method
    def _outsideClick(self, event):
        if self.expanded and not self.preview:
            AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(
                lambda: self.setExpanded_(False))

    # ---------- content states ----------

    @objc.python_method
    def _dot(self, color, frame):
        v = AppKit.NSView.alloc().initWithFrame_(frame)
        v.setWantsLayer_(True)
        v.layer().setBackgroundColor_(color.CGColor())
        v.layer().setCornerRadius_(frame.size.height / 2.0)
        return v

    @objc.python_method
    def _card(self, symbol, title, detail, ok, button, x, ytop, cw, ch=84):
        """Setup card: icon tile, status (check or one action), title, detail."""
        yb = ytop - ch
        bg = AppKit.NSView.alloc().initWithFrame_(AppKit.NSMakeRect(x, yb, cw, ch))
        bg.setWantsLayer_(True)
        bg.layer().setBackgroundColor_(SURFACE.CGColor())
        bg.layer().setBorderColor_(BORDER.CGColor())
        bg.layer().setBorderWidth_(0.5)
        bg.layer().setCornerRadius_(9.0)
        self.content.addSubview_(bg)
        tile = AppKit.NSView.alloc().initWithFrame_(AppKit.NSMakeRect(x + 12, ytop - 40, 28, 28))
        tile.setWantsLayer_(True)
        tile.layer().setBackgroundColor_(AppKit.NSColor.clearColor().CGColor())
        tile.layer().setCornerRadius_(8.0)
        self.content.addSubview_(tile)
        img = _symbol(symbol, 14)
        if img is not None:
            iv = AppKit.NSImageView.imageViewWithImage_(img)
            iv.setFrame_(AppKit.NSMakeRect(x + 17, ytop - 35, 18, 18))
            iv.setContentTintColor_(INK)
            self.content.addSubview_(iv)
        if ok:
            chk = _symbol("checkmark.circle.fill", 18)
            if chk is not None:
                cv = AppKit.NSImageView.imageViewWithImage_(chk)
                cv.setFrame_(AppKit.NSMakeRect(x + cw - 12 - 18, ytop - 12 - 20, 18, 20))
                cv.setContentTintColor_(GREEN)
                self.content.addSubview_(cv)
        elif button is not None:
            label, action = button
            bw = 22 + 6.5 * len(label)
            self.content.addSubview_(self._pill(label, action,
                                                AppKit.NSMakeRect(x + cw - 10 - bw, ytop - 38, bw, 24), "secondary", size=12))
        self.content.addSubview_(_label(title, AppKit.NSMakeRect(x + 12, yb + 28, cw - 24, 16),
                                        _font(13, "medium"), INK))
        det = _label(detail, AppKit.NSMakeRect(x + 12, yb + 11, cw - 24, 15), _font(11.5), MUTED)
        det.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
        self.content.addSubview_(det)
        return det

    def rebuild(self):
        for v in list(self.content.subviews()):
            v.removeFromSuperview()
        self.field = None
        self.pull_label = None

        H = self.content.frame().size.height
        W = self.content.frame().size.width
        top = H - self.mb - 10     # stay clear of the physical notch band
        pad = 28
        view = self.view_name()
        inner = W - 2 * pad
        title_f = _serif(28)
        sub_f = _font(12)

        def heading(title, sub, x=pad):
            self.content.addSubview_(_label(title, AppKit.NSMakeRect(x, top - 34, inner - 50, 34), title_f, INK))
            self.content.addSubview_(_label(sub, AppKit.NSMakeRect(pad, top - 56, inner - 40, 18), sub_f, MUTED))

        # Overflow menu top-right; test-mode badge sits just left of it.
        self.menuBtn = self._icon_button("ellipsis", "showMenu:",
                                         AppKit.NSMakeRect(W - pad - 28, top - 26, 28, 24))
        self.content.addSubview_(self.menuBtn)
        if self.quick_mode:
            tag = _label("TEST MODE", AppKit.NSMakeRect(W - pad - 28 - 88, top - 22, 82, 14),
                         _font(10, "semibold"), ORANGE)
            tag.setAlignment_(AppKit.NSTextAlignmentRight)
            self.content.addSubview_(tag)

        if view == "setup":
            r = self.readiness
            heading("A little more intention.", "Your goals, your day. Kept on this Mac.")

            model = r["ai_model"]
            label, size = gemma.model_label(model)
            if r["ai_ok"]:
                ai_detail, ai_btn = f"{label} · {model}", None
            elif self.pull_pct is not None:
                ai_detail, ai_btn = f"Downloading… {int(self.pull_pct * 100)}%", None
            elif not r["ai_installed"]:
                ai_detail, ai_btn = "Ollama isn't installed", ("Get Ollama", "installOllama:")
            elif not r["ai_server_ok"]:
                ai_detail, ai_btn = "Ollama isn't running", ("Start", "fixOllama:")
            else:
                ai_detail, ai_btn = f"{label} · {size}", ("Download", "pullModel:")
            if self.pull_error and not r["ai_ok"]:
                ai_detail = self.pull_error

            gap = 10
            cw = (inner - 2 * gap) / 3.0
            ct = top - 76
            self.pull_label = self._card("cpu", "On-device AI", ai_detail, r["ai_ok"], ai_btn, pad, ct, cw)
            self._card("display", "Screen reading",
                       "Allowed" if r["screen_ok"] else "Allow, then reopen app",
                       r["screen_ok"], ("Allow", "openScreenPrefs:"), pad + cw + gap, ct, cw)
            self._card("mic.fill", "Voice goals",
                       "On-device speech" if r["voice_ok"] else "Not built yet",
                       r["voice_ok"], ("Build", "buildVoice:"), pad + 2 * (cw + gap), ct, cw)

            # Model size: segmented control, with the chosen model's cost beside it.
            names = gemma.installed_models(0.5) or []
            my = ct - 84 - 14 - 24
            seg = AppKit.NSSegmentedControl.segmentedControlWithLabels_trackingMode_target_action_(
                [lbl for _, lbl, _, _ in gemma.MODEL_CATALOG],
                AppKit.NSSegmentSwitchTrackingSelectOne, self, "pickModel:")
            seg.setFrame_(AppKit.NSMakeRect(pad, my, 280, 24))
            for i, (tag_, _, _, _) in enumerate(gemma.MODEL_CATALOG):
                if tag_ == model:
                    seg.setSelectedSegment_(i)
            self.content.addSubview_(seg)
            notes = [f"{size} download" if model not in names else "Installed"]
            if model in MODEL_NOTES:
                notes.append(MODEL_NOTES[model])
            if model == gemma.recommended_model():
                notes.append("Recommended for this Mac")
            elif gemma.ram_gb() < dict((m, n) for m, _, _, n in gemma.MODEL_CATALOG).get(model, 0):
                notes.append("May run slowly on this Mac")
            self.content.addSubview_(_label("  ·  ".join(notes),
                                            AppKit.NSMakeRect(pad + 294, my + 5, inner - 294, 14), _font(11), FAINT))

            ready = r["all_critical_ok"]
            self.content.addSubview_(self._pill("Re-check", "recheckOnboarding:",
                                                AppKit.NSMakeRect(pad, 16, 112, 32), "secondary", symbol="arrow.clockwise"))
            cont = self._pill("Continue", "finishOnboarding:",
                              AppKit.NSMakeRect(W - pad - 112, 16, 112, 32), "primary")
            cont.setAlphaValue_(1.0 if ready else 0.4)   # finishOnboarding_ ignores clicks until ready
            self.content.addSubview_(cont)

        elif view == "goals":
            heading("What matters today?", "A few clear intentions. We’ll take it from here.")
            row_y = top - 118
            fw = inner - 104 - 112 - 16
            box = AppKit.NSView.alloc().initWithFrame_(AppKit.NSMakeRect(pad, row_y, fw, 36))
            box.setWantsLayer_(True)
            box.layer().setBackgroundColor_(FILL.CGColor())
            box.layer().setCornerRadius_(6.0)
            box.layer().setBorderColor_(BORDER.CGColor())
            box.layer().setBorderWidth_(0.5)
            self.content.addSubview_(box)
            self.field = AppKit.NSTextField.alloc().initWithFrame_(
                AppKit.NSMakeRect(pad + 16, row_y + 9, fw - 32, 18))
            self.field.setFont_(_font(13))
            self.field.setTextColor_(INK)
            self.field.setBezeled_(False)
            self.field.setDrawsBackground_(False)
            self.field.setFocusRingType_(AppKit.NSFocusRingTypeNone)
            self._placeholder("Ship the demo, study calc, keep Slack short")
            self.field.setTarget_(self); self.field.setAction_("startDay:")
            self.content.addSubview_(self.field)

            title, sym = self._mic_label()
            self.micBtn = self._pill(title, "speakGoals:", AppKit.NSMakeRect(pad + fw + 8, row_y, 104, 36),
                                     "secondary", symbol=sym)
            self.content.addSubview_(self.micBtn)
            self.content.addSubview_(self._pill("Start day", "startDay:",
                                                AppKit.NSMakeRect(W - pad - 112, row_y, 112, 36), "primary"))
            self.panel.makeKeyAndOrderFront_(None)
            AppKit.NSApp.activateIgnoringOtherApps_(True)
            self.panel.makeFirstResponder_(self.field)

        elif view == "tracking":
            self.content.addSubview_(self._dot(GREEN, AppKit.NSMakeRect(pad, top - 22, 5, 5)))
            self.sessLabel = _label(self.status["session"],
                                    AppKit.NSMakeRect(pad + 16, top - 34, inner - 100, 34), title_f, INK)
            self.lineLabel = _label(self.status["line"],
                                    AppKit.NSMakeRect(pad, top - 56, inner - 40, 18), sub_f, MUTED)
            self.lineLabel.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
            self.content.addSubview_(self.sessLabel)
            self.content.addSubview_(self.lineLabel)
            goals = _label(self.goals or "", AppKit.NSMakeRect(pad, 24, inner - 262, 15), _font(11.5), FAINT)
            goals.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
            self.content.addSubview_(goals)
            self.content.addSubview_(self._pill("View report", "viewReport:",
                                                AppKit.NSMakeRect(W - pad - 244, 16, 124, 32), "secondary", symbol="doc.text"))
            self.content.addSubview_(self._pill("End day", "endDay:",
                                                AppKit.NSMakeRect(W - pad - 112, 16, 112, 32), "primary"))

        else:
            headline = "A day, accounted for." if self.day_done else "A moment to begin."
            sub = ("Your report is open in the browser." if self.day_done
                   else "Start a new day whenever you're ready.")
            heading(headline, sub)
            self.content.addSubview_(self._pill("View report", "viewReport:",
                                                AppKit.NSMakeRect(W - pad - 244, 16, 124, 32), "secondary", symbol="doc.text"))
            self.content.addSubview_(self._pill("New day", "resetDay:",
                                                AppKit.NSMakeRect(W - pad - 112, 16, 112, 32), "primary"))

    @objc.python_method
    def _placeholder(self, text):
        self.field.setPlaceholderAttributedString_(AppKit.NSAttributedString.alloc().initWithString_attributes_(
            text, {AppKit.NSFontAttributeName: _font(13), AppKit.NSForegroundColorAttributeName: FAINT}))

    @objc.python_method
    def _style_pill(self, btn, title, kind="secondary", symbol=None, size=13):
        primary = kind == "primary"
        color = ON_PRIMARY if primary else INK
        font = _font(size, "semibold" if primary else "medium")
        img = _symbol(symbol, size) if symbol else None
        btn.setAttributedTitle_(AppKit.NSAttributedString.alloc().initWithString_attributes_(
            ("  " + title) if img is not None else title,
            {AppKit.NSFontAttributeName: font, AppKit.NSForegroundColorAttributeName: color}))
        btn.setImage_(img)
        if img is not None:
            btn.setImagePosition_(AppKit.NSImageLeft)
            btn.setImageHugsTitle_(True)
            btn.setContentTintColor_(color)

    @objc.python_method
    def _pill(self, title, action, frame, kind="secondary", symbol=None, size=13):
        """Capsule button. One primary (white) per screen; everything else is a quiet fill."""
        btn = PillButton.alloc().initWithFrame_(frame)
        btn.setBordered_(False)
        btn.setWantsLayer_(True)
        btn.base_bg = PRIMARY if kind == "primary" else FILL
        btn.hover_bg = PRIMARY_HOVER if kind == "primary" else FILL_HOVER
        btn.layer().setBackgroundColor_(btn.base_bg.CGColor())
        btn.layer().setCornerRadius_(6.0)
        btn.layer().setBorderColor_(BORDER.CGColor())
        btn.layer().setBorderWidth_(0 if kind == "primary" else 0.5)
        self._style_pill(btn, title, kind, symbol, size)
        if action:
            btn.setTarget_(self); btn.setAction_(action)
        return btn

    @objc.python_method
    def _icon_button(self, symbol, action, frame):
        btn = PillButton.alloc().initWithFrame_(frame)
        btn.setBordered_(False)
        btn.setWantsLayer_(True)
        btn.base_bg = AppKit.NSColor.clearColor()
        btn.hover_bg = FILL
        btn.layer().setCornerRadius_(frame.size.height / 2.0)
        btn.setImage_(_symbol(symbol, 15, "semibold"))
        btn.setContentTintColor_(MUTED)
        btn.setTarget_(self); btn.setAction_(action)
        return btn

    # ---------- menu, onboarding & model actions ----------

    @objc.python_method
    def _menu_item(self, menu, title, action, state=False, rep=None):
        item = menu.addItemWithTitle_action_keyEquivalent_(title, action, "")
        item.setTarget_(self)
        item.setState_(AppKit.NSControlStateValueOn if state else AppKit.NSControlStateValueOff)
        if rep is not None:
            item.setRepresentedObject_(rep)
        return item

    def showMenu_(self, sender):
        menu = AppKit.NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)
        view = self.view_name()
        self._menu_item(menu, "Setup…", "openOnboarding:")
        sub = AppKit.NSMenu.alloc().init()
        for tag, label, size, _ in gemma.MODEL_CATALOG:
            self._menu_item(sub, f"{label} — {tag} ({size})", "pickModelFromMenu:",
                            state=(tag == gemma.get_model()), rep=tag)
        holder = menu.addItemWithTitle_action_keyEquivalent_("Model", None, "")
        holder.setSubmenu_(sub)
        menu.addItem_(AppKit.NSMenuItem.separatorItem())
        self._menu_item(menu, "Test mode (1-minute checkpoints)", "toggleMode:", state=self.quick_mode)
        if view == "goals":
            self._menu_item(menu, "Fill sample goals", "fillSampleGoals:")
        self._menu_item(menu, "Preview a full-day report", "demoReport:")
        if view in ("tracking", "done"):
            self._menu_item(menu, "Start a new day", "resetDay:")
        menu.addItem_(AppKit.NSMenuItem.separatorItem())
        self._menu_item(menu, "Quit FocusLedger", "quitApp:")
        menu.popUpMenuPositioningItem_atLocation_inView_(
            None, AppKit.NSMakePoint(0, sender.bounds().size.height + 4), sender)

    def quitApp_(self, sender):
        AppKit.NSApp.terminate_(None)

    def openOnboarding_(self, sender):
        self.readiness = check_system_readiness()
        self.onboarding_mode = True
        self.apply_state(animate=True)

    def recheckOnboarding_(self, sender):
        self.readiness = check_system_readiness()
        self.rebuild()

    def finishOnboarding_(self, sender):
        if not self.readiness["all_critical_ok"]:
            return
        try:
            with open(ONBOARDING_FLAG, "w") as f:
                f.write(date.today().isoformat() + "\n")
        except OSError:
            pass
        self.onboarding_mode = False
        if self.goals and self.tracker is None and not self.day_done:
            self.startTracker()
        self.apply_state(animate=True)

    def installOllama_(self, sender):
        subprocess.run(["open", "https://ollama.com/download"], check=False)

    def fixOllama_(self, sender):
        def _bg():
            try:
                if shutil.which("ollama"):
                    subprocess.Popen(["ollama", "serve"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    subprocess.run(["open", "-a", "Ollama"], check=False)
            except OSError:
                pass
            for _ in range(16):          # wait up to ~8s for the daemon
                time.sleep(0.5)
                if gemma.installed_models(0.5) is not None:
                    break
            self.readiness = check_system_readiness()
            self._on_main(self.rebuild)
        threading.Thread(target=_bg, daemon=True).start()

    def openScreenPrefs_(self, sender):
        try:
            if hasattr(Quartz, "CGRequestScreenCaptureAccess"):
                Quartz.CGRequestScreenCaptureAccess()
        except Exception:
            pass
        subprocess.run([
            "open",
            "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
        ], check=False)

    def buildVoice_(self, sender):
        def _bg():
            subprocess.run(["bash", os.path.join(BASE, "build_dictate.sh")], cwd=BASE, check=False)
            self.readiness = check_system_readiness()
            self._on_main(self.rebuild)
        threading.Thread(target=_bg, daemon=True).start()

    @objc.python_method
    def _choose_model(self, tag):
        gemma.set_model(tag)
        self.pull_error = None
        self.readiness = check_system_readiness()
        self.rebuild()

    def pickModel_(self, sender):
        self._choose_model(gemma.MODEL_CATALOG[sender.selectedSegment()][0])

    def pickModelFromMenu_(self, sender):
        self._choose_model(sender.representedObject())

    def pullModel_(self, sender):
        tag = gemma.get_model()
        self.pull_pct, self.pull_error = 0.0, None

        def _progress(frac):
            self.pull_pct = frac
            self._on_main(self._update_pull_label)

        def _bg():
            try:
                gemma.pull_model(tag, _progress)
            except Exception as e:
                self.pull_error = f"Download failed: {str(e)[:48]}"
            self.pull_pct = None
            self.readiness = check_system_readiness()
            self._on_main(self.rebuild)
        threading.Thread(target=_bg, daemon=True).start()
        self.rebuild()

    @objc.python_method
    def _update_pull_label(self):
        lb = getattr(self, "pull_label", None)
        if lb is not None and lb.superview() is not None and self.pull_pct is not None:
            label, _ = gemma.model_label(gemma.get_model())
            lb.setStringValue_(f"Downloading {label}… {int(self.pull_pct * 100)}%")

    def toggleMode_(self, sender):
        saved_text = ""
        if getattr(self, "field", None) is not None:
            saved_text = self.field.stringValue()
        self.quick_mode = not self.quick_mode
        self.rebuild()
        if saved_text and getattr(self, "field", None) is not None:
            self.field.setStringValue_(saved_text)

    def fillSampleGoals_(self, sender):
        if getattr(self, "field", None) is not None:
            self.field.setStringValue_(SAMPLE_GOALS)

    # ---------- voice goals ----------

    LISTEN_TIMEOUT = 45      # generous: the helper caps itself well before this

    @objc.python_method
    def _mic_label(self):
        if getattr(self, "dictating", False):
            return "Listening", "waveform"
        return "Speak", "mic.fill"

    def speakGoals_(self, sender):
        if getattr(self, "dictating", False):
            self._stop_dictation()
            return
        if not os.path.exists(DICTATE_APP):
            self.field.setStringValue_("")
            self._placeholder("Voice helper isn't built yet. Open Setup to build it.")
            return
        self.dictating = True
        self._style_pill(self.micBtn, *self._mic_label()[:1], symbol=self._mic_label()[1])
        threading.Thread(target=self._runDictation, daemon=True).start()

    @objc.python_method
    def _runDictation(self):
        """Launch the helper and stream its partial transcripts into the field."""
        tmp = tempfile.mkdtemp(prefix="fl-dictate-")
        out = os.path.join(tmp, "transcript.txt")
        done = out + ".done"
        try:
            subprocess.run(["open", "-n", DICTATE_APP, "--args", "--out", out],
                           check=True, cwd=BASE)
        except (OSError, subprocess.CalledProcessError) as e:
            shutil.rmtree(tmp, ignore_errors=True)
            self._on_main(self._end_dictation, f"could not start helper ({e})")
            return

        last = ""
        deadline = time.time() + self.LISTEN_TIMEOUT
        while time.time() < deadline and self.dictating:
            if os.path.exists(out):
                try:
                    cur = open(out).read().strip()
                except OSError:
                    cur = last
                if cur != last:
                    last = cur
                    if not cur.startswith("ERR:"):
                        self._on_main(self._apply_transcript, cur)
            if os.path.exists(done):
                break
            time.sleep(0.1)

        shutil.rmtree(tmp, ignore_errors=True)
        if last.startswith("ERR:"):
            self._on_main(self._end_dictation, last[4:].strip())
        else:
            self._on_main(self._end_dictation,
                          None if last else "didn't catch that — try again")

    @objc.python_method
    def _on_main(self, fn, *args):
        """Helper callbacks arrive on a reader thread; AppKit must be touched on main."""
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(lambda: fn(*args))

    @objc.python_method
    def _apply_transcript(self, text):
        f = getattr(self, "field", None)
        if f is not None and f.superview() is not None:
            f.setStringValue_(text)

    @objc.python_method
    def _end_dictation(self, error):
        self.dictating = False
        btn = getattr(self, "micBtn", None)
        if btn is not None and btn.superview() is not None:
            self._style_pill(btn, self._mic_label()[0], symbol=self._mic_label()[1])
        f = getattr(self, "field", None)
        if error and f is not None and f.superview() is not None and not f.stringValue().strip():
            self._placeholder(error)

    @objc.python_method
    def _stop_dictation(self):
        self.dictating = False
        try:
            subprocess.run(["pkill", "-f", DICTATE_EXE], check=False)
        except OSError:
            pass
        self._end_dictation(None)

    # ---------- session lifecycle & reports ----------

    def startDay_(self, sender):
        goals = self.field.stringValue().strip()
        if not goals:
            self._placeholder("Add at least one goal to start")
            return
        self.goals = goals
        self.day_done = False
        fl.append_ledger({"type": "goals", "date": date.today().isoformat(), "goals": goals})
        self.startTracker()
        self.setExpanded_(False)

    def startTracker(self):
        env = os.environ.copy()
        if self.quick_mode:
            env["FL_DEV"] = "1"
        else:
            env.pop("FL_DEV", None)
        self.tracker = subprocess.Popen(
            [PYTHON, "-u", os.path.join(BASE, "focusledger.py")],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=BASE, env=env)
        threading.Thread(target=self._reader, daemon=True).start()
        self.status = dict(IDLE_STATUS)
        self.dot.setBackgroundColor_(GREEN.CGColor())

    @objc.python_method
    def _humanize(self, line):
        """Turn tracker log lines into short sentences. Returns (session, line); None keeps the old one."""
        if line.startswith("▶"):
            return "Tracking today", "Session started"
        if line.startswith("■"):
            return "Paused", "You stepped away. Resuming when you're back"
        if line.startswith("👁"):
            app = line[1:].strip().split(" — ")[0]
            return None, f"Watching {app}"
        if line.startswith("🔎"):
            return None, "Reading your screen"
        if line.startswith("🧠"):
            return None, "Gemma is reviewing the last block"
        if line.startswith("⛔"):
            return None, "Skipped a private window"
        if line.startswith("📒"):
            parts = [p.strip() for p in line.split("|")]
            cat = parts[0].split(":")[-1].strip().replace("_", " ").capitalize()
            aligned = len(parts) > 1 and "True" in parts[1]
            return None, f"Last block: {cat}, {'on goal' if aligned else 'off goal'}"
        return None, None

    def _reader(self):
        from datetime import datetime
        proc = self.tracker
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            if line.startswith("▶"):
                self.session_start = datetime.now()
            elif line.startswith("■"):
                self.session_start = None
            elif line in ("FL_FLUSH_OK", "FL_FLUSH_FAILED"):
                self.flush_ok = line == "FL_FLUSH_OK"
                self.flush_event.set()
                continue
            elif line == "FL_DAY_ENDED":
                self.day_done = True
                self.status["line"] = "New day: enter fresh goals to resume"
                AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(self.rebuild)
                continue
            sess, human = self._humanize(line)
            if sess:
                self.status["session"] = sess
            if human:
                self.status["line"] = human

        if proc.wait() != 0:
            self.flush_ok = False
            self.flush_event.set()
            self._report_status("Tracker stopped before its checkpoint; restart tracking")

    def viewReport_(self, sender):
        """Flush any active buffer via SIGUSR1, then render and open today's report."""
        ledger = paths.LEDGER_PATH
        if not os.path.exists(ledger) and self.tracker is None:
            self.status["line"] = "Nothing to report yet. Start your day first"
            self.rebuild()
            return
        self.status["line"] = "Gemma is writing your report…"
        self.rebuild()
        threading.Thread(target=self._renderReport, daemon=True).start()

    @objc.python_method
    def _report_status(self, message):
        self.status["line"] = message
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(self.rebuild)

    @objc.python_method
    def _renderReport(self):
        if self.tracker is not None and self.tracker.poll() is None:
            self.flush_ok = False
            self.flush_event.clear()
            try:
                self.tracker.send_signal(signal.SIGUSR1)
                if not self.flush_event.wait(timeout=240) or not self.flush_ok:
                    self._report_status("Checkpoint incomplete. Check Ollama and retry")
                    return
            except OSError:
                self._report_status("Tracker stopped. Report may be incomplete")
                return
        result = subprocess.run([PYTHON, os.path.join(BASE, "report.py"),
                                 paths.LEDGER_PATH, "--open"], cwd=BASE)
        self._report_status("Report opened in your browser" if result.returncode == 0
                            else "Report failed. Check Ollama and retry")

    def demoReport_(self, sender):
        """Build a synthetic report without reading or flushing personal activity."""
        self.status["line"] = "Gemma is writing a sample report…"
        if not self.onboarding_mode:
            self.rebuild()
        threading.Thread(target=self._renderDemoReport, daemon=True).start()

    def _renderDemoReport(self):
        demo_out = str(paths.DATA_DIR / "report.demo.html")
        result = subprocess.run([PYTHON, os.path.join(BASE, "report.py"),
                                 os.path.join(BASE, "ledger.sample.jsonl"),
                                 "--out", demo_out, "--demo", "--offline", "--open"], cwd=BASE)
        self._report_status("Sample report opened in your browser" if result.returncode == 0
                            else "Sample report could not be rendered")

    def resetDay_(self, sender):
        """Stop tracking, archive today's ledger, and return to the morning goal prompt."""
        if self.tracker is not None and self.tracker.poll() is None:
            self._report_status("End your day before starting a new one")
            return
        fl.reset_todays_ledger()
        self.goals = None
        self.day_done = False
        self.session_start = None
        self.status = dict(IDLE_STATUS)
        self.dot.setBackgroundColor_(FAINT.CGColor())
        self.apply_state(animate=True)

    def endDay_(self, sender):
        self.status["session"] = "Wrapping up"
        self.status["line"] = "Gemma is reading your day"
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
                self._report_status("Tracking stopped before final checkpoint; report incomplete")
                return
            if proc.returncode != 0:
                self._report_status("Final checkpoint failed; report incomplete")
                return
        self.day_done = True
        self.session_start = None
        self.dot.setBackgroundColor_(FAINT.CGColor())
        result = subprocess.run([PYTHON, os.path.join(BASE, "report.py"),
                                 paths.LEDGER_PATH, "--open"], cwd=BASE)
        self._report_status("Report opened in your browser" if result.returncode == 0 else "Report failed")

    def tick_(self, timer):
        from datetime import datetime
        if not self.expanded:
            if self.session_start is not None:
                mins = int((datetime.now() - self.session_start).total_seconds() // 60)
                self.timeLabel.setStringValue_(f"{mins // 60}:{mins % 60:02d}")
                self.timeLabel.setTextColor_(INK)
            else:
                self.timeLabel.setStringValue_("–" if self.tracker else "")
                self.timeLabel.setTextColor_(FAINT)
        elif self.tracker is not None and not self.onboarding_mode:
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
    paths.initialize()
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)  # no Dock icon
    delegate = App.alloc().init()
    app.setDelegate_(delegate)
    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
