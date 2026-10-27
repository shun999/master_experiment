# -*- coding: utf-8 -*-
"""Hardware-free checks: python -m unittest tests.test_nback_dual_screen -v."""

import csv
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


TASK_PATH = Path(__file__).resolve().parents[1].joinpath('psychopy2022_spectrum1200_nback_lightweight.py')


class Clock:
    def __init__(self, rig):
        self.rig = rig
        self.origin = rig.now

    def reset(self):
        self.origin = self.rig.now

    def getTime(self):
        return self.rig.now - self.origin


class Window:
    def __init__(self, rig, **settings):
        # Supported Window constructor arguments used by these experiments.
        # Do not silently accept invented keywords such as `title`: PsychoPy
        # 2022.2.4 rejects them before it can initialise a real window.
        supported = {'size', 'fullscr', 'screen', 'units', 'color', 'waitBlanking',
                     'checkTiming', 'allowGUI', 'winType', 'autoLog'}
        unsupported = set(settings) - supported
        if unsupported:
            raise TypeError("Window.__init__() got an unexpected keyword argument {!r}".format(
                sorted(unsupported)[0]))
        self.rig = rig
        self.settings = settings
        self.size = settings.get('size', (1280, 720))
        self.monitorFramePeriod = 1.0 / 60
        self.refreshThreshold = 1.2 / 60
        self.frameIntervals = []
        self.nDroppedFrames = 0
        self.recordFrameIntervals = False
        self.last_recorded_flip = None
        self.callbacks = []
        self.drawn = []
        self.history = []
        self.closed = False

    def callOnFlip(self, fn, *args, **kwargs):
        self.callbacks.append((fn, args, kwargs))

    def flip(self):
        self.rig.now += self.monitorFramePeriod if self.settings['waitBlanking'] else .0001
        if self.recordFrameIntervals:
            if self.last_recorded_flip is not None:
                interval = self.rig.now - self.last_recorded_flip
                self.frameIntervals.append(interval)
                self.nDroppedFrames += int(interval > self.refreshThreshold)
            self.last_recorded_flip = self.rig.now
        else:
            self.last_recorded_flip = None
        self.history.append((self.rig.now, list(self.drawn), self.recordFrameIntervals))
        self.drawn.clear()
        callbacks, self.callbacks = self.callbacks, []
        for fn, args, kwargs in callbacks:
            fn(*args, **kwargs)

    def close(self):
        self.closed = True


class Text:
    def __init__(self, win, text='', **settings):
        self.win = win
        self.text = text
        self.settings = settings
        win.rig.texts.append(self)

    def draw(self):
        self.win.drawn.append(self.text)


class KeyboardClock(Clock):
    def reset(self):
        super().reset()
        self.rig.trial += 1


class Keyboard:
    def __init__(self, rig):
        self.rig = rig
        self.clock = KeyboardClock(rig)
        self.responded = False

    def clearEvents(self):
        self.responded = False

    def getKeys(self, keyList, waitRelease=False, clear=True):
        assert waitRelease is False and clear is True
        if 'escape' in keyList and self.rig.should_abort():
            return [types.SimpleNamespace(name='escape', rt=self.clock.getTime())]
        if 'space' in keyList:
            return [types.SimpleNamespace(name='space', rt=0)]
        responses = {1: 'left', 2: 'right'}  # Trial 3 is an omission.
        if ('left' in keyList and not self.responded
                and self.clock.getTime() >= .2 and self.rig.trial in responses):
            self.responded = True
            return [types.SimpleNamespace(name=responses[self.rig.trial], rt=self.clock.getTime())]
        return []


class Recorder:
    def __init__(self, rig, base):
        self.rig = rig
        rig.recorder = self
        self.items = types.SimpleNamespace(qsize=lambda: 0)
        self.attempts = {}
        self.written = {}
        self.dropped = {}
        self.first_device_ts = {}
        self.last_device_ts = {}
        self.large_device_gaps = {}
        self.time_backwards_or_equal = {}
        self.writer_error = None
        self.closed = False
        self.close_calls = 0
        self.unsubscribe_calls = 0
        self.base = base

    def subscribe(self, tracker, kind, constant, required=False):
        self.attempts[kind] = True
        if kind == 'gaze':
            self.rig.subscribe_time = self.rig.now

    def unsubscribe_all(self, tracker):
        self.unsubscribe_calls += 1
        self.rig.unsubscribe_time = self.rig.now
        if self.rig.unsubscribe_error:
            raise RuntimeError('simulated unsubscribe failure')

    def close(self):
        self.close_calls += 1
        self.closed = True


