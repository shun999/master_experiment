# -*- coding: utf-8 -*-
"""Hardware-free checks: python -m unittest test_nback_dual_screen -v."""

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


TASK_PATH = Path(__file__).with_name('psychopy2022_spectrum1200_nback_lightweight.py')


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

    def subscribe(self, tracker, kind, constant, required=False):
        self.attempts[kind] = True
        if kind == 'gaze':
            self.rig.subscribe_time = self.rig.now

    def unsubscribe_all(self, tracker):
        self.rig.unsubscribe_time = self.rig.now
        if self.rig.unsubscribe_error:
            raise RuntimeError('simulated unsubscribe failure')

    def close(self):
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
        )
        if match:
            self.abort_sent = True
        return match

    def load(self, output_dir):
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
        task.N_TRIALS = 3
        task.SKIP_CALIBRATION = True
        task.SAVE_CALIBRATION_DATA = False
        self.validate_screens = task.validate_screen_configuration
        task.validate_screen_configuration = lambda: None
        task.StreamRecorder = lambda base: Recorder(self, base)
        task.select_tracker = lambda: types.SimpleNamespace(
            get_all_gaze_output_frequencies=lambda: [1200], set_gaze_output_frequency=lambda hz: None)
        task.tracker_info = lambda tracker: {'model': 'mock Spectrum', 'gaze_frequency_hz': 1200}
        task.make_design = lambda n, count, seed: [
            {'trial': 1, 'stimulus': '0', 'is_target': 0},
            {'trial': 2, 'stimulus': '0', 'is_target': 1},
            {'trial': 3, 'stimulus': '1', 'is_target': 0},
        ]
        return task


