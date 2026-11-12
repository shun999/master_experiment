# -*- coding: utf-8 -*-
import csv
from contextlib import redirect_stdout
from datetime import datetime, timezone
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from src import resting_baseline as runtime
from src.experiment_paths import create_rest_output_prefix
from tests.test_experiment_assignment import Dialogs
from tests.test_nback_dual_screen import Rig, Clock, Text


ROOT = Path(__file__).resolve().parents[1]


class RestKeyboard:
    def __init__(self, rig):
        if rig.fail_keyboard:
            raise RuntimeError('simulated keyboard failure')
        self.rig, self.emitted, self.first_poll = rig, set(), True
        self.clock = Clock(rig)
        rig.keyboard = self
        self.reset = self.clock.reset
        def reset():
            self.reset()
            rig.start_flip_time = rig.now
        self.clock.reset = reset

    def clearEvents(self):
        self.emitted.clear()

    def getKeys(self, keyList, waitRelease=False, clear=True):
        assert waitRelease is False and clear is True
        if self.first_poll:
            self.first_poll = False
            return []
        phase = self.rig.session.status
        now = self.clock.getTime()
        if phase == 'ready':
            script = [('space', .01), ('escape' if self.rig.abort_ready else self.rig.start_key, .06)]
        elif phase == 'running':
            if self.rig.error_after is not None and now > self.rig.error_after:
                raise RuntimeError('simulated input failure')
            script = self.rig.script
            if self.rig.abort_rt is not None:
                script = list(script) + [('escape', self.rig.abort_rt), ('space', self.rig.abort_rt + .001)]
            if self.rig.session.laps and not self.rig.live_csv:
                self.rig.live_csv = self.rig.read_laps()
        else:
            script = [('space', self.rig.duration + .02), ('num_enter', self.rig.duration + .1)]
        keys = []
        for index, (name, rt) in enumerate(script):
            token = (phase, index)
            if token not in self.emitted and rt <= now and name in keyList:
                self.emitted.add(token)
                keys.append(types.SimpleNamespace(name=name, rt=rt))
        return keys


class RestRig(Rig):
    def __init__(self, script=(), abort_ready=False, abort_rt=None, fail_keyboard=False,
                 fail_text=False, fail_window=None, error_after=None, start_key='return'):
        super().__init__(fail_window=fail_window)
        self.script, self.abort_ready, self.abort_rt = script, abort_ready, abort_rt
        self.fail_keyboard, self.fail_text, self.error_after = fail_keyboard, fail_text, error_after
        self.start_key, self.session, self.keyboard = start_key, None, None
        self.duration, self.directory, self.start_flip_time = None, None, None
        self.live_csv = []

    def read_laps(self):
        with next(self.directory.rglob('*_laps.csv')).open(encoding='utf-8-sig', newline='') as stream:
            return list(csv.DictReader(stream))

    def metadata(self):
        return json.loads(next(self.directory.rglob('*_metadata.json')).read_text(encoding='utf-8'))

    def run(self, directory, duration=300.0, participant_id=' afx ', session_number=1,
            diagnostics=False, gui=None, monitors=2, **settings):
        self.directory, self.duration = Path(directory), duration
        psychopy = types.ModuleType('psychopy')
        psychopy.__version__ = 'mock'
        psychopy.core = types.SimpleNamespace(Clock=lambda: Clock(self))
        def text(win, **kwargs):
            if self.fail_text and win.settings['screen'] == 1:
                raise RuntimeError('simulated participant text failure')
            return Text(win, **kwargs)
        psychopy.visual = types.SimpleNamespace(Window=self.window, TextStim=text)
        psychopy.gui = gui
        hardware = types.ModuleType('psychopy.hardware')
        def keyboard():
            assert len(self.windows) == 2
            assert self.windows[0].history and self.windows[1].history
            assert self.windows[1].history[0][1] == ['+']
            return RestKeyboard(self)
        hardware.keyboard = types.SimpleNamespace(Keyboard=keyboard)
        pyglet = types.ModuleType('pyglet')
        pyglet.canvas = types.SimpleNamespace(get_display=lambda: types.SimpleNamespace(get_screens=lambda: list(range(monitors))))
        session_class = runtime.RestSession
        def session(*args):
            self.session = session_class(*args)
            return self.session
        with patch.dict(sys.modules, {'psychopy': psychopy, 'psychopy.hardware': hardware,
                                      'pyglet': pyglet, 'tobii_research': None}), \
                patch.object(runtime, 'RestSession', side_effect=session), redirect_stdout(io.StringIO()):
            return runtime.run_resting_baseline(participant_id, session_number, output_root=directory,
                        duration_seconds=duration, startup_diagnostics=diagnostics, **settings)


