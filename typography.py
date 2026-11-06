"""Bundled OFL fonts shared by native UI and self-contained HTML reports."""
import base64
from pathlib import Path

FONT_DIR = Path(__file__).resolve().parent / 'assets' / 'fonts'


def report_font_css():
    rules = ["/* " + (FONT_DIR / name).read_text() + " */" for name in ("Geist-OFL.txt", "InstrumentSerif-OFL.txt")]
    for filename, family, style, weight in [
        ('Geist.ttf', 'Geist', 'normal', '100 900'),
        ('InstrumentSerif-Regular.ttf', 'Instrument Serif', 'normal', '400'),
        ('InstrumentSerif-Italic.ttf', 'Instrument Serif', 'italic', '400'),
    ]:
        encoded = base64.b64encode((FONT_DIR / filename).read_bytes()).decode('ascii')
        rules.append(f'@font-face {{ font-family: "{family}"; font-style: {style}; font-weight: {weight}; font-display: swap; src: url(data:font/ttf;base64,{encoded}) format("truetype"); }}')
    return '\n'.join(rules)


def register_native_fonts():
    """Register only for this process; never install fonts in the user's account."""
    import CoreText
    from Foundation import NSURL
    for filename in ('Geist.ttf', 'InstrumentSerif-Regular.ttf', 'InstrumentSerif-Italic.ttf'):
        CoreText.CTFontManagerRegisterFontsForURL(
            NSURL.fileURLWithPath_(str(FONT_DIR / filename)), CoreText.kCTFontManagerScopeProcess, None)
