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
# Dark-surface categorical palette, validated (lightness band, chroma floor,
# CVD separation, contrast ≥3:1 on #14191f).
CATEGORY_META = {
    "deep_work":     {"label": "Deep work",     "color": "#D8C3A5"}, # Warm nude / almond
    "learning":      {"label": "Learning",      "color": "#C5A880"}, # Soft warm camel
    "communication": {"label": "Communication", "color": "#9E7B66"}, # Soft brown / mocha
    "admin":         {"label": "Admin",         "color": "#8C8275"}, # Warm taupe
    "drift":         {"label": "Drift",         "color": "#A05A4E"}, # Muted terracotta rust
}
UNKNOWN_META = {"label": "Other", "color": "#8C8275"}


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

    # Focus Score: share of tracked time whose verdict was aligned with a goal.
    aligned_min = sum((c.get("minutes", 0) or 0)
                      for s in sessions for c in s["checkpoints"] if c.get("aligned"))
    focus_score = round(100 * aligned_min / total_min) if total_min else 0

    categories = [
        {
            "key": k,
            "label": meta_for(k)["label"],
            "color": meta_for(k)["color"],
            "minutes": round(v),
            "pct": round(100 * v / total_min) if total_min else 0,
        }
        for k, v in sorted(cat_min.items(), key=lambda kv: -kv[1])
        if round(v) > 0
    ]

    # Cognitive Metrics (WHOOP-style Trio & Health Monitors)
    cognitive_strain = min(21.0, round((total_min / 60) * 2.2 + (aligned_min / 60) * 1.8, 1)) if total_min else 0.0

    # Context switching / Friction index (unique activities / hour)
    unique_acts = set(c.get("activity", "") for s in sessions for c in s["checkpoints"] if c.get("activity"))
    switches_per_hr = round(len(unique_acts) / max(0.5, total_min / 60), 1) if total_min else 0.0

    # Longest Deep Work Flow Streak (contiguous aligned checkpoints)
    max_streak_min = 0
    curr_streak = 0
    for s in sessions:
        for c in s["checkpoints"]:
            if c.get("aligned"):
                curr_streak += c.get("minutes", 30) or 30
                if curr_streak > max_streak_min:
                    max_streak_min = curr_streak
            else:
                curr_streak = 0
    max_streak_min = round(max_streak_min)

    # Peak Productivity Window
    peak_window = "N/A"
    if sessions and verdicts:
        best_sess = max(sessions, key=lambda s: sum(1 for c in s["checkpoints"] if c.get("aligned")))
        if best_sess.get("start") and best_sess.get("end"):
            peak_window = f"{best_sess['start']} – {best_sess['end']}"

    # Gemma call site #2 — synthesize the whole day. Degrade gracefully if it fails.
    try:
        synthesis = gemma.daily_report(goals, verdicts) if verdicts else {}
    except Exception as e:
        synthesis = {
            "headline": f"(Gemma synthesis unavailable: {e})",
            "coach_verdict": "Cognitive report synthesis could not be completed.",
            "tactical_rule": "Maintain focused blocks without multi-tasking.",
            "goal_progress": [], "drift": [], "tomorrow": []
        }

    # Goal Completion Rate %
    goal_prog = synthesis.get("goal_progress", [])
    if goal_prog:
        comp_count = sum(1 for g in goal_prog if any(w in g.get("verdict", "").lower() for w in ["done", "complete", "achieved", "met", "finished", "good", "yes"]))
        goal_completion = round(100 * comp_count / len(goal_prog)) if comp_count else min(100, round(focus_score * 1.1))
    else:
        goal_completion = focus_score

    max_cat_min = max((c["minutes"] for c in categories), default=0)

    return {
        "goals": goals,
        "sessions": sessions,
        "categories": categories,
        "total_min": round(total_min),
        "aligned_min": round(aligned_min),
        "drift_min": round(cat_min.get("drift", 0)),
        "deep_min": round(cat_min.get("deep_work", 0)),
        "focus_score": focus_score,
        "cognitive_strain": cognitive_strain,
        "goal_completion": goal_completion,
        "switches_per_hr": switches_per_hr,
        "max_streak_min": max_streak_min,
        "peak_window": peak_window,
        "max_cat_min": max_cat_min,
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
