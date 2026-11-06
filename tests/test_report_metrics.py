import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta

import goals
import report
import report_metrics as metrics
import seed


class ReportMetricsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ledger = self.root / 'ledger.jsonl'
        self.out = self.root / 'report.html'
        self.rows = seed.sample_rows()
        seed.write_rows(self.rows, self.ledger)

    def test_sample_overview_and_personal_baseline(self):
        data = report.build_report(self.ledger, offline=True)
        self.assertEqual(data['focus_score'], 82)
        self.assertEqual(data['deep_min'], 180)
        self.assertEqual(data['drift_min'], 60)
        self.assertEqual(data['total_min'], 330)
        self.assertEqual(data['baseline_count'], 7)
        self.assertEqual(data['baseline'], 74)
        self.assertEqual(data['delta'], 8)
        self.assertEqual(data['goal_allocations'][0]['actual_minutes'], 180)
        self.assertTrue(data['goal_allocations'][0]['completed'])
        self.assertFalse(data['goal_allocations'][1]['completed'])

    def test_timeline_keeps_lunch_gap_and_partial_bins(self):
        data = report.build_report(self.ledger, offline=True)
        bins = {b['label']: b for b in data['timeline']['bins']}
        self.assertEqual(bins['12:00']['aligned'] + bins['12:00']['drift'], 0)
        self.assertEqual(data['timeline']['timed_min'], 330)
        row = seed.checkpoint(datetime(2026, 7, 24, 9, 10), 10, 'deep_work', seed.GOAL_NAMES[0])
        timeline = metrics.timeline([{'type': 'goals', 'goals': seed.GOALS}, row], '2026-07-24')
        self.assertEqual(timeline['bins'][0]['aligned'], 10)
        self.assertAlmostEqual(timeline['bins'][0]['classified_pct'], 100/3)

    def test_overlap_is_not_double_counted_in_timeline(self):
        row = seed.checkpoint(datetime(2026, 7, 24, 9), 30, 'deep_work', seed.GOAL_NAMES[0])
        replacement = seed.checkpoint(datetime(2026, 7, 24, 9, 15), 30, 'drift', None)
        timeline = metrics.timeline([{'type': 'goals', 'goals': seed.GOALS}, row, replacement], '2026-07-24')
        self.assertEqual(timeline['timed_min'], 45)
        self.assertEqual(sum(b['aligned'] for b in timeline['bins']), 15)
        self.assertEqual(sum(b['drift'] for b in timeline['bins']), 30)

    def test_legacy_intervals_are_labeled_and_missing_time_is_not_invented(self):
        rows = [{'type': 'goals', 'goals': 'Write report'},
                {'type': 'checkpoint', 't': '10:30:00', 'minutes': 20, 'verdict': {'goal': 'Write report', 'aligned': True}}]
        chart = metrics.timeline(rows, '2026-07-24')
        self.assertTrue(chart['legacy'])
        self.assertEqual(chart['timed_min'], 20)
        rows[-1]['t'] = 'invalid'
        self.assertFalse(metrics.timeline(rows, '2026-07-24')['bins'])

    def test_previous_recorded_days_exclude_empty_future_and_selected_day(self):
        self.rows.extend([{'type': 'goals', 'date': '2026-07-25', 'goals': seed.GOALS},
                          seed.checkpoint(datetime(2026, 7, 25, 9), 30, 'drift', None),
                          {'type': 'goals', 'date': '2026-07-16', 'goals': seed.GOALS}])
        seed.write_rows(self.rows, self.ledger)
        data = report.build_report(self.ledger, day='2026-07-24', offline=True)
        self.assertEqual(data['baseline_count'], 7)
        self.assertEqual(data['baseline'], 74)
        seed.write_rows([r for r in self.rows if r.get('date') == '2026-07-24'], self.ledger)
        self.assertIsNone(report.build_report(self.ledger, offline=True)['delta'])

    def test_empty_day_is_unknown_not_zero_performance(self):
        data = report.build_report(self.ledger, day='2026-07-26', offline=True)
        self.assertIsNone(data['focus_score'])
        self.assertFalse(data['timeline']['bins'])
        report.render(data, self.out)
        self.assertTrue('Waiting for the first checkpoint' in self.out.read_text())

    def test_none_and_unknown_goal_do_not_inflate_alignment(self):
        rows = [{'type': 'goals', 'goals': 'Write report'},
                {'type': 'checkpoint', 'minutes': 30, 'verdict': {'goal': 'none', 'aligned': True}},
                {'type': 'checkpoint', 'minutes': 30, 'verdict': {'goal': 'Imagined goal', 'aligned': True}}]
        data = metrics.totals(rows)
        self.assertEqual(data['aligned'], 0)
        self.assertEqual(data['drift'], 60)

    def test_invalid_minutes_and_verdicts_are_skipped(self):
        rows = [{'type': 'goals', 'goals': 'Write report'}]
        rows += [{'type': 'checkpoint', 'minutes': n, 'verdict': {'goal': 'Write report', 'aligned': True}} for n in [-1, float('nan'), float('inf'), 'bad', None, True]]
        rows.append({'type': 'checkpoint', 'minutes': 30, 'verdict': []})
        self.assertEqual(metrics.totals(rows)['total'], 0)

    def test_targets_overrun_and_completion_remain_independent(self):
        self.rows[ next(i for i in range(len(self.rows)-1,-1,-1) if self.rows[i]['type']=='goals') ]['goal_plans'][0]['target_minutes'] = 60
        self.rows = [r for r in self.rows if r['type'] != 'goal_status']
        seed.write_rows(self.rows, self.ledger)
        goal = report.build_report(self.ledger, offline=True)['goal_allocations'][0]
        self.assertEqual(goal['bar_pct'], 100)
        self.assertEqual(goal['over_minutes'], 120)
        self.assertFalse(goal['completed'])
        self.rows.append({'type': 'goal_status', 'date': '2026-07-24', 'goal': seed.GOAL_NAMES[0], 'completed': True})
        self.rows.append({'type': 'goal_status', 'date': '2026-07-24', 'goal': seed.GOAL_NAMES[0], 'completed': False})
        seed.write_rows(self.rows, self.ledger)
        self.assertFalse(report.build_report(self.ledger, offline=True)['goal_allocations'][0]['completed'])

    def test_budget_validation(self):
        self.assertIsNone(goals.parse_target(''))
        self.assertEqual(goals.parse_target('90'), 90)
        for invalid in ['0', '-5', '1.5', 'tomorrow', '1441']:
            with self.assertRaises(ValueError):
                goals.parse_target(invalid)

    def test_live_refresh_is_offline_and_frozen_exports_do_not_reload(self):
        with patch.object(report, 'OUT_PATH', str(self.out)), patch.object(report.gemma, 'daily_report') as model:
            report.refresh_live(str(self.ledger))
            model.assert_not_called()
        self.assertTrue('window.location.reload()' in self.out.read_text())
        report.write_report(self.ledger, self.out, offline=True, demo=True)
        self.assertFalse('window.location.reload()' in self.out.read_text())
        self.assertTrue('synthetic activity only' in self.out.read_text())

    def test_atomic_publication_failure_preserves_previous_report(self):
        self.out.write_text('previous report')
        data = report.build_report(self.ledger, offline=True)
        with patch.object(report.os, 'replace', side_effect=OSError):
            with self.assertRaises(OSError):
                report.render(data, self.out)
        self.assertEqual(self.out.read_text(), 'previous report')
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['ledger.jsonl', 'report.html'])

    def test_corrupt_json_and_html_are_safe_in_new_sections(self):
        rows = [{'type': 'goals', 'date': '2026-07-24', 'goals': '<script>alert(1)</script>', 'goal_plans': [{'goal':'<script>alert(1)</script>', 'target_minutes':30}]}]
        seed.write_rows(rows, self.ledger)
        with self.ledger.open('a') as output:
            output.write('broken\nnull\n')
        report.write_report(self.ledger, self.out, offline=True)
        html = self.out.read_text()
        self.assertFalse('<script>alert(1)</script>' in html)
        self.assertTrue('&lt;script&gt;' in html)


if __name__ == '__main__':
    unittest.main()
