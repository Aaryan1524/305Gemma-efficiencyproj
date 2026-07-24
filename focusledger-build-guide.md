# FocusLedger — Build Guide

**A local-only work session tracker that understands what you did, not just where you clicked.**

Built for: Build with Gemma — 305 SummerCodex, FIU, July 24 2026.

---

## 0. The pitch (memorize this, it's 30 of your 100 points)

Screen-time apps tell you *"3h 14m in Chrome."* Useless. They see the app, not the meaning.

FocusLedger runs Gemma locally on your Mac. It watches only while you're actually working, reads what's on screen, and judges it against goals you set that morning. At the end of the day it hands you one report: what you did, whether it matched your intent, and what to fix tomorrow.

It cannot be a cloud product. Streaming your whole screen all day to an API is unaffordable, and nobody would consent to it. **Local weights are the only reason this can exist.** That's your Gemma Integration argument.

**One-liner:** *It forgets what it saw. It only remembers what it meant.*

---

## 1. Rules compliance check

| Requirement | How you satisfy it |
|---|---|
| Gemma must be core | Gemma does all classification and report generation. Remove Gemma and there is no product — only a screenshot folder. |
| Listed build area | Hits two: "On-device or privacy-first AI apps" and "Small business or productivity tools." |
| Original work created during the hackathon | Start the repo today with an empty first commit at 9 AM. Commit often — the history is your proof. |
| Public code repo | GitHub, public, README explaining the Gemma call sites. |
| Working demo | Terminal recording + the HTML report. Both are explicitly accepted. |
| Kaggle writeup | Section 8 below has the outline. Write it at 5 PM, not 7:55 PM. |
| Explain libraries used | List Ollama, pyobjc/Vision, mss in the writeup. Required by the rules. |

---

## 2. Session-based tracking (your specific requirement)

You work 3h morning, 5h evening, 2h night. You do **not** want a 16-hour timeline with holes in it.

So don't run a clock. Run **sessions**.

**The rule:**
- Poll for user input activity (keyboard/mouse) every 10 seconds.
- If there's been input in the last 60 seconds → you're active. Capture.
- If no input for 10 minutes → close the session. Stop capturing entirely.
- Next input after a closed session → open a **new** session.

Your day becomes:
```
Session 1  09:12 – 12:20   (3h 08m)
Session 2  17:45 – 22:30   (4h 45m)
Session 3  23:40 – 01:35   (1h 55m)
```

Three honest blocks. No phantom hours. This is also cheaper — you're not burning CPU or disk while the laptop sits idle, which is exactly the "zero marginal cost" argument.

macOS gives you idle time for free:

```python
import subprocess

def idle_seconds():
    out = subprocess.check_output(
        "ioreg -c IOHIDSystem | awk '/HIDIdleTime/ {print $NF/1000000000; exit}'",
        shell=True, text=True
    )
    return float(out.strip())
```

No permissions needed, no extra library. `HIDIdleTime` is nanoseconds since last human input.

---

## 3. Architecture

```
┌─────────────────────────────────────────────────┐
│ SESSION MANAGER                                 │
│ idle_seconds() every 10s → active? idle?        │
│ opens/closes sessions, gates everything below   │
└────────────────────┬────────────────────────────┘
                     │ only runs while active
┌────────────────────▼────────────────────────────┐
│ CAPTURE (every 10s)                             │
│ • frontmost app name + window title             │
│ • screenshot → only if screen changed           │
│ • blocklist check → skip capture entirely       │
└────────────────────┬────────────────────────────┘
┌────────────────────▼────────────────────────────┐
│ OCR — Apple Vision, on-device                   │
│ image → raw text → DELETE IMAGE IMMEDIATELY     │
└────────────────────┬────────────────────────────┘
┌────────────────────▼────────────────────────────┐
│ BUFFER — 30 min of samples in memory            │
└────────────────────┬────────────────────────────┘
┌────────────────────▼────────────────────────────┐
│ GEMMA (Ollama, local) — every 30 min            │
│ batch + today's goals → one JSON verdict        │
│ raw text DISCARDED after this                   │
└────────────────────┬────────────────────────────┘
┌────────────────────▼────────────────────────────┐
│ LEDGER — ledger.jsonl, verdicts only            │
└────────────────────┬────────────────────────────┘
┌────────────────────▼────────────────────────────┐
│ GEMMA — end of day → HTML report                │
└─────────────────────────────────────────────────┘
```

Everything on disk is a verdict. No pixels, no raw text. Ever.

---

## 4. Setup

```bash
mkdir focusledger && cd focusledger
git init
python3 -m venv venv && source venv/bin/activate
pip install mss pyobjc-framework-Vision pyobjc-framework-Quartz requests jinja2

ollama pull gemma3:4b
ollama run gemma3:4b "reply with the single word: ready"
```

**Use `gemma3:4b`.** The 12B is a better judge but you're demoing on a laptop under load in a room full of people. 4B is fast and good enough for classification. Note the exact model tag — the judges will ask.

