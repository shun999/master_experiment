# -*- coding: utf-8 -*-
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from src.startup_diagnostics import StartupDiagnostics
from tests.test_nback_dual_screen import Rig


ROOT = Path(__file__).resolve().parents[1]
STAGES = ('OPERATOR_WINDOW', 'OPERATOR_STIMULI', 'OPERATOR_FIRST_FLIP',
          'PARTICIPANT_WINDOW', 'KEYBOARD', 'PARTICIPANT_STIMULI',
          'PARTICIPANT_FIRST_FLIP', 'KEYBOARD_INITIAL_POLL', 'OPERATOR_READY_FLIP')


class StartupDiagnosticsTests(unittest.TestCase):
    def test_each_step_is_one_shot_and_cancels_before_releasing_file(self):
        with tempfile.TemporaryDirectory() as root:
            path, messages, files = Path(root) / 'trace.log', [], []
            def arm(timeout, repeat, file, exit):
                self.assertEqual(timeout, 20)
                self.assertFalse(repeat)
                self.assertFalse(exit)
                self.assertFalse(file.closed)
                self.assertIn(messages[-1], path.read_text(encoding='utf-8'))
                files.append(file)
            def cancel():
                self.assertFalse(files[-1].closed)
            with patch('src.startup_diagnostics.faulthandler.dump_traceback_later', side_effect=arm) as start, \
                    patch('src.startup_diagnostics.faulthandler.cancel_dump_traceback_later', side_effect=cancel) as stop:
                diagnostics = StartupDiagnostics(messages.append, path)
                for label in ('FIRST', 'SECOND'):
                    with diagnostics.step(label):
                        self.assertEqual(messages[-1], 'STARTUP {} START'.format(label))
                    self.assertEqual(messages[-1], 'STARTUP {} END'.format(label))
                    self.assertEqual(start.call_count, stop.call_count)
                diagnostics.close()
                diagnostics.close()
                self.assertEqual(start.call_count, 2)
                self.assertEqual(stop.call_count, 2)
                self.assertTrue(all(file.closed for file in files))
                with self.assertRaises(RuntimeError):
                    with diagnostics.step('AFTER_CLOSE'):
                        pass

    def test_exception_disarms_and_preserves_error_and_failed_stage(self):
        with tempfile.TemporaryDirectory() as root, \
                patch('src.startup_diagnostics.faulthandler.dump_traceback_later') as start, \
                patch('src.startup_diagnostics.faulthandler.cancel_dump_traceback_later') as stop:
            messages = []
            diagnostics = StartupDiagnostics(messages.append, Path(root) / 'trace.log')
            try:
                with self.assertRaisesRegex(RuntimeError, 'simulated failure'):
                    with diagnostics.step('KEYBOARD'):
                        raise RuntimeError('simulated failure')
                self.assertEqual(stop.call_count, 1)
                self.assertEqual(messages[-1], 'STARTUP KEYBOARD FAILED: RuntimeError: simulated failure')
                self.assertNotIn('STARTUP KEYBOARD END', messages)
            finally:
                diagnostics.close()
            self.assertTrue(start.call_args[1]['file'].closed)
            self.assertEqual(stop.call_count, 1)

    def test_disabled_diagnostics_logs_steps_without_file_or_watchdog(self):
        with tempfile.TemporaryDirectory() as root, \
                patch('src.startup_diagnostics.faulthandler.dump_traceback_later') as start, \
                patch('src.startup_diagnostics.faulthandler.cancel_dump_traceback_later') as stop:
            path, messages = Path(root) / 'trace.log', []
            diagnostics = StartupDiagnostics(messages.append, path, enabled=False)
            with diagnostics.step('WINDOW'):
                pass
            diagnostics.close()
            self.assertEqual(messages, ['STARTUP WINDOW START', 'STARTUP WINDOW END'])
            self.assertFalse(path.exists())
            start.assert_not_called()
            stop.assert_not_called()

    def test_invalid_settings_fail_before_opening_files(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'trace.log'
            for options in ({'enabled': 1}, {'timeout_seconds': 0}, {'timeout_seconds': -1},
                            {'timeout_seconds': float('inf')}, {'timeout_seconds': float('nan')}):
                with self.assertRaises(ValueError):
                    StartupDiagnostics(lambda message: None, path, **options)
                self.assertFalse(path.exists())

    def test_real_watchdog_in_child_process_dumps_only_during_slow_step(self):
        # Keep faulthandler's process-global watchdog separate from other tests.
        code = '''
from pathlib import Path
import sys
import time
sys.path.insert(0, sys.argv[1])
from src.startup_diagnostics import StartupDiagnostics
path = Path(sys.argv[2])
diagnostics = StartupDiagnostics(lambda message: print(message, flush=True), path, timeout_seconds=.15)
try:
    with diagnostics.step('CONTROLLED_WAIT'):
        if sys.argv[3] == 'slow':
            time.sleep(.4)
    with diagnostics.step('FAST_STEP'):
        pass
finally:
    diagnostics.close()
before = path.read_bytes()
time.sleep(.3)
assert path.read_bytes() == before
print('AFTER_CLOSE_OK', flush=True)
'''
        for mode in ('slow', 'fast'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as root:
                path = Path(root) / 'trace.log'
                result = subprocess.run([sys.executable, '-B', '-c', code, str(ROOT), str(path), mode],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        universal_newlines=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('AFTER_CLOSE_OK', result.stdout)
                content = path.read_text(encoding='utf-8')
                if mode == 'slow':
                    self.assertEqual(content.count('Timeout ('), 1)
                    self.assertIn('line ', content)
                    self.assertIn('<module>', content)
                else:
                    self.assertNotIn('Timeout (', content)


class NbackStartupTests(unittest.TestCase):
    def test_startup_order_early_operator_display_and_monitor_closed_before_sdk(self):
        with tempfile.TemporaryDirectory() as root:
            rig = Rig()
            task = rig.load(root)
            created, files = [], []
            real_diagnostics = task.StartupDiagnostics
            def diagnostics(*args, **kwargs):
                instance = real_diagnostics(*args, **kwargs)
                created.append(instance)
                return instance
            real_window = task.visual.Window
            def window(**settings):
                self.assertTrue(created[0]._armed)
                if settings['screen'] == 1:
                    self.assertEqual(len(rig.windows), 1)
                    operator = rig.windows[0]
                    self.assertTrue(operator.history)
                    self.assertIn('被験者画面を準備しています', operator.history[0][1][0])
                    log = Path(str(rig.recorder.base) + '_progress.log').read_text(encoding='utf-8')
                    self.assertIn('STARTUP OPERATOR_FIRST_FLIP END', log)
                    self.assertIn('STARTUP PARTICIPANT_WINDOW START', log)
                    self.assertNotIn('STARTUP KEYBOARD START', log)
                return real_window(**settings)
            select = task.select_tracker
            def tracker():
                self.assertTrue(created[0]._closed)
                self.assertFalse(created[0]._armed)
                self.assertTrue(all(file.closed for file in files))
                return select()
            def arm(timeout, repeat, file, exit):
                self.assertEqual(timeout, 20)
                self.assertFalse(repeat)
                self.assertFalse(exit)
                files.append(file)
            task.visual.Window, task.select_tracker = window, tracker
            task.SKIP_CALIBRATION = False
            output = io.StringIO()
            with patch.object(task, 'StartupDiagnostics', side_effect=diagnostics), \
                    patch('src.startup_diagnostics.faulthandler.dump_traceback_later', side_effect=arm) as start, \
                    patch('src.startup_diagnostics.faulthandler.cancel_dump_traceback_later') as stop, \
                    redirect_stdout(output):
                task.main('DNV', 2)
            log = Path(str(rig.recorder.base) + '_progress.log').read_text(encoding='utf-8')
            phases = [line.split(' | ', 1)[1] for line in log.splitlines() if ' | STARTUP ' in line]
            expected = [phase for label in STAGES for phase in ('STARTUP ' + label + ' START', 'STARTUP ' + label + ' END')]
            self.assertEqual(phases[1:-1], expected)
            self.assertEqual(phases[-1], 'STARTUP COMPLETE')
            environment = json.loads(phases[0][len('STARTUP ENVIRONMENT '):])
            self.assertEqual(Path(environment['script']).parent, task.PROJECT_ROOT)
            self.assertEqual(environment['python_executable'], sys.executable)
            self.assertEqual(environment['psychopy_version'], 'mock')
            self.assertEqual(environment['participant_screen'], 1)
            self.assertTrue(all(line in output.getvalue() for line in log.splitlines()))
            self.assertEqual(start.call_count, len(STAGES))
            self.assertEqual(stop.call_count, len(STAGES))
            meta = json.loads(Path(str(rig.recorder.base) + '_metadata.json').read_text(encoding='utf-8'))
            self.assertTrue(meta['startup_diagnostics_enabled'])
            self.assertEqual(meta['startup_traceback_seconds'], 20)
            self.assertEqual(Path(meta['startup_traceback_path']).parent, rig.recorder.base.parent)
            self.assertTrue(Path(meta['startup_traceback_path']).exists())
            self.assertEqual(meta['status'], 'completed')

    def test_startup_failures_keep_failed_stage_and_close_every_resource(self):
        stages = ('OPERATOR_WINDOW', 'OPERATOR_STIMULI', 'OPERATOR_FIRST_FLIP',
                  'PARTICIPANT_WINDOW', 'KEYBOARD', 'PARTICIPANT_STIMULI', 'PARTICIPANT_FIRST_FLIP')
        for fail_stage in stages:
            with self.subTest(stage=fail_stage), tempfile.TemporaryDirectory() as root:
                rig = Rig()
                task = rig.load(root)
                def fail():
                    raise RuntimeError('simulated startup failure')
                real_window, real_text = task.visual.Window, task.visual.TextStim
                def window(**settings):
                    screen = settings['screen']
                    if fail_stage == ('OPERATOR_WINDOW' if screen == 0 else 'PARTICIPANT_WINDOW'):
                        fail()
                    instance = real_window(**settings)
                    if fail_stage == ('OPERATOR_FIRST_FLIP' if screen == 0 else 'PARTICIPANT_FIRST_FLIP'):
                        instance.flip = fail
                    return instance
                def text(win, **kwargs):
                    if fail_stage == ('OPERATOR_STIMULI' if win.settings['screen'] == 0 else 'PARTICIPANT_STIMULI'):
                        fail()
                    return real_text(win, **kwargs)
                task.visual.Window, task.visual.TextStim = window, text
                if fail_stage == 'KEYBOARD':
                    task.keyboard.Keyboard = fail
                with patch('src.startup_diagnostics.faulthandler.dump_traceback_later') as start, \
                        patch('src.startup_diagnostics.faulthandler.cancel_dump_traceback_later') as stop, \
                        self.assertRaisesRegex(RuntimeError, 'simulated startup failure'), redirect_stdout(io.StringIO()):
                    task.main('DNV', 2)
                self.assertEqual(start.call_count, stop.call_count)
                self.assertTrue(all(call[1]['file'].closed for call in start.call_args_list))
                self.assertTrue(rig.recorder.closed)
                self.assertTrue(all(window.closed for window in rig.windows))
                log = Path(str(rig.recorder.base) + '_progress.log').read_text(encoding='utf-8')
                self.assertIn('STARTUP ' + fail_stage + ' START', log)
                self.assertIn('STARTUP ' + fail_stage + ' FAILED: RuntimeError', log)
                self.assertNotIn('STARTUP ' + fail_stage + ' END', log)
                self.assertNotIn('Detecting Tobii tracker', log)
                meta = json.loads(Path(str(rig.recorder.base) + '_metadata.json').read_text(encoding='utf-8'))
                self.assertEqual(meta['status'], 'failed')

    def test_disabled_diagnostics_still_shows_startup_and_records_configuration(self):
        with tempfile.TemporaryDirectory() as root:
            rig = Rig()
            task = rig.load(root)
            task.STARTUP_DIAGNOSTICS = False
            with patch('src.startup_diagnostics.faulthandler.dump_traceback_later') as start, \
                    patch('src.startup_diagnostics.faulthandler.cancel_dump_traceback_later') as stop, \
                    redirect_stdout(io.StringIO()):
                task.main('DNV', 2)
            start.assert_not_called()
            stop.assert_not_called()
            self.assertFalse(Path(str(rig.recorder.base) + '_startup_traceback.log').exists())
            log = Path(str(rig.recorder.base) + '_progress.log').read_text(encoding='utf-8')
            self.assertIn('STARTUP COMPLETE', log)
            meta = json.loads(Path(str(rig.recorder.base) + '_metadata.json').read_text(encoding='utf-8'))
            self.assertFalse(meta['startup_diagnostics_enabled'])
            self.assertIsNone(meta['startup_traceback_path'])


if __name__ == '__main__':
    unittest.main()
