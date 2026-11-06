"""Generate a deterministic synthetic week and day without reading personal history."""
import argparse
from datetime import datetime, timedelta
import json

GOAL_NAMES = ['Build FocusLedger', 'Study calculus', 'Keep communication intentional']
GOALS = '; '.join(GOAL_NAMES)


def checkpoint(start, minutes, category, goal):
    end = start + timedelta(minutes=minutes)
    return {'type': 'checkpoint', 'date': start.date().isoformat(), 't': end.strftime('%H:%M:%S'),
            'start_at': start.isoformat(), 'end_at': end.isoformat(), 'recorded_at': end.isoformat(),
            'minutes': minutes, 'samples': max(1, minutes * 6),
            'verdict': {'activity': {'deep_work': 'Building the local report', 'learning': 'Working through calculus problems', 'drift': 'Browsing unrelated feeds'}[category],
                        'category': category, 'goal': goal or 'none', 'aligned': goal is not None,
                        'confidence': 'high', 'note': 'Illustrative activity generated for the sample report.'}}


def sample_rows():
    rows = []
    today = datetime(2026, 7, 24, 9)
    # Seven distinct prior recorded days, averaging 74% alignment.
    for offset, score in zip(range(7, 0, -1), [70, 78, 72, 75, 74, 76, 73]):
        start = today - timedelta(days=offset)
        day = start.date().isoformat()
        rows.extend([{'type': 'goals', 'date': day, 'goals': GOALS}, {'type': 'session_open', 'date': day, 't': '09:00:00'}])
        for category, remaining, goal in [('deep_work', 180, GOAL_NAMES[0]), ('learning', score * 3 - 180, GOAL_NAMES[1]), ('drift', 300 - score * 3, None)]:
            while remaining > 0:
                minutes = min(30, remaining)
                rows.append(checkpoint(start, minutes, category, goal))
                start += timedelta(minutes=minutes)
                remaining -= minutes
        rows.append({'type': 'session_close', 'date': day, 't': '14:00:00', 'duration': '5h'})
    day = today.date().isoformat()
    rows.append({'type': 'goals', 'date': day, 'goals': GOALS,
                 'goal_plans': [{'goal': name, 'target_minutes': minutes} for name, minutes in zip(GOAL_NAMES, [180, 120, 30])]})
    for hour, kinds in [(9, ['deep_work', 'deep_work', 'drift', 'learning', 'deep_work']),
                        (13, ['learning', 'deep_work', 'drift', 'deep_work', 'deep_work', 'learning'])]:
        start = today.replace(hour=hour)
        rows.append({'type': 'session_open', 'date': day, 't': start.strftime('%H:%M:%S')})
        for kind in kinds:
            goal = GOAL_NAMES[0] if kind == 'deep_work' else GOAL_NAMES[1] if kind == 'learning' else None
            rows.append(checkpoint(start, 30, kind, goal))
            start += timedelta(minutes=30)
        rows.append({'type': 'session_close', 'date': day, 't': start.strftime('%H:%M:%S'), 'duration': '2h 30m' if hour == 9 else '3h'})
    rows.append({'type': 'goal_status', 'date': day, 'goal': GOAL_NAMES[0], 'completed': True})
    return rows


def write_rows(rows, path):
    with open(path, 'w') as output:
        for row in rows:
            output.write(json.dumps(row) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', default='ledger.sample.jsonl')
    args = parser.parse_args()
    write_rows(sample_rows(), args.out)
    print(f'Synthetic sample written to {args.out}')


if __name__ == '__main__':
    main()