class DualScreenTests(unittest.TestCase):
    def run_task(self, root, **kwargs):
        rig = Rig(**kwargs)
        task = rig.load(root)
        with redirect_stdout(io.StringIO()):
            task.main()
        metadata = json.loads(next(Path(root).glob('*_metadata.json')).read_text(encoding='utf-8'))
        with next(Path(root).glob('*_events.csv')).open(encoding='utf-8', newline='') as stream:
            events = list(csv.DictReader(stream))
        with next(Path(root).glob('*_trials.csv')).open(encoding='utf-8', newline='') as stream:
            trials = list(csv.DictReader(stream))
        return rig, task, metadata, events, trials

    def test_protocol_timing_and_fixation(self):
        with tempfile.TemporaryDirectory() as root:
            rig, task, metadata, events, trials = self.run_task(root)
        names = [event['event'] for event in events]
        expected = ['sync_start_countdown', 'sync_start_cue', 'sync_start_cue_end',
                    'task_start', 'task_end', 'sync_end_countdown', 'sync_end_cue', 'sync_end_cue_end']
        self.assertEqual([name for name in names if name in expected], expected)
        times = {event['event']: int(event['system_time_stamp_us']) / 1e6 for event in events}
        for phase in ('start', 'end'):
            prefix = 'sync_' + phase
            self.assertAlmostEqual(times[prefix + '_cue'] - times[prefix + '_countdown'], 10, delta=.04)
            self.assertAlmostEqual(times[prefix + '_cue_end'] - times[prefix + '_cue'], 1, delta=.04)
        self.assertEqual(times['task_start'], int(trials[0]['stim_on_us']) / 1e6)
        self.assertGreaterEqual(times['task_start'], times['sync_start_cue_end'])
        self.assertGreater(times['sync_end_countdown'], times['task_end'])
        self.assertLessEqual(rig.subscribe_time, times['sync_start_countdown'])
        self.assertGreaterEqual(rig.unsubscribe_time + 1e-6, times['sync_end_cue_end'])
        participant = rig.windows[1]
        for time, drawn, recording in participant.history:
            in_sync = any(times['sync_' + phase + '_countdown'] <= time <= times['sync_' + phase + '_cue_end']
                          for phase in ('start', 'end'))
            if in_sync:
                self.assertEqual(drawn, ['+'])
                self.assertFalse(recording)
            if recording:
                self.assertGreaterEqual(time + 1e-6, times['task_start'])
                self.assertLessEqual(time, times['task_end'] + 1e-6)
        self.assertEqual(metadata['status'], 'completed')
        self.assertEqual(metadata['completed_trials'], 3)
        self.assertGreater(metadata['participant_frame_timing']['recorded_intervals'], 300)
        self.assertLess(metadata['participant_frame_timing']['max_interval_seconds'], .04)
        self.assertEqual(metadata['sync_countdown_seconds'], 10)
        self.assertEqual(metadata['sync_cue_seconds'], 1)
        self.assertTrue(rig.recorder.closed)
        self.assertTrue(all(window.closed for window in rig.windows))

    def test_dashboard_settings_rate_and_trial_records(self):
        with tempfile.TemporaryDirectory() as root:
            rig, task, metadata, events, trials = self.run_task(root)
        operator, participant = rig.windows
        self.assertEqual(operator.settings['screen'], 0)
        self.assertFalse(operator.settings['fullscr'])
        self.assertTrue(operator.settings['allowGUI'])
        self.assertEqual(operator.size, task.OPERATOR_WINDOW_SIZE)
        self.assertFalse(metadata['operator_fullscreen'])
        self.assertEqual(metadata['operator_screen_pixels'], list(task.OPERATOR_WINDOW_SIZE))
        self.assertFalse(operator.settings['waitBlanking'])
        self.assertFalse(operator.settings['checkTiming'])
        self.assertTrue(participant.settings['waitBlanking'])
        self.assertTrue(participant.settings['fullscr'])
        self.assertEqual(participant.settings['screen'], 1)
        self.assertEqual(sum(text.win is operator for text in rig.texts), 2)
        times = {event['event']: int(event['system_time_stamp_us']) / 1e6 for event in events}
        updates = [time for time, drawn, recording in operator.history
                   if times['task_start'] <= time < times['task_end']]
        self.assertGreater(len(updates), 3)
        self.assertTrue(all(right - left >= .2 - 1e-8 for left, right in zip(updates, updates[1:])))
        self.assertIn('66.7% (2/3)', operator.history[-1][1][1])
        self.assertIn('未集計', operator.history[0][1][1])
        self.assertEqual([row['response_key'] for row in trials], ['left', 'right', ''])
        self.assertEqual([row['correct'] for row in trials], ['1', '1', '0'])
        self.assertEqual(trials[-1]['rt_seconds'], '')
        for row in trials[:2]:
            self.assertGreaterEqual(float(row['rt_seconds']), .2)
            self.assertLess(float(row['rt_seconds']), .24)
        self.assertEqual(len([event for event in events if event['event'] == 'response_detected']), 2)

    def test_escape_at_each_recording_stage_preserves_data(self):
        for phase in ('start_countdown', 'start_cue', 'task', 'end_countdown', 'end_cue'):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as root:
                rig, task, metadata, events, trials = self.run_task(root, abort=phase)
                self.assertEqual(metadata['status'], 'aborted')
                self.assertTrue(rig.abort_sent)
                self.assertTrue(rig.recorder.closed)
                self.assertTrue(all(window.closed for window in rig.windows))
                names = [event['event'] for event in events]
                self.assertIn('experiment_aborted', names)
                self.assertNotIn('sync_end_cue_end', names)
                if phase in ('start_countdown', 'start_cue', 'task'):
                    self.assertNotIn('sync_end_countdown', names)
                if phase in ('start_countdown', 'start_cue'):
                    self.assertIsNone(metadata['participant_frame_timing'])
                if phase == 'task':
                    self.assertEqual(metadata['completed_trials'], 1)
                    self.assertEqual(trials[-1]['completed'], '0')
                    self.assertEqual(trials[-1]['correct'], '')

    def test_calibration_escape_leaves_sdk_mode(self):
        with tempfile.TemporaryDirectory() as root:
            rig = Rig(abort='calibration')
            task = rig.load(root)
            task.SKIP_CALIBRATION = False
            with redirect_stdout(io.StringIO()):
                task.main()
            metadata = json.loads(next(Path(root).glob('*_metadata.json')).read_text(encoding='utf-8'))
        self.assertEqual(metadata['status'], 'aborted')
        self.assertTrue(rig.calibration_left)
        self.assertTrue(rig.recorder.closed)
        self.assertTrue(all(window.closed for window in rig.windows))

    def test_ready_escape_does_not_start_recording(self):
        with tempfile.TemporaryDirectory() as root:
            rig, task, metadata, events, trials = self.run_task(root, abort='ready')
        self.assertEqual(metadata['status'], 'aborted')
        self.assertIsNone(rig.subscribe_time)
        self.assertIsNone(metadata['participant_frame_timing'])
        self.assertEqual(trials, [])
        self.assertTrue(rig.recorder.closed)
        self.assertTrue(all(window.closed for window in rig.windows))

    def test_successful_calibration_then_task(self):
        with tempfile.TemporaryDirectory() as root:
            rig = Rig()
            task = rig.load(root)
            task.SKIP_CALIBRATION = False
            with redirect_stdout(io.StringIO()):
                task.main()
            metadata = json.loads(next(Path(root).glob('*_metadata.json')).read_text(encoding='utf-8'))
        self.assertEqual(metadata['status'], 'completed')
        self.assertEqual(metadata['calibration']['status'], 'success')
        self.assertTrue(rig.calibration_left)
        self.assertEqual(metadata['completed_trials'], 3)
        self.assertTrue(all(window.closed for window in rig.windows))

    def test_partial_window_creation_failure_still_saves_and_closes(self):
        with tempfile.TemporaryDirectory() as root:
            rig = Rig(fail_window=1)
            task = rig.load(root)
            with self.assertRaisesRegex(RuntimeError, 'window creation'), redirect_stdout(io.StringIO()):
                task.main()
            metadata = json.loads(next(Path(root).glob('*_metadata.json')).read_text(encoding='utf-8'))
        self.assertEqual(metadata['status'], 'failed')
        self.assertEqual(len(rig.windows), 1)
        self.assertTrue(rig.windows[0].closed)
        self.assertTrue(rig.recorder.closed)

    def test_cleanup_error_does_not_skip_window_close_or_save(self):
        with tempfile.TemporaryDirectory() as root:
            rig, task, metadata, events, trials = self.run_task(root, unsubscribe_error=True)
        self.assertEqual(metadata['status'], 'failed')
        self.assertIn('unsubscribe failure', metadata['error'])
        self.assertEqual(len(trials), 3)
        self.assertTrue(rig.recorder.closed)
        self.assertTrue(all(window.closed for window in rig.windows))

    def test_screen_validation_rejects_missing_and_duplicate_monitors(self):
        rig = Rig()
        with tempfile.TemporaryDirectory() as root:
            task = rig.load(root)
            pyglet = types.ModuleType('pyglet')
            pyglet.canvas = types.SimpleNamespace(get_display=lambda: types.SimpleNamespace(get_screens=lambda: [0]))
            with patch.dict(sys.modules, {'pyglet': pyglet}):
                with self.assertRaisesRegex(ValueError, 'Found 1'):
                    rig.validate_screens()
                task.OPERATOR_SCREEN_INDEX = 1
                with self.assertRaisesRegex(ValueError, 'different monitors'):
                    rig.validate_screens()
                task.OPERATOR_SCREEN_INDEX = -1
                with self.assertRaisesRegex(ValueError, 'nonnegative'):
                    rig.validate_screens()


if __name__ == '__main__':
    unittest.main()