**Grant Screen Recording permission now**, not at 5 PM. System Settings → Privacy & Security → Screen Recording → add Terminal. It requires a restart of Terminal. Do this first or you will lose 20 minutes to a black screenshot.

---

## 5. The four components

### 5.1 Foreground window (solves the YouTube-music problem)

The music tab is not focused. Your editor is. Capture focus explicitly and Gemma stops calling background audio a distraction.

```python
from AppKit import NSWorkspace
import Quartz

def frontmost():
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    name = app.localizedName()
    title = ""
    wins = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID)
    for w in wins:
        if w.get('kCGWindowOwnerName') == name and w.get('kCGWindowName'):
            title = w['kCGWindowName']
            break
    return name, title
```

### 5.2 Blocklist — refuse to look

```python
BLOCKED_APPS = {"1Password", "Keychain Access", "Bitwarden", "Messages", "Signal", "FaceTime"}
BLOCKED_TITLE_WORDS = ["bank", "chase", "wells fargo", "password", "login",
                       "sign in", "checkout", "payment", "incognito", "private browsing"]

def is_blocked(app, title):
    if app in BLOCKED_APPS:
        return True
    t = title.lower()
    return any(w in t for w in BLOCKED_TITLE_WORDS)
```

When blocked, log `{"blocked": true}` and capture nothing. Password fields render as dots anyway so OCR sees `••••••••`, but say the blocklist part out loud in the demo — judges reward visible restraint.

### 5.3 OCR — Apple Vision, on-device

```python
import Vision, Quartz
from Foundation import NSURL

def ocr(path):
    url = NSURL.fileURLWithPath_(path)
    src = Quartz.CGImageSourceCreateWithURL(url, None)
    img = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(1)  # 1 = fast, 0 = accurate
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(img, None)
    handler.performRequests_error_([req], None)
    lines = []
    for obs in (req.results() or []):
        c = obs.topCandidates_(1)
        if c:
            lines.append(c[0].string())
    return "\n".join(lines)
```

Vision ships with macOS, runs entirely offline, and is noticeably sharper than Tesseract on UI text.

**The OCR output will be messy.** Sidebar labels, timestamps, notification fragments. That's fine and it's the whole point: you're not asking for clean extraction, you're asking Gemma for a correct *judgment*. Truncate each sample to ~1500 chars and let the model filter.

### 5.4 Gemma call

```python
import requests, json

def gemma(prompt, system=""):
    r = requests.post("http://localhost:11434/api/generate", json={
        "model": "gemma3:4b",
        "prompt": prompt,
        "system": system,
        "stream": False,
        "options": {"temperature": 0.2}
    }, timeout=180)
    return r.json()["response"]

def gemma_json(prompt, system=""):
    raw = gemma(prompt, system)
    raw = raw.replace("```json", "").replace("```", "").strip()
    s, e = raw.find("{"), raw.rfind("}")
    return json.loads(raw[s:e+1])
```

Low temperature. You want a judge, not a poet.

**Checkpoint prompt** — runs every 30 minutes on the buffered batch:

```python
CHECKPOINT_SYS = """You classify a 30-minute block of computer activity.
You receive the user's goals for today and samples of their screen.
Each sample has the focused app, window title, and OCR text.

IMPORTANT: background audio (music, a video playing in an unfocused tab)
is NOT the activity. Judge only the FOCUSED application.

Reply with ONLY this JSON, no other text:
{"activity": "<8 words max, what they were actually doing>",
 "category": "<deep_work|learning|communication|admin|drift>",
 "goal": "<which stated goal this served, or 'none'>",
 "aligned": true/false,
 "confidence": "<high|medium|low>",
 "note": "<one short sentence of evidence>"}"""

def checkpoint(goals, samples):
    body = "\n\n".join(
        f"[{s['t']}] app={s['app']} | title={s['title']}\n{s['text'][:1500]}"
        for s in samples)
    return gemma_json(f"TODAY'S GOALS:\n{goals}\n\nSCREEN SAMPLES:\n{body}", CHECKPOINT_SYS)
```

Append the verdict to `ledger.jsonl`, clear the buffer, discard the text.

**Report prompt** — end of day, reads only the ledger:

```python
REPORT_SYS = """You are a focus coach. You get today's goals and a list of
30-minute verdicts from work sessions. Be specific and direct — no filler,
no praise padding. Reply with ONLY this JSON:
{"headline": "<one sentence summarizing the day>",
 "goal_progress": [{"goal": "...", "time_min": 0, "verdict": "<one line>"}],
 "drift": [{"what": "...", "time_min": 0, "when": "..."}],
 "tomorrow": ["<specific action>", "<specific action>"]}"""
```

That second Gemma call matters for scoring. It's not classification — it's synthesis across the whole day, something no rules engine does. Point at it during judging.

---

## 6. Timeline (8:30 AM – 6:00 PM, working around workshops)

You have four workshops eating your day. Build in the gaps and during the ones you don't need. Budget ~7 working hours.

