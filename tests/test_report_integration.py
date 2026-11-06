from datetime import date, datetime, timedelta
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import Mock, patch

import AppKit
import focusledger as fl
import notch
import report
import goals


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ledger = self.root / 'ledger.jsonl'
        self.output = self.root / 'report.html'
        self.addCleanup(fl.BUFFER.clear)
        fl.BUFFER.clear()
        self.time = datetime.now().replace(microsecond=0)

    def test_checkpoint_publishes_real_live_report_and_failed_retry_preserves_it(self):
        with patch.object(fl, 'LEDGER_PATH', str(self.ledger)), patch.object(report, 'OUT_PATH', str(self.output)), patch.object(fl.gemma, 'checkpoint', return_value={'goal': 'Write report', 'aligned': True, 'category': 'deep_work'}):
            fl.append_ledger({'type':'goals', 'goals':'Write report'})
            fl.BUFFER.append({'text':'synthetic'})
            self.assertTrue(fl.run_checkpoint('Write report', self.time - timedelta(minutes=30), self.time))
            first = self.output.read_text()
            data = report.build_report(self.ledger, offline=True)
            self.assertEqual(data['focus_score'], 100)
            self.assertEqual(data['total_min'], 30)
            self.assertTrue('Auto-refresh' in first)
            row = report.load_rows(self.ledger)[-1]
            self.assertEqual(row['end_at'], self.time.isoformat())
            fl.BUFFER.append({'text':'synthetic'})
            with patch.object(fl.gemma, 'checkpoint', side_effect=RuntimeError):
                self.assertFalse(fl.run_checkpoint('Write report', self.time, self.time + timedelta(minutes=30)))
            self.assertEqual(self.output.read_text(), first)
            self.assertEqual(len(fl.BUFFER), 1)

    def test_report_failure_does_not_repeat_or_lose_saved_checkpoint(self):
        with patch.object(fl, 'LEDGER_PATH', str(self.ledger)), patch.object(report, 'refresh_live', side_effect=OSError), patch.object(fl.gemma, 'checkpoint', return_value={'goal':'Write report', 'aligned':True}):
            fl.BUFFER.append({'text':'synthetic'})
            self.assertTrue(fl.run_checkpoint('Write report', self.time - timedelta(minutes=1), self.time))
            self.assertEqual(len(report.load_rows(self.ledger)), 1)
            self.assertFalse(fl.BUFFER)

    def test_native_start_day_records_optional_budgets_without_starting_real_tracker(self):
        app = Mock()
        app.preview = False
        app.field.stringValue.return_value = 'Write report; Study calculus'
        app.goal_targets = {'Write report':90}
        with patch.object(fl, 'LEDGER_PATH', str(self.ledger)):
            notch.App.startDay_.callable(app, None)
        plan = goals.day_plan(report.load_rows(self.ledger))
        self.assertEqual(plan[0]['target_minutes'], 90)
        self.assertIsNone(plan[1]['target_minutes'])
        app.startTracker.assert_called_once()

    def test_review_goals_records_only_user_selected_completion(self):
        fl_row = {'type':'goals','date':date.today().isoformat(),'goals':'Write report'}
        self.ledger.write_text(json.dumps(fl_row)+'\n')
        AppKit.NSApplication.sharedApplication()
        view = AppKit.NSView.alloc().initWithFrame_(AppKit.NSMakeRect(0,0,460,100))
        alert = Mock()
        def confirm():
            view.subviews()[0].setState_(AppKit.NSControlStateValueOn)
            return AppKit.NSAlertFirstButtonReturn
        alert.runModal.side_effect = confirm
        app = Mock()
        app.preview = False
        app.report_busy = False
        app._goal_alert.return_value = (alert,view,100)
        with patch.object(notch.paths, 'LEDGER_PATH', str(self.ledger)), patch.object(fl, 'LEDGER_PATH', str(self.ledger)):
            notch.App.reviewGoals_.callable(app,None)
        self.assertTrue(goals.day_plan(report.load_rows(self.ledger))[0]['completed'])
        app._startReportTask.assert_called_once()

    def test_native_planner_saves_targets_from_real_accessory_fields(self):
        AppKit.NSApplication.sharedApplication()
        view = AppKit.NSView.alloc().initWithFrame_(AppKit.NSMakeRect(0,0,460,120))
        alert = Mock()
        def confirm():
            fields = [v for v in view.subviews() if isinstance(v, AppKit.NSTextField) and v.isEditable()]
            fields[0].setStringValue_('90')
            return AppKit.NSAlertFirstButtonReturn
        alert.runModal.side_effect = confirm
        app = Mock()
        app.goal_targets = {}
        app.field.stringValue.return_value = 'Write report; Study calculus'
        app._goal_alert.return_value = alert,view,120
        notch.App.planTime_.callable(app,None)
        self.assertEqual(app.goal_targets, {'Write report':90, 'Study calculus':None})

    def test_invalid_native_budget_can_be_cancelled_without_replacing_plan(self):
        AppKit.NSApplication.sharedApplication()
        view = AppKit.NSView.alloc().initWithFrame_(AppKit.NSMakeRect(0,0,460,120))
        alert = Mock()
        calls = []
        def respond():
            calls.append(True)
            fields = [v for v in view.subviews() if isinstance(v, AppKit.NSTextField) and v.isEditable()]
            fields[0].setStringValue_('-5')
            return AppKit.NSAlertFirstButtonReturn if len(calls) == 1 else AppKit.NSAlertSecondButtonReturn
        alert.runModal.side_effect = respond
        app = Mock()
        app.goal_targets = {'Write report':60}
        app.field.stringValue.return_value = 'Write report'
        app._goal_alert.return_value = alert,view,120
        notch.App.planTime_.callable(app,None)
        self.assertEqual(app.goal_targets, {'Write report':60})

    def test_background_failure_releases_busy_state_without_raw_error(self):
        app = Mock()
        app.report_busy = True
        task = Mock(side_effect=RuntimeError('sensitive text'))
        notch.App._runReportTask(app,task)
        self.assertFalse(app.report_busy)
        self.assertNotIn('sensitive text', app._report_status.call_args.args[0])

    def test_duplicate_report_request_does_not_start_another_task(self):
        app = Mock()
        app.report_busy = True
        with patch.object(notch.threading,'Thread') as thread:
            notch.App._startReportTask(app,Mock())
            thread.assert_not_called()


if __name__ == '__main__':
    unittest.main()