class RestingBaselineTests(unittest.TestCase):
    def test_lap_timestamps_intervals_and_deadline(self):
        session = runtime.RestSession(300)
        self.assertIsNone(session.add_lap(1))
        session.start(datetime(2026, 10, 29, 1, 0, tzinfo=timezone.utc))
        for rt in (-1, float('nan'), float('inf'), 300, 301):
            self.assertIsNone(session.add_lap(rt))
        first, second = session.add_lap(1.234567), session.add_lap(2.25)
        self.assertEqual(first['lap_number'], 1)
        self.assertEqual(first['elapsed_seconds'], first['lap_seconds'])
        self.assertEqual(first['timestamp_iso'], '2026-10-29T01:00:01.234567+00:00')
        self.assertEqual(second['lap_seconds'], 1.015433)
        self.assertIsNone(session.add_lap(2.25))
        self.assertIsNone(session.add_lap(1.5))
        self.assertIsNotNone(session.add_lap(299.999999))
        session.finish('completed', 300.01)
        self.assertIsNone(session.add_lap(299.9999999))
        self.assertEqual(runtime.format_elapsed(300), '05:00.0')

    def test_three_hundred_seconds_fixation_laps_and_durable_output(self):
        script = [('space', 1.5), ('space', .25), ('return', .8), ('num_enter', 1),
                  ('space', 299.99999), ('space', 300), ('space', 300.00001)]
        with tempfile.TemporaryDirectory() as directory:
            rig = RestRig(script=script)
            result = rig.run(directory, diagnostics=True)
            rows = rig.read_laps()
            self.assertEqual(result, rig.metadata())
            self.assertTrue(rig.live_csv)
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(result['participant'], 'AFX')
            self.assertEqual(result['lap_count'], 3)
            self.assertEqual([float(row['elapsed_seconds']) for row in rows], [.25, 1.5, 299.99999])
            self.assertEqual([float(row['lap_seconds']) for row in rows], [.25, 1.25, 298.49999])
            self.assertAlmostEqual(result['actual_elapsed_seconds'], 300, delta=.04)
            self.assertEqual(result['planned_duration_seconds'], 300)
            self.assertEqual(Path(result['output_directory']).parent, Path(directory) / 'rest' / 'AFX' / 'session_01')
            self.assertTrue(all(path.name.startswith(Path(result['output_prefix']).name + '_')
                                for path in Path(result['output_directory']).iterdir()))
            trace = next(Path(directory).rglob('*_startup_traceback.log')).read_text(encoding='utf-8')
            self.assertIn('KEYBOARD_INITIAL_POLL END', next(Path(directory).rglob('*_progress.log')).read_text(encoding='utf-8'))
            self.assertIn('PARTICIPANT_FIRST_FLIP START', trace)
            self.assertNotIn('Timeout', trace)
        self.assertTrue(all(win.closed for win in rig.windows))
        operator, participant = rig.windows
        self.assertFalse(operator.settings['fullscr'])
        self.assertFalse(operator.settings['waitBlanking'])
        self.assertEqual(operator.settings['size'], (960, 640))
        self.assertTrue(participant.settings['fullscr'])
        self.assertTrue(participant.settings['waitBlanking'])
        self.assertTrue(all(frame == ['+'] for _, frame, _ in participant.history))
        self.assertTrue(any(abs(moment - rig.start_flip_time) < 1e-9 for moment, _, _ in participant.history))
        cross = next(text for text in rig.texts if text.win == participant)
        self.assertEqual(cross.settings['height'], 48)
        self.assertEqual(cross.settings['pos'], (0, 0))
        self.assertEqual(cross.settings['color'], 'white')
        shown = '\n'.join(text for _, frame, _ in operator.history for text in frame)
        self.assertIn('05:00.0', shown)
        self.assertIn('Lap総数：3', shown)
        self.assertNotIn('マーカー', shown)

    def test_no_laps_and_independent_session_number(self):
        with tempfile.TemporaryDirectory() as directory:
            rig = RestRig(start_key='num_enter')
            result = rig.run(directory, duration=.5, session_number=12)
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(result['lap_count'], 0)
            self.assertEqual(rig.read_laps(), [])
            self.assertEqual(Path(result['output_directory']).parent.name, 'session_12')

    def test_abort_before_start_and_during_measurement_preserves_laps(self):
        for abort_ready, abort_rt, expected in ((True, None, 0), (False, .5, 1)):
            with self.subTest(ready=abort_ready), tempfile.TemporaryDirectory() as directory:
                rig = RestRig(script=[('space', .25)], abort_ready=abort_ready, abort_rt=abort_rt)
                result = rig.run(directory, duration=1)
                self.assertEqual(result['status'], 'aborted')
                self.assertEqual(len(rig.read_laps()), expected)
                self.assertEqual(result['actual_elapsed_seconds'], 0 if abort_ready else .5)
                self.assertTrue(all(win.closed for win in rig.windows))

    def test_startup_and_running_errors_close_windows_and_save_status(self):
        cases = [dict(fail_window=0), dict(fail_window=1), dict(fail_text=True),
                 dict(fail_keyboard=True), dict(script=[('space', .25)], error_after=.5)]
        for options in cases:
            with self.subTest(options=options), tempfile.TemporaryDirectory() as directory:
                rig = RestRig(**options)
                with self.assertRaisesRegex(RuntimeError, 'simulated'):
                    rig.run(directory, duration=1, diagnostics=True)
                self.assertEqual(rig.metadata()['status'], 'failed')
                self.assertIn('simulated', rig.metadata()['error'])
                self.assertTrue(all(win.closed for win in rig.windows))
                if 'error_after' in options:
                    self.assertEqual(len(rig.read_laps()), 1)

    def test_prompt_validation_and_cancel_do_not_create_outputs(self):
        dialogs = Dialogs([['bad id', '1'], ['afx', '0'], ['afx', '1.5'], [' afx ', '7']])
        assignment = runtime.prompt_rest_assignment(types.SimpleNamespace(Dlg=dialogs.dialog))
        self.assertEqual(assignment, ('AFX', 7))
        self.assertEqual(len(dialogs.shown), 4)
        with tempfile.TemporaryDirectory() as directory:
            cancel = Dialogs([None])
            rig = RestRig()
            self.assertIsNone(rig.run(directory, participant_id=None, session_number=None,
                                     gui=types.SimpleNamespace(Dlg=cancel.dialog)))
            self.assertEqual(list(Path(directory).iterdir()), [])
            self.assertEqual(rig.windows, [])

    def test_operator_refresh_is_throttled_and_only_five_recent_laps_are_shown(self):
        rig = Rig()
        win = rig.window(screen=0, waitBlanking=False)
        session = runtime.RestSession(300)
        session.start(datetime.now(timezone.utc))
        for rt in range(1, 8):
            session.add_lap(rt)
        view = runtime.RestOperator(win, types.SimpleNamespace(Clock=lambda: Clock(rig)),
                                   types.SimpleNamespace(TextStim=Text), 'AFX', 1, session, 5)
        for step in range(100):
            view.render(step * .02)
            rig.wait(.02)
        times = [moment for moment, _, _ in win.history]
        self.assertTrue(all(b - a >= .2 - 1e-8 for a, b in zip(times, times[1:])))
        shown = view.body.text
        self.assertIn('Lap総数：7', shown)
        self.assertNotIn('Lap 1　', shown)
        self.assertNotIn('Lap 2　', shown)
        self.assertIn('Lap 3　', shown)
        self.assertIn('Lap 7　', shown)

    def test_invalid_settings_and_missing_monitors_do_not_create_outputs(self):
        for settings in (dict(duration=0), dict(duration=float('nan')), dict(operator_refresh_hz=0),
                         dict(startup_traceback_seconds=0), dict(operator_window_size=(0, 640)),
                         dict(screen_index=0), dict(monitors=1), dict(session_number=0),
                         dict(participant_id='../AFX')):
            with self.subTest(settings=settings), tempfile.TemporaryDirectory() as directory:
                rig = RestRig()
                with self.assertRaises(ValueError):
                    rig.run(directory, **settings)
                self.assertEqual(list(Path(directory).iterdir()), [])
                self.assertEqual(rig.windows, [])

    def test_paths_never_overwrite_and_validate_before_creating_files(self):
        with tempfile.TemporaryDirectory() as directory, patch('src.experiment_paths.datetime') as clock:
            clock.now.return_value = datetime(2026, 10, 29, 10, 0)
            first = create_rest_output_prefix(directory, ' afx ', 1)
            saved = Path(str(first) + '_laps.csv')
            saved.write_text('original', encoding='utf-8')
            second = create_rest_output_prefix(directory, 'AFX', 1)
            self.assertEqual(second.parent.name, first.parent.name + '_02')
            self.assertEqual(saved.read_text(encoding='utf-8'), 'original')
            other = create_rest_output_prefix(directory, 'AFX', 2)
            self.assertNotEqual(first.parent.parent, other.parent.parent)
            for participant, number in (('../AFX', 1), ('AFX', 0), ('AFX', True), ('AFX', 1.5)):
                with self.assertRaises(ValueError):
                    create_rest_output_prefix(Path(directory) / 'invalid', participant, number)
                self.assertFalse((Path(directory) / 'invalid').exists())

    def test_entrypoint_passes_configuration_without_device_imports(self):
        spec = importlib.util.spec_from_file_location('rest_entrypoint', str(ROOT / 'psychopy2022_resting_baseline.py'))
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'tobii_research': None}):
            spec.loader.exec_module(module)
            with patch.object(module, 'run_resting_baseline', return_value='result') as run:
                self.assertEqual(module.main('AFX', 2), 'result')
            args, config = run.call_args
            self.assertEqual(args, ('AFX', 2))
            self.assertEqual(config['duration_seconds'], 300)
            self.assertEqual(config['output_root'], ROOT / 'data')


if __name__ == '__main__':
    unittest.main()