class Rig:
    def __init__(self, abort=None, fail_window=None, unsubscribe_error=False):
        self.now = 0.0
        self.trial = 0
        self.windows = []
        self.texts = []
        self.recorder = None
        self.abort = abort
        self.abort_sent = False
        self.fail_window = fail_window
        self.unsubscribe_error = unsubscribe_error
        self.subscribe_time = self.unsubscribe_time = None
        self.calibration_left = False
        self.tlx_active = False
        self.tlx_answers = 0

    def window(self, **settings):
        if settings['screen'] == self.fail_window:
            raise RuntimeError('simulated window creation failure')
        window = Window(self, **settings)
        self.windows.append(window)
        return window

    def wait(self, seconds):
        self.now += seconds

    def should_abort(self):
        if self.abort_sent or not self.abort:
            return False
        op = next((win for win in self.windows if win.settings['screen'] == 0), None)
        banner = op.history[-1][1][0] if op and op.history else ''
        elapsed = self.now - op.history[-1][0] if op and op.history else 0
        match = (
            (self.abort == 'start_countdown' and '開始マーカーまで' in banner and elapsed >= .1)
            or (self.abort == 'start_cue' and '開始マーカーを入力' in banner and elapsed >= .1)
            or (self.abort == 'end_countdown' and '終了マーカーまで' in banner and elapsed >= .1)
            or (self.abort == 'end_cue' and '終了マーカーを入力' in banner and elapsed >= .1)
            or (self.abort == 'task' and self.trial == 2 and elapsed >= .05)
            or (self.abort == 'calibration' and 'CALIBRATION: enter' in banner and elapsed >= .1)
            or (self.abort == 'ready' and '準備ができたらSPACE' in banner)
            or (self.abort == 'rest' and op and '段階: 休憩' in op.history[-1][1][1] and elapsed >= .1)
            or (self.abort == 'tlx' and self.tlx_active and self.tlx_answers == 2)
        )
        if match:
            self.abort_sent = True
        return match

    def load(self, output_dir, full=False):
        psychopy = types.ModuleType('psychopy')
        psychopy.__version__ = 'mock'
        psychopy.core = types.SimpleNamespace(Clock=lambda: Clock(self), wait=self.wait)
        psychopy.visual = types.SimpleNamespace(Window=self.window, TextStim=Text)
        psychopy.visual.Circle = lambda win, **kwargs: Text(win, text='calibration_dot', **kwargs)
        hardware = types.ModuleType('psychopy.hardware')
        hardware.keyboard = types.SimpleNamespace(Keyboard=lambda: Keyboard(self))
        tobii = types.ModuleType('tobii_research')
        tobii.get_system_time_stamp = lambda: int(round(self.now * 1e6))
        tobii.CALIBRATION_STATUS_SUCCESS = 'success'

        class Calibration:
            def __init__(self, tracker):
                pass
            def enter_calibration_mode(inner):
                pass
            def leave_calibration_mode(inner):
                self.calibration_left = True
            def collect_data(inner, x, y):
                return 'success'
            def compute_and_apply(inner):
                return types.SimpleNamespace(status='success')

        tobii.ScreenBasedCalibration = Calibration
        with patch.dict(sys.modules, {'psychopy': psychopy, 'psychopy.hardware': hardware,
                                      'tobii_research': tobii}):
            spec = importlib.util.spec_from_file_location('nback_under_test', str(TASK_PATH))
            task = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(task)
        task.OUTPUT_DIR = Path(output_dir)
        if not full:
            task.N_TRIALS = 3
            task.N_BLOCKS = 2
            task.REST_SECONDS = 3.0
            task.SYNC_COUNTDOWN_SECONDS = 2
        task.SKIP_CALIBRATION = True
        task.SAVE_CALIBRATION_DATA = False
        self.validate_screens = task.validate_screen_configuration
        task.validate_screen_configuration = lambda: None
        task.StreamRecorder = lambda base: Recorder(self, base)
        task.select_tracker = lambda: types.SimpleNamespace(
            get_all_gaze_output_frequencies=lambda: [1200], set_gaze_output_frequency=lambda hz: None)
        task.tracker_info = lambda tracker: {'model': 'mock Spectrum', 'gaze_frequency_hz': 1200}
        if not full:
            task.make_design = lambda n, count, seed: [
                {'trial': 1, 'stimulus': '0', 'is_target': 0},
                {'trial': 2, 'stimulus': '0', 'is_target': 1},
                {'trial': 3, 'stimulus': '1', 'is_target': 0},
            ]

        def questionnaire(win, operator, session, stamp, check_abort):
            from src.nasa_tlx import SCALE_KEYS
            assert self.recorder.closed
            with Path(str(self.recorder.base) + '_trials.csv').open(encoding='utf-8') as stream:
                assert len(list(csv.DictReader(stream))) == task.N_TRIALS * task.N_BLOCKS
            self.tlx_active = True
            session.start(stamp('tlx_start', ''))
            for key, value in zip(SCALE_KEYS, (0, 20, 40, 60, 80, 100)):
                check_abort()
                session.set_rating(key, value)
                self.tlx_answers += 1
                stamp('tlx_response', key)
                self.wait(.01)
            session.finish(stamp('tlx_end', ''))
            return session.score

        task.run_raw_tlx = questionnaire
        return task


