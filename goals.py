"""Goal names, optional time budgets, and explicit user completion state."""
import re


def split_goals(text):
    if not isinstance(text, str):
        return []
    parts = [part.strip(' .,-') for part in re.split(r'[;\n,]|\band then\b|\band\b', text) if part.strip(' .,-')]
    return list(dict.fromkeys(parts))[:6] or ([text.strip()] if text.strip() else [])


def parse_target(value):
    text = str(value).strip()
    if not text:
        return None
    if not text.isdigit() or not 1 <= int(text) <= 1440:
        raise ValueError('Use whole minutes from 1 to 1440, or leave the target blank.')
    return int(text)


def day_plan(rows):
    """The latest morning intentions plus user-reported status, never AI completion."""
    latest = next((r for r in reversed(rows) if r.get('type') == 'goals'), {})
    names = split_goals(latest.get('goals', ''))
    targets = {}
    for item in latest.get('goal_plans', []) if isinstance(latest.get('goal_plans', []), list) else []:
        if not isinstance(item, dict) or item.get('goal') not in names:
            continue
        try:
            targets[item['goal']] = parse_target(item.get('target_minutes') or '')
        except ValueError:
            continue
    completed = {}
    # A fresh goals row starts a fresh plan/status assessment, preserving old history.
    start = next((i for i in range(len(rows) - 1, -1, -1) if rows[i].get('type') == 'goals'), -1)
    for row in rows[start + 1:]:
        if row.get('type') == 'goal_status' and row.get('goal') in names and isinstance(row.get('completed'), bool):
            completed[row['goal']] = row['completed']
    return [{'goal': name, 'target_minutes': targets.get(name), 'completed': completed.get(name, False)} for name in names]
