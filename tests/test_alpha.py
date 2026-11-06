import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch, Mock

import focusledger as fl
import gemma
import paths
import report


class AlphaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        fl.BUFFER.clear()
        self.refresh = patch.object(fl, "refresh_report").start()
        self.addCleanup(patch.stopall)
        fl._last_hash = None
        self.addCleanup(fl.BUFFER.clear)

    def test_capture_skips_unidentified_and_blocked(self):
        for window in [("Unknown", "", None), ("1Password", "secret", 3), ("Safari", "login", 4)]:
            with patch.object(fl, 'frontmost', return_value=window), patch.object(fl, 'capture_window') as capture:
                fl.capture_tick()
                capture.assert_not_called()
        self.assertFalse(fl.BUFFER)

    def test_capture_only_selected_window_and_memory_ocr(self):
        with patch.object(fl, 'frontmost', return_value=('Editor', 'sample', 42)), \
                patch.object(fl, 'capture_window', return_value=object()) as capture, \
                patch.object(fl.Quartz, 'CGImageGetDataProvider'), \
                patch.object(fl.Quartz, 'CGDataProviderCopyData', return_value=b'pixels'), \
                patch.object(fl.vision_ocr, 'ocr_image', return_value='synthetic text'):
            fl.capture_tick()
            capture.assert_called_once_with(42)
            self.assertEqual(len(fl.BUFFER), 1)

    def test_window_capture_fails_closed_before_requesting_content(self):
        import window_capture
        with patch.object(window_capture.Quartz, 'CGPreflightScreenCaptureAccess', return_value=False), patch.object(window_capture, 'SC') as api:
            self.assertIsNone(window_capture.capture_window(42))
            api.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_.assert_not_called()
        self.assertIsNone(window_capture.capture_window(None))

    def test_offline_sample_does_not_claim_no_activity_or_goal_completion(self):
        data = report.build_report(paths.BASE / 'ledger.sample.jsonl', demo=True, offline=True)
        out = self.root / 'demo.html'
        report.render(data, out)
        html = out.read_text()
        self.assertNotIn('No activity recorded today.', html)
        self.assertNotIn('Goals met', html)
        self.assertTrue('Drift' in html)

    def test_report_fonts_travel_with_exported_html(self):
        data = report.build_report(paths.BASE / 'ledger.sample.jsonl', demo=True, offline=True)
        out = self.root / 'standalone.html'
        report.render(data, out)
        html = out.read_text()
        self.assertEqual(html.count('data:font/ttf;base64,'), 3)
        self.assertIn('SIL OPEN FONT LICENSE', html)
        self.assertNotIn('fonts.googleapis.com', html)
        self.assertIn('Instrument Serif', html)

    def test_native_fonts_are_available_to_the_process(self):
        from typography import register_native_fonts
        import notch
        register_native_fonts()
        self.assertEqual(notch._font(13).familyName(), 'Geist')
        self.assertEqual(notch._font(13, 'semibold').fontName(), 'Geist-SemiBold')
        self.assertEqual(notch._serif(28).familyName(), 'Instrument Serif')

    def test_window_selection_matches_pid_not_name(self):
        app = Mock()
        app.localizedName.return_value = 'Editor'
        app.processIdentifier.return_value = 7
        windows = [{'kCGWindowOwnerPID': 8, 'kCGWindowLayer': 0, 'kCGWindowName': 'other', 'kCGWindowNumber': 1},
                   {'kCGWindowOwnerPID': 7, 'kCGWindowLayer': 0, 'kCGWindowName': 'ours', 'kCGWindowNumber': 2}]
        with patch.object(fl, 'NSWorkspace') as workspace, patch.object(fl.Quartz, 'CGWindowListCopyWindowInfo', return_value=windows):
            workspace.sharedWorkspace.return_value.frontmostApplication.return_value = app
            self.assertEqual(fl.frontmost(), ('Editor', 'ours', 2))

    def test_failed_checkpoint_retains_samples_and_interval(self):
        fl.BUFFER.append({'text': 'private'})
        start = datetime(2026, 11, 6, 10)
        with patch.object(gemma, 'checkpoint', side_effect=RuntimeError('private')):
            self.assertFalse(fl.run_checkpoint('goal', start, start + timedelta(minutes=30)))
        self.assertEqual(len(fl.BUFFER), 1)
        with patch.object(fl, 'LEDGER_PATH', str(self.root / 'ledger')), patch.object(gemma, 'checkpoint', return_value={'aligned': True}):
            self.assertTrue(fl.run_checkpoint('goal', start, start + timedelta(minutes=60)))
        self.assertEqual(json.loads((self.root / 'ledger').read_text())['minutes'], 60)
        self.assertFalse(fl.BUFFER)

    def test_migration_preserves_original_and_destination(self):
        base = self.root / 'checkout'
        data = self.root / 'data'
        base.mkdir()
        (base / 'ledger.jsonl').write_text('legacy')
        with patch.object(paths, 'BASE', base), patch.object(paths, 'DATA_DIR', data), patch.object(paths, 'LEDGER_PATH', str(data / 'ledger.jsonl')), patch.dict(os.environ, {}, clear=True):
            paths.initialize()
            self.assertEqual((data / 'ledger.jsonl').read_text(), 'legacy')
            (data / 'ledger.jsonl').write_text('new')
            paths.initialize()
            self.assertEqual((data / 'ledger.jsonl').read_text(), 'new')
            self.assertEqual((base / 'ledger.jsonl').read_text(), 'legacy')

    def test_explicit_ledger_disables_migration(self):
        with patch.object(paths, 'DATA_DIR', self.root / 'data'), patch.dict(os.environ, {'FL_LEDGER_PATH': str(self.root / 'custom')}), patch.object(paths.shutil, 'copyfileobj') as copy:
            paths.initialize()
            copy.assert_not_called()

    def test_environment_paths_are_absolute_and_shared(self):
        env = {**os.environ, 'FL_DATA_DIR': str(self.root), 'FL_LEDGER_PATH': str(self.root / 'custom')}
        result = subprocess.run([sys.executable, '-c', 'import paths, focusledger, report; assert paths.LEDGER_PATH == focusledger.LEDGER_PATH == report.LEDGER_PATH; print(paths.LEDGER_PATH)'], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str((self.root / 'custom').resolve()))

    def test_migration_failure_removes_partial_copy(self):
        (self.root / 'ledger.jsonl').write_text('legacy')
        data = self.root / 'data'
        with patch.object(paths, 'BASE', self.root), patch.object(paths, 'DATA_DIR', data), patch.object(paths, 'LEDGER_PATH', str(data / 'ledger.jsonl')), patch.dict(os.environ, {}, clear=True), patch.object(paths.shutil, 'copyfileobj', side_effect=OSError):
            with self.assertRaises(RuntimeError):
                paths.initialize()
            self.assertFalse((data / 'ledger.jsonl').exists())
            self.assertTrue((self.root / 'ledger.jsonl').exists())

    def test_new_day_preserves_history(self):
        ledger = self.root / 'ledger'
        ledger.write_text('history')
        with patch.object(fl, 'LEDGER_PATH', str(ledger)):
            fl.reset_todays_ledger()
        self.assertEqual(ledger.read_text(), 'history')

    def test_day_selection_legacy_and_explicit(self):
        rows = [{'type': 'goals', 'date': '2026-11-05'}, {'type': 'checkpoint'},
                {'type': 'goals', 'date': '2026-11-06'}, {'type': 'checkpoint', 'date': '2026-11-06'}]
        self.assertEqual(paths.rows_for_day(rows), rows[2:])
        self.assertEqual(paths.rows_for_day(rows, '2026-11-05'), rows[:2])

    def test_report_escaping_and_offline_demo(self):
        source = self.root / 'ledger'
        source.write_text(json.dumps({'type': 'goals', 'date': '2026-11-06', 'goals': '<script>alert(1)</script>'}) + '\ninvalid\nnull\n')
        with patch.object(gemma, 'daily_report') as model:
            data = report.build_report(source, demo=True, offline=True)
            out = self.root / 'report.html'
            report.render(data, out)
            model.assert_not_called()
        html = out.read_text()
        self.assertNotIn('<script>alert(1)</script>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('synthetic activity only', html)

    def test_report_failure_hides_model_error_details(self):
        with patch.object(gemma, 'daily_report', side_effect=RuntimeError('private screen text')):
            data = report.build_report(paths.BASE / 'ledger.sample.jsonl')
        self.assertNotIn('private screen text', str(data))
        self.assertIn('unavailable', data['synthesis']['headline'])

    def test_model_timeout_and_invalid_json(self):
        with patch.object(gemma.requests, 'post', side_effect=gemma.requests.Timeout):
            with self.assertRaises(gemma.requests.Timeout):
                gemma.gemma('synthetic')
        with patch.object(gemma, 'gemma', return_value='private screen text'):
            with self.assertRaises(ValueError) as err:
                gemma.gemma_json('synthetic')
        self.assertNotIn('private screen text', str(err.exception))

    def test_demo_cli_does_not_touch_personal_ledger(self):
        personal = self.root / 'ledger.jsonl'
        personal.write_text('private sentinel')
        out = self.root / 'demo.html'
        result = subprocess.run([sys.executable, 'report.py', 'ledger.sample.jsonl', '--out', str(out), '--demo', '--offline'],
                                env={**os.environ, 'FL_LEDGER_PATH': str(personal)}, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(personal.read_text(), 'private sentinel')
        self.assertTrue(out.exists())

    def test_idle_close_excludes_idle_gap(self):
        start = datetime(2026, 11, 6, 10)
        fl.BUFFER.append({'text': 'synthetic'})
        with patch.object(fl.paths, 'initialize'), patch.object(fl, 'prompt_goals', return_value='goal'), \
                patch.object(fl.gemma, 'warmup'), patch.object(fl.signal, 'signal'), \
                patch.object(fl, 'idle_seconds', side_effect=[0, 601, KeyboardInterrupt]), \
                patch.object(fl, 'datetime') as clock, patch.object(fl.time, 'sleep'), \
                patch.object(fl, 'append_ledger') as append, patch.object(fl, 'run_checkpoint', return_value=True) as checkpoint:
            clock.now.side_effect = [start, start + timedelta(seconds=601)]
            fl._flush_requested = False
            fl.main()
            self.assertEqual(checkpoint.call_args.kwargs['until'], start)
            self.assertEqual(append.call_args.args[0]['duration'], '0h 0m')

    def test_idle_failure_retries_same_interval(self):
        start = datetime(2026, 11, 6, 10)
        fl.BUFFER.append({'text': 'synthetic'})
        with patch.object(fl.paths, 'initialize'), patch.object(fl, 'prompt_goals', return_value='goal'), \
                patch.object(fl.gemma, 'warmup'), patch.object(fl.signal, 'signal'), \
                patch.object(fl, 'idle_seconds', side_effect=[0, 601, 611, KeyboardInterrupt]), \
                patch.object(fl, 'datetime') as clock, patch.object(fl.time, 'sleep'), \
                patch.object(fl, 'append_ledger'), patch.object(fl, 'run_checkpoint', side_effect=[False, True]) as checkpoint:
            clock.now.side_effect = [start, start + timedelta(seconds=601), start + timedelta(seconds=611)]
            fl._flush_requested = False
            fl.main()
            self.assertEqual(checkpoint.call_count, 2)
            self.assertEqual(checkpoint.call_args_list[0].kwargs, checkpoint.call_args_list[1].kwargs)

    def test_midnight_closes_previous_day_and_stops(self):
        from datetime import date
        start = datetime(2026, 11, 5, 23, 59)
        with patch.object(fl.paths, 'initialize'), patch.object(fl, 'prompt_goals', return_value='goal'), \
                patch.object(fl.gemma, 'warmup'), patch.object(fl.signal, 'signal'), \
                patch.object(fl, 'idle_seconds', return_value=0), patch.object(fl.time, 'sleep'), \
                patch.object(fl, 'date') as day, patch.object(fl, 'datetime') as clock, \
                patch.object(fl, 'append_ledger') as append:
            day.today.side_effect = [date(2026, 11, 5), date(2026, 11, 5), date(2026, 11, 6), date(2026, 11, 6)]
            clock.now.return_value = start
            clock.combine.side_effect = datetime.combine
            clock.min = datetime.min
            fl._flush_requested = False
            fl.main()
            self.assertEqual(append.call_args.args[0]['date'], '2026-11-05')
            self.assertEqual(append.call_args.args[0]['type'], 'session_close')

    def test_report_acknowledgement_and_failure(self):
        import notch
        for completed, success, expected_calls in [(True, True, 1), (True, False, 0), (False, False, 0)]:
            app = Mock()
            app.tracker.poll.return_value = None
            app.flush_event.wait.return_value = completed
            def acknowledge(*args, **kwargs):
                app.flush_ok = success
            app.tracker.send_signal.side_effect = acknowledge
            with patch.object(notch.subprocess, 'run') as run:
                run.return_value.returncode = 0
                notch.App._renderReport(app)
                self.assertEqual(run.call_count, expected_calls)
                if not expected_calls:
                    self.assertIn('incomplete', app._report_status.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
