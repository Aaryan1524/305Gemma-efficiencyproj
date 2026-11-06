"""Deterministic report facts from ledger verdicts; no model or capture calls."""
from collections import defaultdict
from datetime import date, datetime, timedelta
import math

from goals import day_plan, split_goals


def dated_rows(rows):
    day = None
    result = []
    for row in rows:
        if row.get('date'):
            try:
                day = date.fromisoformat(row['date']).isoformat()
            except (ValueError, TypeError):
                continue
        if day:
            result.append({**row, 'date': day})
    return result


def number(value):
    if isinstance(value, bool):
        return 0.0
    try:
        result = float(value)
        return result if math.isfinite(result) and 0 < result <= 1440 else 0.0
    except (ValueError, TypeError):
        return 0.0


def duration(minutes):
    if not minutes:
        return '0m'
    if minutes < 1:
        return '<1m'
    minutes = round(minutes)
    hours, rest = divmod(minutes, 60)
    return (f'{hours}h' + (f' {rest}m' if rest else '')) if hours else f'{minutes}m'


def checkpoints(rows):
    """Normalize inconsistent verdicts without counting 'none' as a stated goal."""
    goals = []
    result = []
    for row in rows:
        if row.get('type') == 'goals':
            goals = split_goals(row.get('goals', ''))
        if row.get('type') != 'checkpoint' or not isinstance(row.get('verdict'), dict):
            continue
        minutes = number(row.get('minutes'))
        if not minutes:
            continue
        verdict = row['verdict']
        goal = next((g for g in goals if g.casefold() == str(verdict.get('goal', '')).casefold()), None)
        category = verdict.get('category')
        if category not in ('deep_work', 'learning', 'communication', 'admin', 'drift'):
            category = 'other'
        result.append({**row, 'minutes': minutes, 'verdict': {**verdict, 'category': category, 'goal': goal or 'none',
                       'aligned': verdict.get('aligned') is True and goal is not None}})
    return result


def totals(rows):
    items = checkpoints(rows)
    total = sum(r['minutes'] for r in items)
    aligned = sum(r['minutes'] for r in items if r['verdict']['aligned'])
    deep = sum(r['minutes'] for r in items if r['verdict'].get('category') == 'deep_work')
    return {'total': total, 'aligned': aligned, 'deep': deep, 'drift': total - aligned,
            'score': round(aligned / total * 100) if total else None, 'items': items}


def _local(value):
    try:
        dt = datetime.fromisoformat(value)
        return dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt
    except (ValueError, TypeError):
        return None


def _clock(value):
    try:
        h, m, s = map(int, value.split(':'))
        return h * 60 + m + s / 60 if 0 <= h <= 24 and 0 <= m < 60 and 0 <= s < 60 else None
    except (ValueError, TypeError, AttributeError):
        return None


def timeline(rows, selected_day):
    """24h bins retain real gaps; legacy bounds are approximate and labeled."""
    start = datetime.combine(date.fromisoformat(selected_day), datetime.min.time())
    bins = [{'aligned': 0.0, 'drift': 0.0} for _ in range(48)]
    intervals = []
    legacy = False
    previous = 0.0
    for row in checkpoints(rows):
        lo, hi = _local(row.get('start_at')), _local(row.get('end_at'))
        if lo is not None and hi is not None and hi > lo:
            a, b = (lo - start).total_seconds() / 60, (hi - start).total_seconds() / 60
        else:
            end = _clock(row.get('t'))
            if end is None:
                continue
            if end + 720 < previous:
                end += 1440
            previous = end
            a, b = end - row['minutes'], end
            legacy = True
        a, b = max(0, a), min(1440, b)
        if b > a:
            intervals.append((a, b, 'aligned' if row['verdict']['aligned'] else 'drift'))
    # Never double-color overlapping intervals in edited/legacy ledgers.
    boundaries = sorted({point for a, b, _ in intervals for point in (a, b)})
    for a, b in zip(boundaries, boundaries[1:]):
        state = next((state for lo, hi, state in reversed(intervals) if lo <= a and hi >= b), None)
        if state is None:
            continue
        for index in range(max(0, int(a // 30)), min(48, math.ceil(b / 30))):
            bins[index][state] += max(0, min(b, (index + 1) * 30) - max(a, index * 30))
    occupied = [i for i, item in enumerate(bins) if item['aligned'] + item['drift'] > 0]
    if not occupied:
        return {'bins': [], 'ticks': [], 'legacy': legacy, 'timed_min': 0}
    # Whole-hour boundaries with at least six hours of context, clamped to the day.
    first = max(0, (occupied[0] // 2) * 2)
    last = min(48, ((occupied[-1] // 2) + 1) * 2)
    if last - first < 12:
        last = min(48, first + 12)
        first = max(0, min(first, last - 12))
    result = []
    for i in range(first, last):
        item = bins[i]
        aligned_pct, drift_pct = item['aligned'] / 30 * 100, item['drift'] / 30 * 100
        label = f'{i // 2:02d}:{(i % 2) * 30:02d}'
        result.append({**item, 'label': label, 'aligned_pct': aligned_pct, 'classified_pct': aligned_pct + drift_pct,
                       'description': f"{label}: {duration(item['aligned'])} aligned, {duration(item['drift'])} drift, {duration(max(0, 30 - item['aligned'] - item['drift']))} without classified data"})
    tick_positions = sorted({first, (first + last) // 2, last})
    return {'bins': result, 'ticks': [{'label': f'{i // 2:02d}:{(i % 2) * 30:02d}', 'position': (i - first) / (last - first) * 100} for i in tick_positions],
            'legacy': legacy, 'timed_min': sum(b['aligned'] + b['drift'] for b in result)}


def metrics(all_rows, selected_day):
    all_rows = dated_rows(all_rows)
    rows = [r for r in all_rows if r['date'] == selected_day]
    current = totals(rows)
    groups = defaultdict(list)
    for row in all_rows:
        if row['date'] < selected_day:
            groups[row['date']].append(row)
    prior = []
    for day in sorted(groups, reverse=True):
        item = totals(groups[day])
        if item['total']:
            prior.append({'date': day, 'score': item['score']})
        if len(prior) == 7:
            break
    baseline = sum(p['score'] for p in prior) / len(prior) if prior else None
    goals = day_plan(rows)
    goal_totals = defaultdict(float)
    for row in current['items']:
        if row['verdict']['aligned']:
            goal_totals[row['verdict']['goal']] += row['minutes']
    for goal in goals:
        actual = goal_totals[goal['goal']]
        target = goal['target_minutes']
        goal.update({'actual_minutes': actual, 'actual_label': duration(actual),
                     'target_label': duration(target) if target else None,
                     'bar_pct': min(100, actual / (target or current['aligned'] or 1) * 100),
                     'over_minutes': max(0, actual - target) if target else 0})
    updated = next((r.get('recorded_at') or r.get('end_at') or r.get('t') for r in reversed(current['items'])), None)
    return {**current, 'rows': rows, 'goals': goals, 'baseline': baseline, 'baseline_count': len(prior),
            'delta': round(current['score'] - baseline) if baseline is not None and current['score'] is not None else None,
            'history': list(reversed(prior)), 'timeline': timeline(rows, selected_day), 'last_checkpoint': updated,
            'selected_day': selected_day}
