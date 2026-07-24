"""End-of-day report: ledger.jsonl -> HTML.

Reads only the ledger (verdicts and session markers — never pixels or raw
text), reconstructs the day's sessions, computes factual time-by-category,
and asks Gemma to synthesize the whole day. That synthesis is Gemma call
site #2: not classification, but reflection across everything at once —
the thing no rules engine can do.
"""

import argparse
import json
import os
import webbrowser
from collections import defaultdict

from jinja2 import Environment, FileSystemLoader

import gemma

LEDGER_PATH = "ledger.jsonl"
TEMPLATE_DIR = "templates"
TEMPLATE_NAME = "report.html"
OUT_PATH = "report.html"

# Category → display label + accent color. Kept in sync with CHECKPOINT_SYS.
CATEGORY_META = {
    "deep_work":     {"label": "Deep work",     "color": "#2e9e6b"},
    "learning":      {"label": "Learning",      "color": "#3b82c4"},
    "communication": {"label": "Communication", "color": "#d99a2b"},
    "admin":         {"label": "Admin",         "color": "#8a8f98"},
    "drift":         {"label": "Drift",         "color": "#d0524b"},
}
UNKNOWN_META = {"label": "Other", "color": "#8a8f98"}


def load_rows(path):
    """Parse the ledger into a list of dicts, skipping any unparseable lines."""
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def build_sessions(rows):
    """Group checkpoint verdicts into the sessions they belong to.

    Returns a list of session dicts: {start, end, duration, checkpoints:[...]}.
    Checkpoints that arrive with no open session (e.g. a truncated ledger) are
    attached to a trailing catch-all session so nothing is silently dropped.
    """
    sessions = []
    current = None
    for row in rows:
        t = row.get("type")
        if t == "session_open":
            current = {"start": row.get("t", ""), "end": "", "duration": "", "checkpoints": []}
            sessions.append(current)
        elif t == "session_close":
            if current is None:
                current = {"start": "", "end": "", "duration": "", "checkpoints": []}
                sessions.append(current)
            current["end"] = row.get("t", "")
            current["duration"] = row.get("duration", "")
            current = None
        elif t == "checkpoint":
            if current is None:
                current = {"start": row.get("t", ""), "end": "", "duration": "", "checkpoints": []}
                sessions.append(current)
            v = dict(row.get("verdict", {}))
            v["t"] = row.get("t", "")
            v["minutes"] = row.get("minutes", 0)
            current["checkpoints"].append(v)
    return sessions


def latest_goals(rows):
    """The most recent goals line in the ledger (the day's stated goals)."""
    goals = ""
    for row in rows:
        if row.get("type") == "goals":
            goals = row.get("goals", "")
    return goals


def category_minutes(sessions):
    """Factual minutes per category, summed straight from the checkpoint verdicts."""
    totals = defaultdict(float)
    for s in sessions:
        for c in s["checkpoints"]:
            totals[c.get("category", "admin")] += c.get("minutes", 0) or 0
    return dict(totals)


def all_verdicts(sessions):
    """Flat list of verdict dicts for the whole-day synthesis prompt."""
    out = []
    for s in sessions:
        out.extend(s["checkpoints"])
    return out


def meta_for(category):
    return CATEGORY_META.get(category, UNKNOWN_META)


def build_report(path=LEDGER_PATH):
    """Assemble everything the template needs: facts from the ledger + Gemma's synthesis."""
    rows = load_rows(path)
    goals = latest_goals(rows)
    sessions = build_sessions(rows)
    verdicts = all_verdicts(sessions)

    cat_min = category_minutes(sessions)
    total_min = sum(cat_min.values())
    categories = [
        {
            "key": k,
            "label": meta_for(k)["label"],
            "color": meta_for(k)["color"],
            "minutes": round(v),
            "pct": round(100 * v / total_min) if total_min else 0,
        }
        for k, v in sorted(cat_min.items(), key=lambda kv: -kv[1])
    ]

    # Gemma call site #2 — synthesize the whole day. Degrade gracefully if it fails.
    try:
        synthesis = gemma.daily_report(goals, verdicts) if verdicts else {}
    except Exception as e:
        synthesis = {"headline": f"(Gemma synthesis unavailable: {e})",
                     "goal_progress": [], "drift": [], "tomorrow": []}

    return {
        "goals": goals,
        "sessions": sessions,
        "categories": categories,
        "total_min": round(total_min),
        "meta_for": meta_for,
        "synthesis": synthesis,
    }


def render(data, out_path=OUT_PATH):
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=True)
    env.filters["catlabel"] = lambda k: meta_for(k)["label"]
    env.filters["catcolor"] = lambda k: meta_for(k)["color"]
    html = env.get_template(TEMPLATE_NAME).render(**data)
    with open(out_path, "w") as f:
        f.write(html)
    return out_path


def main():
    ap = argparse.ArgumentParser(description="Render the FocusLedger end-of-day report.")
    ap.add_argument("ledger", nargs="?", default=LEDGER_PATH, help="path to ledger.jsonl")
    ap.add_argument("--out", default=OUT_PATH, help="output HTML path")
    ap.add_argument("--open", action="store_true", help="open the report in a browser")
    ap.add_argument("--json", action="store_true", help="also print the synthesis JSON")
    args = ap.parse_args()

    if not os.path.exists(args.ledger):
        raise SystemExit(f"No ledger at {args.ledger}. Run focusledger.py first, or seed.py for a demo.")

    data = build_report(args.ledger)
    if args.json:
        print(json.dumps(data["synthesis"], indent=2))
    out = render(data, args.out)
    print(f"📄 wrote {out}  ({len(data['sessions'])} sessions, {data['total_min']} min tracked)")
    if args.open:
        webbrowser.open("file://" + os.path.abspath(out))


if __name__ == "__main__":
    main()