class DualScreenTests(unittest.TestCase):
    def read_outputs(self, root):
        root = Path(root)
        metadata = json.loads(next(root.rglob('*_metadata.json')).read_text(encoding='utf-8'))
        with next(root.rglob('*_events.csv')).open(encoding='utf-8', newline='') as stream:
            events = list(csv.DictReader(stream))
        with next(root.rglob('*_trials.csv')).open(encoding='utf-8', newline='') as stream:
            trials = list(csv.DictReader(stream))
        return metadata, events, trials

    def run_task(self, root, full=False, **kwargs):
        rig = Rig(**kwargs)
        task = rig.load(root, full=full)
        with redirect_stdout(io.StringIO()):
            task.main('DNV', 2)
        return (rig, task) + self.read_outputs(root)

    def test_full_540_second_protocol_and_sync(self):
        with tempfile.TemporaryDirectory() as root:
            rig, task, metadata, events, trials = self.run_task(root, full=True)
            with next(Path(root).rglob('*_tlx.csv')).open(encoding='utf-8-sig') as stream:
                tlx = next(csv.DictReader(stream))
        self.assertEqual(metadata['status'], 'completed')
        self.assertEqual(metadata['completed_trials'], 180)
        self.assertEqual(metadata['completed_blocks'], 6)
        self.assertEqual(metadata['planned_set_seconds'], 540)
        self.assertAlmostEqual(metadata['actual_set_seconds'], 540, delta=.04)
        self.assertEqual([int(row['trial']) for row in trials], list(range(1, 181)))
        self.assertEqual(tlx['status'], 'completed')
        self.assertEqual(float(tlx['raw_tlx']), 50)
        self.assertEqual(metadata['raw_tlx'], 50)
        def time(name, block):
            return next(int(event['system_time_stamp_us']) / 1e6 for event in events
                        if event['event'] == name and int(event['block']) == block)
        set_start = time('set_start', 0)
        self.assertAlmostEqual(set_start - time('sync_start_countdown', 1), 10, delta=.04)
        for block in range(1, 7):
            start, end, rest_end = time('task_start', block), time('task_end', block), time('rest_end', block)
            self.assertAlmostEqual(start - set_start, (block - 1) * 90, delta=.04)
            self.assertAlmostEqual(end - start, 60, delta=.04)
            self.assertAlmostEqual(rest_end - end, 30, delta=.04)
            self.assertAlmostEqual(start - time('sync_start_countdown', block), 10, delta=.04)
            self.assertAlmostEqual(time('sync_start_cue', block) - start, 0, delta=.04)
            self.assertAlmostEqual(end - time('sync_end_countdown', block), 10, delta=.04)
            self.assertAlmostEqual(time('sync_end_cue', block) - end, 0, delta=.04)
            for phase in ('start', 'end'):
                self.assertAlmostEqual(time('sync_' + phase + '_cue_end', block)
                                       - time('sync_' + phase + '_cue', block), 1, delta=.04)
            block_trials = [row for row in trials if int(row['block']) == block]
            self.assertEqual([int(row['trial_in_block']) for row in block_trials], list(range(1, 31)))
            self.assertEqual(block_trials[0]['is_target'], '0')
            for row in block_trials:
                onset = int(row['stim_on_us']) / 1e6
                self.assertAlmostEqual(int(row['stim_off_us']) / 1e6 - onset, .5, delta=.04)
                self.assertAlmostEqual(int(row['response_window_end_us']) / 1e6 - onset, 2, delta=.04)
                self.assertIn(row['stimulus'], '0123456789')
                self.assertLess(abs(float(row['onset_error_seconds'])), .04)
            self.assertGreaterEqual(int(block_trials[25]['stim_on_us']) / 1e6,
                                    time('sync_end_countdown', block) - .04)
            for moment, drawn, recording in rig.windows[1].history:
                if end + .04 < moment < rest_end - .04:
                    self.assertEqual(drawn, ['+'])
                    self.assertFalse(recording)
        self.assertEqual(len([event for event in events if event['event'] == 'sync_start_cue']), 6)
        self.assertLessEqual(rig.subscribe_time, time('sync_start_countdown', 1))
        self.assertGreaterEqual(rig.unsubscribe_time + 1e-6, time('set_end', 0))
        self.assertLessEqual(rig.unsubscribe_time, time('tlx_start', 0))
        self.assertEqual(rig.recorder.close_calls, 1)
        self.assertEqual(rig.recorder.unsubscribe_calls, 1)
        self.assertTrue(all(window.closed for window in rig.windows))
        self.assertGreater(metadata['participant_frame_timing']['recorded_intervals'], 21000)
        self.assertLess(metadata['participant_frame_timing']['max_interval_seconds'], .04)

    def test_window_settings_and_trial_scores(self):
        with tempfile.TemporaryDirectory() as root:
            rig, task, metadata, events, trials = self.run_task(root)
        operator, participant = rig.windows
        self.assertFalse(operator.settings['fullscr'])
        self.assertTrue(operator.settings['allowGUI'])
        self.assertFalse(operator.settings['waitBlanking'])
        self.assertFalse(operator.settings['checkTiming'])
        self.assertEqual(operator.size, task.OPERATOR_WINDOW_SIZE)
        self.assertTrue(participant.settings['fullscr'])
        self.assertTrue(participant.settings['waitBlanking'])
        self.assertEqual(metadata['completed_blocks'], 2)
        self.assertEqual([row['response_key'] for row in trials], ['left', 'right', '', '', '', ''])
        self.assertEqual([row['correct'] for row in trials], ['1', '1', '0', '0', '0', '0'])
        self.assertEqual(trials[-1]['rt_seconds'], '')
        self.assertTrue(all(row['completed'] == '1' for row in trials))
        self.assertEqual(sum(text.win is operator for text in rig.texts), 2)

    def test_normal_dashboard_updates_are_limited_to_5hz(self):
        with tempfile.TemporaryDirectory() as root:
            rig = Rig()
            task = rig.load(root)
            window = rig.window(screen=0, waitBlanking=False)
            operator = task.OperatorDisplay(window, task.load_nback_assignment(task.COUNTERBALANCE_CSV, 'DNV', 2))
            for step in range(100):
                operator.detail = str(step)
                operator.render()
                rig.wait(.02)
        times = [moment for moment, _, _ in window.history]
        self.assertGreater(len(times), 5)
        self.assertTrue(all(b - a >= .2 - 1e-8 for a, b in zip(times, times[1:])))

    def test_escape_preserves_partial_trials_and_tlx(self):
        for phase in ('start_countdown', 'start_cue', 'task', 'end_countdown', 'end_cue', 'rest', 'tlx'):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as root:
                rig, task, metadata, events, trials = self.run_task(root, abort=phase)
                self.assertEqual(metadata['status'], 'aborted')
                self.assertTrue(rig.abort_sent)
                self.assertTrue(rig.recorder.closed)
                self.assertEqual(rig.recorder.close_calls, 1)
                self.assertTrue(all(window.closed for window in rig.windows))
                self.assertIn('experiment_aborted', [event['event'] for event in events])
                if phase == 'tlx':
                    self.assertEqual(metadata['completed_trials'], 6)
                    self.assertEqual(metadata['completed_blocks'], 2)
                    self.assertEqual(metadata['tlx_status'], 'aborted')
                    self.assertIsNone(metadata['raw_tlx'])
                    with next(Path(root).rglob('*_tlx.csv')).open(encoding='utf-8-sig') as stream:
                        row = next(csv.DictReader(stream))
                    self.assertEqual(row['physical_demand'], '20')
                    self.assertEqual(row['temporal_demand'], '')
                    self.assertEqual(row['raw_tlx'], '')
                else:
                    self.assertEqual(metadata['tlx_status'], 'not_started')
                if phase == 'start_countdown':
                    self.assertIsNone(metadata['participant_frame_timing'])
                if phase in ('task', 'start_cue', 'end_countdown'):
                    self.assertEqual(trials[-1]['completed'], '0')
                    self.assertEqual(trials[-1]['correct'], '')
                    self.assertNotEqual(trials[-1]['response_window_end_us'], '')

    def test_calibration_complete_and_escape(self):
        for abort in (None, 'calibration', 'ready'):
            with self.subTest(abort=abort), tempfile.TemporaryDirectory() as root:
                rig = Rig(abort=abort)
                task = rig.load(root)
                task.SKIP_CALIBRATION = False
                with redirect_stdout(io.StringIO()):
                    task.main('DNV', 2)
                metadata, events, trials = self.read_outputs(root)
                self.assertEqual(metadata['status'], 'completed' if abort is None else 'aborted')
                self.assertTrue(rig.recorder.closed)
                self.assertTrue(all(window.closed for window in rig.windows))
                if abort != 'ready':
                    self.assertTrue(rig.calibration_left)

    def test_failure_still_saves_and_closes(self):
        for options in ({'fail_window': 1}, {'unsubscribe_error': True}):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as root:
                rig = Rig(**options)
                task = rig.load(root)
                with self.assertRaises(RuntimeError), redirect_stdout(io.StringIO()):
                    task.main('DNV', 2)
                metadata, events, trials = self.read_outputs(root)
                self.assertEqual(metadata['status'], 'failed')
                self.assertEqual(metadata['tlx_status'], 'not_started')
                self.assertTrue(rig.recorder.closed)
                self.assertTrue(all(window.closed for window in rig.windows))

    def test_design_resets_each_block(self):
        with tempfile.TemporaryDirectory() as root:
            rig = Rig()
            task = rig.load(root, full=True)
            for n in range(1, 5):
                for seed in range(6):
                    design = task.make_design(n, 30, seed)
                    self.assertTrue(all(row['is_target'] == 0 for row in design[:n]))
                    for i, row in enumerate(design):
                        self.assertEqual(row['is_target'], int(i >= n and row['stimulus'] == design[i-n]['stimulus']))

    def test_screen_validation(self):
        with tempfile.TemporaryDirectory() as root:
            rig = Rig()
            task = rig.load(root)
            pyglet = types.ModuleType('pyglet')
            pyglet.canvas = types.SimpleNamespace(get_display=lambda: types.SimpleNamespace(get_screens=lambda: [0]))
            with patch.dict(sys.modules, {'pyglet': pyglet}):
                with self.assertRaisesRegex(ValueError, 'Found 1'):
                    rig.validate_screens()
                task.OPERATOR_SCREEN_INDEX = 1
                with self.assertRaisesRegex(ValueError, 'different monitors'):
                    rig.validate_screens()


if __name__ == '__main__':
    unittest.main()
