"""Gemma call site — both prompts, both call sites.

Everything FocusLedger knows about talking to Gemma lives here: the raw
Ollama call, JSON extraction, the two system prompts (checkpoint judge,
end-of-day report writer), and the two functions that build on them.
"""

import json

import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "gemma3:12b"
TEMPERATURE = 0.2


def gemma(prompt, system=""):
    """POST a prompt to the local Ollama server, return the raw response text."""
    try:
        r = requests.post(OLLAMA_URL, json={
            "model": MODEL,
            "prompt": prompt,
            "system": system,
            "stream": False,
            "options": {"temperature": TEMPERATURE}
        }, timeout=180)
    except requests.ConnectionError as e:
        raise RuntimeError(
            "Could not reach Ollama at " + OLLAMA_URL +
            ". Is it running? Start it with `ollama serve`."
        ) from e
    return r.json()["response"]


def gemma_json(prompt, system=""):
    """Call gemma() and parse the response as JSON.

    Strips ```json fences and takes the first {...} span before parsing,
    since Gemma sometimes wraps its JSON in prose or markdown despite
    instructions not to.
    """
    raw = gemma(prompt, system)
    stripped = raw.replace("```json", "").replace("```", "").strip()
    s, e = stripped.find("{"), stripped.rfind("}")
    try:
        if s == -1 or e == -1 or e < s:
            raise ValueError("no JSON object found in response")
        return json.loads(stripped[s:e + 1])
    except (ValueError, json.JSONDecodeError) as err:
        raise ValueError(
            f"Failed to parse JSON from Gemma response: {err}\n"
            f"Raw response was:\n{raw}"
        ) from err


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
 "note": "<one short sentence of evidence>"}
No preamble, no markdown fences."""

REPORT_SYS = """You are a focus coach. You get today's goals and a list of
30-minute verdicts from work sessions. Be specific and direct — no filler,
no praise padding. Reply with ONLY this JSON:
{"headline": "<one sentence summarizing the day>",
 "goal_progress": [{"goal": "...", "time_min": 0, "verdict": "<one line>"}],
 "drift": [{"what": "...", "time_min": 0, "when": "..."}],
 "tomorrow": ["<specific action>", "<specific action>"]}
No preamble, no markdown fences."""


def checkpoint(goals, samples):
    """Judge a batch of screen samples against today's goals.

    samples: list of dicts with keys t, app, title, text.
    Returns the parsed verdict dict.
    """
    body = "\n\n".join(
        f"[{s['t']}] app={s['app']} | title={s['title']}\n{s['text'][:1500]}"
        for s in samples)
    return gemma_json(f"TODAY'S GOALS:\n{goals}\n\nSCREEN SAMPLES:\n{body}", CHECKPOINT_SYS)


def daily_report(goals, verdicts):
    """Synthesize the whole day from the ledger of checkpoint verdicts.

    goals: string of today's stated goals.
    verdicts: list of verdict dicts (as produced by checkpoint / stored in ledger.jsonl).
    Returns the parsed report dict.
    """
    body = "\n".join(json.dumps(v) for v in verdicts)
    return gemma_json(f"TODAY'S GOALS:\n{goals}\n\nSESSION VERDICTS:\n{body}", REPORT_SYS)


def warmup():
    """Send a tiny prompt to force Ollama to load the model into memory.

    Call this once at startup so the first real checkpoint isn't the one
    that eats the ~30s model-load penalty. Errors are not swallowed.
    """
    return gemma("reply with the single word: ready")


if __name__ == "__main__":
    print("warmup:", warmup())

    goals = "Finish the FocusLedger capture loop; study for calc exam"

    sample_a = {
        "t": "14:02",
        "app": "Code",
        "title": "focusledger.py — 305Gemma",
        "text": (
            "focusledger.py\n"
            "def idle_seconds():\n"
            "  out = subprocess.check_ou6put(\n"
            "  \"ioreg -c IOHIDSystem | awk\n"
            "1  BLOCKED_APPS = {\"1Passwor\"\n"
            "def is_blocked(app, tit1e):\n"
            "  retvrn any(w in t for w in B\n"
            "PROBLEMS  OUTPUT  TERM1NAL\n"
            "venv (305Gemma-efficiencyproj)\n"
            "Ln 42, Col 17  Spaces: 4  UTF-8"
        ),
    }

    sample_b = {
        "t": "14:32",
        "app": "Google Chrome",
        "title": "MrBeast $1,000,000 Challenge - YouTube",
        "text": (
            "MrBeast $1,000,00O Challenge - YouTube\n"
            "1.2M w4tching now\n"
            "SUBSCRIBE  142M subscribers\n"
            "0:47 / 18:32\n"
            "LIKE  DISLIKE  SHARE  CLIP\n"
            "Up next  Autoplay\n"
            "Comments  4,821\n"
            "\"bro really said last to leave\"\n"
            "reply  1.2K"
        ),
    }

    required_keys = {"activity", "category", "goal", "aligned", "confidence", "note"}

    verdict_a = checkpoint(goals, [sample_a])
    print("verdict A:", json.dumps(verdict_a, indent=2))
    assert required_keys.issubset(verdict_a.keys()), f"missing keys in verdict A: {required_keys - verdict_a.keys()}"

    verdict_b = checkpoint(goals, [sample_b])
    print("verdict B:", json.dumps(verdict_b, indent=2))
    assert required_keys.issubset(verdict_b.keys()), f"missing keys in verdict B: {required_keys - verdict_b.keys()}"

    print("SELFTEST PASS")
