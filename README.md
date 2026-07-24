<div align="center">

# FocusLedger

**A local-only work-session tracker that understands what you did, not just where you clicked.**

*It forgets what it saw. It only remembers what it meant.*

`Gemma 3 12B` · `Ollama` · `Apple Vision OCR` · `100% on-device` · `zero network`

</div>

---

## The idea

Screen-time apps tell you *"3h 14m in Chrome."* Useless — they see the app, not the meaning.

FocusLedger runs **Gemma locally on your Mac**. It watches only while you're actually working, reads what's on screen with on-device OCR, and judges it against goals you set that morning. At the end of the day it hands you one report: what you did, whether it matched your intent, and what to fix tomorrow.

It **cannot** be a cloud product. Streaming your whole screen all day to an API is unaffordable, and nobody would consent to it. **Local open weights are the only reason this can exist.**

## Where Gemma is used — two distinct call sites

Remove Gemma and there is no product, only a screenshot folder. Both call sites live in [`gemma.py`](gemma.py).

1. **Per-block classification** (`checkpoint()`, prompt `CHECKPOINT_SYS`) — every 30 minutes a batch of messy OCR samples + your goals become **one structured JSON verdict**: activity, category, which goal it served, aligned or not, confidence, evidence. Low temperature — a judge, not a poet.
2. **Whole-day synthesis** (`daily_report()`, prompt `REPORT_SYS`) — at end of day Gemma reads *only the ledger of verdicts* and writes the headline, per-goal progress, drift list, and tomorrow's actions. This is reflection across the whole day — something no rules engine does.

The OCR is deliberately noisy (sidebar labels, timestamps, fragments). We don't ask Gemma for clean extraction — we ask it for a correct **judgment** over messy input.

## Architecture

```
SESSION MANAGER   idle_seconds() every 10s → active? idle?  (gates everything below)
      │ only runs while you're active
CAPTURE           frontmost app + title · screenshot only if screen changed · blocklist
      │
OCR               Apple Vision, on-device → text → DELETE IMAGE IMMEDIATELY
      │
BUFFER            ~30 min of text samples, in memory only
      │
GEMMA #1          checkpoint() every 30 min → one JSON verdict → raw text discarded
      │
LEDGER            ledger.jsonl — verdicts only, never pixels or raw text
      │
GEMMA #2          end of day → HTML report
```

Everything on disk is a verdict. No pixels, no raw text. Ever.

## Privacy by design

- **Session-gated** — polls macOS idle time (`ioreg HIDIdleTime`, no permissions). Captures only while you're active; idle time is never observed.
- **Images deleted the instant they're read** — a screenshot exists on disk for the length of one OCR call and no longer. `captures/` stays empty mid-run.
- **Raw text never persisted** — it lives in memory until the next checkpoint turns it into a verdict, then it's gone.
- **Blocklist** — password managers, Messages/Signal, and titles like *bank*, *login*, *checkout*, *private browsing* are refused outright.
- **Airplane mode still works** — every token runs on your laptop.

## Setup

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# On-device model (~8 GB)
ollama pull gemma3:12b
ollama serve   # in a separate terminal if not already running
```

**Grant Screen Recording permission**: System Settings → Privacy & Security → Screen Recording → add your terminal, then **restart the terminal** (or screenshots come back black).

## Usage

```bash
# Track a real work session (asks your goals, then watches until Ctrl-C)
python focusledger.py

# Build the end-of-day report from your ledger
python report.py ledger.jsonl --open

# Generate a full demo day (or append seeded evening+night onto your real morning)
python seed.py                                   # → ledger.sample.jsonl
python seed.py --base ledger.jsonl --out ledger.demo.jsonl
python report.py ledger.sample.jsonl --open
```

Dev mode shortens every interval for testing — `FL_DEV=1 python focusledger.py` (2s poll, 20s idle-close, 60s checkpoints).

## Files

| File | Role |
|---|---|
| [`focusledger.py`](focusledger.py) | Session manager + capture loop (entry point) |
| [`vision_ocr.py`](vision_ocr.py) | Apple Vision OCR; image deleted right after |
| [`gemma.py`](gemma.py) | Ollama client + both prompts, both call sites |
| [`report.py`](report.py) | Ledger → Gemma synthesis → HTML |
| [`seed.py`](seed.py) | Demo evening/night sessions |
| [`templates/report.html`](templates/report.html) | Jinja2 report template |
| `ledger.jsonl` | Your real activity — **gitignored** |
| [`ledger.sample.jsonl`](ledger.sample.jsonl) | Committed sample for the demo |

## Libraries

Ollama · Gemma 3 12B · pyobjc (Vision, Quartz, AppKit) · mss · Jinja2 · requests

## Honest notes

- The demo report combines **real captured data with seeded evening/night sessions** — your morning is real, `seed.py` fills out the day.
- Single-monitor only; classification is imperfect on ambiguous screens.
- `ledger.jsonl` is gitignored on purpose — it's your real screen activity.
