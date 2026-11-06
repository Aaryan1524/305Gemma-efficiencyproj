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
import paths

BASE = os.path.dirname(os.path.abspath(__file__))
LEDGER_PATH = paths.LEDGER_PATH
TEMPLATE_DIR = os.path.join(BASE, "templates")
TEMPLATE_NAME = "report.html"
OUT_PATH = paths.REPORT_PATH

# Category → display label + color. Colors are CSS variables defined in the
# template (Apple system colors, with light and dark variants).
CATEGORY_META = {
    "deep_work":     {"label": "Deep work",     "color": "var(--blue)"},
    "learning":      {"label": "Learning",      "color": "var(--purple)"},
    "communication": {"label": "Communication", "color": "var(--green)"},
    "admin":         {"label": "Admin",         "color": "var(--gray)"},
    "drift":         {"label": "Drift",         "color": "var(--orange)"},
}
UNKNOWN_META = {"label": "Other", "color": "var(--gray)"}


def load_rows(path):
    """Parse the ledger into a list of dicts, skipping any unparseable lines."""
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                if isinstance(row, dict):
                    rows.append(row)
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


def _disp_min(val):
    """Ensure positive sub-minute durations (e.g. 0.5m in Quick Test) display as at least 1m."""
    return max(1, round(val)) if val > 0 else 0


def build_report(path=LEDGER_PATH, day=None, demo=False, offline=False):
    """Assemble everything the template needs: facts from the ledger + Gemma's synthesis."""
    rows = paths.rows_for_day(load_rows(path), day)
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
            "minutes": _disp_min(v),
            "pct": max(1, round(100 * v / total_min)) if total_min else 0,
        }
        for k, v in sorted(cat_min.items(), key=lambda kv: -kv[1])
        if v > 0
    ]

    # Cognitive Metrics (WHOOP-style Trio & Health Monitors)
    cognitive_strain = min(21.0, round((total_min / 60) * 2.2 + (aligned_min / 60) * 1.8, 1)) if total_min else 0.0
    if total_min > 0 and cognitive_strain == 0.0:
        cognitive_strain = 0.5

    # Context switching / Friction index (unique activities / hour)
    unique_acts = set(c.get("activity", "") for s in sessions for c in s["checkpoints"] if c.get("activity"))
    switches_per_hr = round(len(unique_acts) / max(0.5, total_min / 60), 1) if total_min else 0.0

    # Longest Deep Work Flow Streak (contiguous aligned checkpoints)
    max_streak_min = 0
    curr_streak = 0
    for s in sessions:
        curr_streak = 0
        for c in s["checkpoints"]:
            if c.get("aligned"):
                curr_streak += c.get("minutes", 30) or 30
                if curr_streak > max_streak_min:
                    max_streak_min = curr_streak
            else:
                curr_streak = 0
    max_streak_min = _disp_min(max_streak_min)

    # Peak Productivity Window
    peak_window = "N/A"
    if sessions and verdicts:
        best_sess = max(sessions, key=lambda s: sum(1 for c in s["checkpoints"] if c.get("aligned")))
        if best_sess.get("start") and best_sess.get("end"):
            peak_window = f"{best_sess['start']} – {best_sess['end']}"
        elif best_sess.get("start"):
            peak_window = f"{best_sess['start']} – active"

    # Gemma call site #2 — synthesize the whole day. Degrade gracefully if it fails.
    try:
        synthesis = gemma.daily_report(goals, verdicts) if verdicts and not offline else {}
    except Exception as e:
        synthesis = {
            "headline": "Gemma synthesis unavailable; showing recorded activity only",
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

    from datetime import datetime
    stamp = next((row.get("date") for row in reversed(rows) if row.get("type") == "goals" and row.get("date")), None)
    try:
        day = datetime.strptime(stamp, "%Y-%m-%d") if stamp else datetime.now()
    except ValueError:
        day = datetime.now()

    return {
        "demo": demo,
        "offline": offline,
        "report_date": f"{day:%A, %B} {day.day}",
        "model_name": gemma.get_model(),
        "goals": goals,
        "sessions": sessions,
        "categories": categories,
        "total_min": _disp_min(total_min),
        "aligned_min": _disp_min(aligned_min),
        "drift_min": _disp_min(cat_min.get("drift", 0)),
        "deep_min": _disp_min(cat_min.get("deep_work", 0)),
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
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w") as f:
        f.write(html)
    return out_path


def main():
    ap = argparse.ArgumentParser(description="Render the FocusLedger end-of-day report.")
    ap.add_argument("ledger", nargs="?", default=LEDGER_PATH, help="path to ledger.jsonl")
    ap.add_argument("--out", default=OUT_PATH, help="output HTML path")
    ap.add_argument("--open", action="store_true", help="open the report in a browser")
    ap.add_argument("--json", action="store_true", help="also print the synthesis JSON")
    ap.add_argument("--date", help="report day (YYYY-MM-DD); defaults to latest recorded day")
    ap.add_argument("--demo", action="store_true", help="label synthetic data")
    ap.add_argument("--offline", action="store_true", help="skip model synthesis")
    args = ap.parse_args()

    if not os.path.exists(args.ledger):
        raise SystemExit(f"No ledger at {args.ledger}. Run focusledger.py first, or seed.py for a demo.")

    data = build_report(args.ledger, day=args.date, demo=args.demo, offline=args.offline)
    if args.json:
        print(json.dumps(data["synthesis"], indent=2))
    out = render(data, args.out)
    print(f"📄 wrote {out}  ({len(data['sessions'])} sessions, {data['total_min']} min tracked)")
    if args.open:
        if not webbrowser.open("file://" + os.path.abspath(out)):
            raise SystemExit("Report saved, but the browser could not be opened.")


if __name__ == "__main__":
    main()