| Window | Goal | Done when |
|---|---|---|
| **9:00–9:30** | Repo + Ollama verified + Screen Recording permission granted | `ollama run gemma3:4b` responds; screenshot isn't black |
| **9:30–10:30** | `gemma_json()` returns a valid dict from a hand-written fake sample | You can classify one made-up screen correctly |
| **10:30–11:30** | Session manager + capture loop | Walk away 10 min → session closes. Come back → new session opens. Print to console. |
| **11:30–1:00** | Vision OCR wired in; blocklist; images deleted after OCR | `ls` the capture dir mid-run → empty |
| **1:00–2:00** | *Lunch — leave it running on yourself. This is your real data.* | |
| **2:00–3:00** | 30-min checkpoint batching → `ledger.jsonl` | Real verdicts from your morning appear in the file |
| **3:00–4:00** | Morning goal prompt + end-of-day report generator | `python report.py` prints JSON from the ledger |
| **4:00–5:00** | HTML report via Jinja2. **Make it look good.** | Opens in browser, session blocks visible, readable from 6 feet |
| **5:00–5:45** | Kaggle writeup + README + push repo | Submitted, not drafted |
| **5:45–6:00** | Rehearse the demo out loud twice | You can do it in 3 minutes without looking |

**Hard stop at 5 PM on features.** Whatever isn't working at 5 PM doesn't exist. Ship what runs.

### Bonus tier, only if you're ahead
1. Mid-day nudge — if the last two checkpoints are both `aligned: false`, one macOS notification. Nothing more.
2. Confidence gating — hide `"confidence": "low"` verdicts from the report.
3. A "zero network" indicator in the UI. Cheap to build, strong for the narrative.

---

## 7. The demo (3 minutes, 6:00 PM)

Don't live-capture on stage. Wi-Fi, projectors, and permission dialogs will betray you.

1. **10s** — "Screen time apps tell you *where* you clicked. They can't tell you *what* you did."
2. **30s** — Show the morning prompt. Type three real goals.
3. **40s** — Run the capture loop live for ~30 seconds on your own laptop. Show the terminal printing focus + category. Then open the capture folder: **empty**. Say the line: *it forgets what it saw, it only remembers what it meant.*
4. **60s** — Open the HTML report from your real morning session plus seeded afternoon data. Walk the three session blocks. Show one drift entry.
5. **30s** — "Every token ran on this laptop. Gemma 3 4B through Ollama. Airplane mode, still works." Turn on airplane mode and run one classification. **This is your closing move.**

That last beat is the whole pitch in one gesture. Practice it.

### Seed data

Write `seed.py` that generates a plausible evening + night session and appends to a copy of your ledger. Your morning is real; the rest fills out the report so it doesn't look thin. Say plainly in the writeup that the demo report combines real captured data with seeded sessions — judges respect that and it costs you nothing.

---

## 8. Kaggle writeup outline

- **Problem** — Existing trackers measure application, not intent. A person can't tell from "3h Chrome" whether the day was good.
- **Solution** — Session-scoped local capture, Gemma judges content against user-declared goals, single end-of-day synthesis.
- **Why Gemma specifically** — Cost and consent. Continuous screen understanding is economically impossible via API and nobody would agree to it. Open weights running on-device are the enabling condition, not an optimization.
- **Architecture** — the diagram from section 3.
- **How Gemma is used** — two distinct call sites: per-block classification (structured JSON) and whole-day synthesis. Include the actual prompts.
- **Privacy design** — images deleted post-OCR, raw text never persisted, app/title blocklist, session gating means idle time is never observed.
- **Challenges** — OCR noise handled by treating the model as a judge over messy input rather than demanding clean extraction; background-audio false positives solved with foreground-window detection.
- **Libraries used** — Ollama, Gemma 3 4B, pyobjc (Vision, Quartz, AppKit), mss, Jinja2. *(Rules require you to list these.)*
- **Limitations** — 4B classification is imperfect on ambiguous screens; demo combines real and seeded sessions; single-monitor only.

---

## 9. Things that will go wrong

| Symptom | Fix |
|---|---|
| Black screenshots | Screen Recording permission — Terminal must be restarted after granting |
| Gemma returns prose around the JSON | You already strip fences; also add "no preamble" to the system prompt |
| OCR returns nothing | Vision needs a real file path; check the image actually wrote before OCR |
| 30-min batches too slow to test | Set the interval to 60 seconds during dev. Change it back before demoing. |
| Ollama first call takes 30s | Model loading. Send one warmup call at startup. |
| Everything classified `drift` | Your goals string is too vague. Feed it specific goals, not "be productive." |

---

## 10. Repo layout

```
focusledger/
├── README.md
├── requirements.txt
├── focusledger.py      # session manager + capture loop
├── vision_ocr.py
├── gemma.py            # both prompts, both call sites
├── report.py           # ledger → HTML
├── seed.py             # demo data
├── templates/report.html
└── ledger.jsonl        # gitignore this — it's your real activity
```

Gitignore `ledger.jsonl`. Commit a `ledger.sample.jsonl` instead. You do not want your actual screen activity in a public repo at 5:45 PM.
