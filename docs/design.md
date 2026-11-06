# Visual direction

FocusLedger pairs Instrument Serif for reflection and large numbers with Geist
for controls and supporting text. The direction draws from the editorial warmth
of [Te Vārtā](https://www.tevarta.com/) and the spare typography of
[Rezt](https://rezt.dev/), with an original layout for daily goal reflection.

The report uses paper/ink colors, muted sage and ochre, hairline separators, one
primary alignment statistic, and numbered sections. It follows the system light
or dark preference, adapts to narrow screens, supports keyboard-operated sessions,
and has print styles. Avoid saturated dashboard rings, decorative cards, and
unnecessary motion.

The native notch retains its black outer silhouette so it joins the hardware
notch. Expanded views use warm text, serif headings, fine borders, and compact
rectangular controls. Preview mode stays expanded for visual inspection; it does
not start tracking. Set FL_PREVIEW to setup, goals, tracking, or done with temporary
FL_DATA_DIR and FL_LEDGER_PATH values.

Fonts are bundled under SIL OFL 1.1. CoreText registers them only for the app's
process. HTML exports embed the fonts and their license notices, so opening or
sharing a report does not make font/network requests. Keep assets/fonts with the
checkout and see THIRD_PARTY_NOTICES.md for attribution.

## Verification

The design change passes the existing regression suite plus font registration and
self-contained export checks (22 tests total). Chrome inspection covers the dark
report's heading, intention, category breakdown, and sessions. Native inspection
covers the expanded setup panel with bundled typefaces. Tracking behavior and
personal data are outside this visual change; previews use temporary data paths.
