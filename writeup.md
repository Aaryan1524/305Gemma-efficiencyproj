# FocusLedger — Kaggle Writeup

**Build with Gemma · 305 SummerCodex · FIU · July 24, 2026**

*A local-only work-session tracker that judges what you did against what you meant to do — every token on-device.*

---

## Problem

Existing screen-time trackers measure the **application**, not the **intent**. "3h in Chrome" can't tell you whether the day was good — Chrome held both your calculus lecture and your Reddit spiral. The signal people actually want ("did I do what I meant to?") requires understanding *content*, and doing that continuously has been impossible: it's too expensive to stream a screen to an API all day, and no one would consent to it.

## Solution

FocusLedger is session-scoped local capture with two Gemma judgments layered on top:

1. It tracks **honest work sessions** (via macOS idle time), never a 16-hour timeline full of holes.
2. While you're active it captures the focused window, OCRs it **on-device**, and immediately deletes the image.
3. Every 30 minutes **Gemma classifies** the block against goals you set that morning.
4. At end of day **Gemma synthesizes** the whole ledger into a report: what you did, whether it aligned, and what to fix tomorrow.

## Why Gemma specifically

**Cost and consent.** Continuous screen understanding is economically impossible via a metered API and nobody would agree to upload their whole screen all day. Open weights running on-device are not an optimization here — they are the **enabling condition**. Remove Gemma and there is no product, only a folder of screenshots. The closing demo makes this literal: turn on airplane mode, run a classification, it still works.

## How Gemma is used — two call sites

Both live in `gemma.py`.

- **Per-block classification** — `checkpoint()` with system prompt `CHECKPOINT_SYS`. Input: today's goals + a batch of OCR samples (focused app, window title, messy screen text). Output: strict JSON — `activity`, `category` (deep_work / learning / communication / admin / drift), which `goal` it served, `aligned`, `confidence`, `note`. Temperature 0.2 — a judge, not a poet. The prompt explicitly instructs Gemma to judge **only the focused app**, so background audio isn't mistaken for the activity.
- **Whole-day synthesis** — `daily_report()` with `REPORT_SYS`. Input: goals + the day's verdicts (only). Output: `headline`, per-goal `goal_progress`, `drift` list, `tomorrow` actions. This is synthesis across the entire day, not classification — the part no rules engine can do.

## Architecture

```
SESSION MANAGER → CAPTURE → OCR (delete image) → BUFFER → GEMMA #1 (verdict) → LEDGER → GEMMA #2 (report)
```

The session manager polls `ioreg HIDIdleTime` every 10s and gates everything below it — capture only runs while you're active. See the README for the full diagram.

## Privacy design

- **Images deleted post-OCR** — a screenshot lives on disk only for the length of one OCR call; `captures/` stays empty mid-run.
- **Raw text never persisted** — held in memory until the next checkpoint converts it to a verdict, then discarded.
- **App/title blocklist** — password managers, DMs, and titles like *bank*, *login*, *checkout*, *private browsing* are never captured.
- **Session gating** — idle time is never observed, so the tool sees your work, not your life.

Everything that touches disk (`ledger.jsonl`) is a verdict. No pixels, no raw text, ever.

## Challenges

- **OCR noise** — Apple Vision output is messy (sidebar labels, timestamps, notification fragments). Rather than demand clean extraction, we treat Gemma as a **judge over messy input**, truncate each sample to ~1500 chars, and let the model filter. This turned the hardest problem into a non-problem.
- **Background-audio false positives** — a music tab playing while you code used to read as a distraction. Solved with explicit foreground-window detection (AppKit + Quartz): we judge only the focused app.
- **LLM JSON quirks** — Gemma occasionally wraps JSON in prose or leaves a trailing comma. `gemma_json()` strips fences, extracts the `{...}` span, and removes trailing commas before parsing.

## Libraries used

Ollama · Gemma 3 12B · pyobjc (Vision, Quartz, AppKit) · mss · Jinja2 · requests

## Limitations

- 12B classification is imperfect on ambiguous screens.
- The demo report **combines real captured data with seeded evening/night sessions** — the morning is real, `seed.py` fills out the day. Stated plainly because it's the honest thing to do.
- Single-monitor, macOS only.
