# Changelog

## Unreleased

- Add three headline metrics, personal baselines, a timeline with explicit gaps,
  and per-goal time bars with optional morning budgets and manual completion.
- Regenerate live report facts after saved checkpoints; refresh open live pages
  every 30 seconds and keep final, historical, and demo reports frozen.
- Remove unsupported strain and switching proxies; record explicit interval bounds.

- Redesign the native notch and daily report with Geist, Instrument Serif, warm
  paper/ink palettes, muted accents, and a minimal editorial hierarchy.
- Bundle OFL fonts locally and embed fonts/licenses in standalone reports.

## 0.1.0a1 — unreleased

- Prepare a source-based public alpha with MIT licensing and contributor workflows.
- Use ScreenCaptureKit to target individual windows; keep capture pixels in memory and reduce sensitive logging.
- Move history/reports into per-user storage with non-destructive legacy migration.
- Isolate synthetic demos and add offline report rendering and day selection.
- Surface checkpoint/report failures and preserve history across new-day actions.
- Add deterministic regression tests, pinned dependencies, and macOS CI.
- Verify native single-window capture, neighbor exclusion, OCR, and local Gemma integration.
- Fix the offline demo headline and replace inferred goal completion with recorded drift share.
