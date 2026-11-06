# Visual direction

FocusLedger pairs Instrument Serif for reflection and large numbers with Geist
for controls and supporting text. The direction draws from the editorial warmth
of [Te Vārtā](https://www.tevarta.com/) and the spare typography of
[Rezt](https://rezt.dev/), with an original layout for daily goal reflection.

The report uses paper/ink colors, muted sage and ochre, hairline separators, three
headline metrics, a timeline, and per-goal allocation. It follows the system light
or dark preference, adapts to narrow screens, supports keyboard-operated sessions,
and has print styles. Avoid saturated dashboard rings, decorative cards, and
unnecessary motion.

The native notch retains its original black outer silhouette and shadow so it
joins the hardware notch. The outer shape has no colored stroke or outline. Expanded views use warm text, serif headings, fine borders, and compact
rectangular controls. Preview mode stays expanded for visual inspection; it does
not start tracking. Set FL_PREVIEW to setup, goals, tracking, or done with temporary
FL_DATA_DIR and FL_LEDGER_PATH values.

Fonts are bundled under SIL OFL 1.1. CoreText registers them only for the app's
process. HTML exports embed the fonts and their license notices, so opening or
sharing a report does not make font/network requests. Keep assets/fonts with the
checkout and see THIRD_PARTY_NOTICES.md for attribution.

## Verification

The original typography change passed font registration and export checks (22 tests
at that stage). The performance report now passes 43 tests, including native budget
controls, explicit completion recording, timeline gaps, baselines, and live publishing. Chrome inspection covers the dark
report's heading, intention, category breakdown, and sessions. Native inspection
covers the expanded setup and goals panels with bundled typefaces. Tracking behavior and
personal data are outside this visual change; previews use temporary data paths.

## Performance report

The overview prioritizes alignment, deep-work time, and unmatched time (drift).
Only alignment gets a personal baseline comparison. One timeline and per-goal bars
sit underneath; category details, sessions, and AI reflection remain collapsed.
No cognitive strain, inferred task-completion percentage, or app-switch proxy is
shown. Desktop light and mobile dark WebKit renders have been inspected, including
font loading and horizontal-overflow checks. See report-light.png and
report-dark-mobile.png under docs/images for the synthetic sample.
