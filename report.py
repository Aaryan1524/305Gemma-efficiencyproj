"""End-of-day report: ledger.jsonl -> HTML.

Reads only the ledger (verdicts and session markers — never pixels or raw
text), reconstructs the day's sessions, computes factual time-by-category,
and asks Gemma to synthesize the whole day. That synthesis is Gemma call
site #2: not classification, but reflection across everything at once —
the thing no rules engine can do.
"""

import argparse
import fcntl
import tempfile
from datetime import date, datetime
import json
import os
import webbrowser
from collections import defaultdict

from jinja2 import Environment, FileSystemLoader

import gemma
import paths
from typography import report_font_css
import report_metrics as facts

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


def build_report(path=LEDGER_PATH, day=None, demo=False, offline=False, live=False):
    """One day's deterministic facts and an optional, clearly separate AI reflection."""
    all_rows = facts.dated_rows(load_rows(path))
    selected = day or (all_rows[-1]['date'] if all_rows else date.today().isoformat())
    date.fromisoformat(selected)
    data = facts.metrics(all_rows, selected)
    rows = data['rows']
    normalized = iter(data['items'])
    # Keep session markers while dropping corrupt checkpoints.
    # Reconstruct with normalized checkpoint verdicts in their original order.
    session_rows = []
    for row in rows:
        if row.get('type') == 'checkpoint':
            if not isinstance(row.get('verdict'), dict) or not facts.number(row.get('minutes')):
                continue
            session_rows.append(next(normalized))
        else:
            session_rows.append(row)
    sessions = build_sessions(session_rows)
    verdicts = all_verdicts(sessions)
    goals = latest_goals(rows)
    cat_min = category_minutes(sessions)
    categories = [{'key': key, 'label': meta_for(key)['label'], 'color': meta_for(key)['color'],
                   'minutes': _disp_min(value), 'pct': round(100 * value / data['total']) if data['total'] else 0}
                  for key, value in sorted(cat_min.items(), key=lambda item: -item[1]) if value > 0]
    synthesis = {'headline': 'Your day so far' if live else ('A sample day, by the numbers' if demo else 'Recorded activity, by the numbers')}
    if not verdicts:
        synthesis = {'headline': 'No classified activity yet.'}
    elif not offline:
        try:
            synthesis = gemma.daily_report(goals, verdicts)
        except Exception:
            synthesis = {'headline': 'Gemma synthesis unavailable; showing recorded activity only'}
    checkpoint_label = data['last_checkpoint']
    if checkpoint_label and 'T' in checkpoint_label:
        checkpoint_label = checkpoint_label.split('T', 1)[1][:5]
    elif checkpoint_label:
        checkpoint_label = checkpoint_label[:5]
    day_date = date.fromisoformat(selected)
    return {**data, 'demo': demo, 'offline': offline, 'live': live,
            'report_date': f'{day_date:%A, %B} {day_date.day}', 'model_name': gemma.get_model(),
            'intentions': goals, 'goal_allocations': data['goals'], 'sessions': sessions,
            'categories': categories, 'total_min': _disp_min(data['total']),
            'total_label': facts.duration(data['total']), 'deep_label': facts.duration(data['deep']),
            'drift_label': facts.duration(data['drift']), 'aligned_label': facts.duration(data['aligned']),
            'aligned_min': _disp_min(data['aligned']), 'deep_min': _disp_min(data['deep']),
            'drift_min': _disp_min(data['drift']), 'focus_score': data['score'],
            'last_checkpoint_label': checkpoint_label, 'generated_at': datetime.now().strftime('%H:%M'),
            'synthesis': synthesis, 'meta_for': meta_for}


def render(data, out_path=OUT_PATH):
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=True)
    env.filters["duration"] = facts.duration
    env.filters["catlabel"] = lambda k: meta_for(k)["label"]
    env.filters["catcolor"] = lambda k: meta_for(k)["color"]
    html = env.get_template(TEMPLATE_NAME).render(font_css=report_font_css(), **data)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=os.path.dirname(os.path.abspath(out_path)), delete=False) as output:
            temp_path = output.name
            output.write(html)
        os.replace(temp_path, out_path)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
    return out_path


def write_report(path=LEDGER_PATH, out_path=OUT_PATH, **options):
    """Serialize publishers and atomically replace HTML; browsers never read half a page."""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(str(out_path) + '.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return render(build_report(path, **options), out_path)


def refresh_live(path=LEDGER_PATH, day=None, live=True):
    return write_report(path, OUT_PATH, day=day, offline=True, live=live)


def main():
    ap = argparse.ArgumentParser(description="Render the FocusLedger end-of-day report.")
    ap.add_argument("ledger", nargs="?", default=LEDGER_PATH, help="path to ledger.jsonl")
    ap.add_argument("--out", default=OUT_PATH, help="output HTML path")
    ap.add_argument("--open", action="store_true", help="open the report in a browser")
    ap.add_argument("--json", action="store_true", help="also print the synthesis JSON")
    ap.add_argument("--date", help="report day (YYYY-MM-DD); defaults to latest recorded day")
    ap.add_argument("--live", action="store_true", help="refresh the open local report every 30 seconds")
    ap.add_argument("--demo", action="store_true", help="label synthetic data")
    ap.add_argument("--offline", action="store_true", help="skip model synthesis")
    args = ap.parse_args()

    if not os.path.exists(args.ledger):
        raise SystemExit(f"No ledger at {args.ledger}. Run focusledger.py first, or seed.py for a demo.")

    try:
        if args.date:
            date.fromisoformat(args.date)
    except ValueError:
        ap.error('--date must be YYYY-MM-DD')
    out = write_report(args.ledger, args.out, day=args.date, demo=args.demo,
                       offline=args.offline, live=args.live and not args.demo and not args.date)
    if args.json:
        print(json.dumps(build_report(args.ledger, day=args.date, offline=True)['synthesis'], indent=2))
    print(f'📄 wrote {out}')
    if args.open:
        if not webbrowser.open("file://" + os.path.abspath(out)):
            raise SystemExit("Report saved, but the browser could not be opened.")


if __name__ == "__main__":
    main()
