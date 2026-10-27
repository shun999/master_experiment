# -*- coding: utf-8 -*-
from contextlib import redirect_stdout
import importlib.util
import io
import os
from pathlib import Path
import random
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from src import demo_runtime as runtime
from tests.test_nback_dual_screen import Rig, Clock, Text


ROOT = Path(__file__).resolve().parents[1]


class InputClock(Clock):
    def reset(self):
        super().reset()
        keyboard = self.rig.demo_keyboard
        session = self.rig.session
        keyboard.phase, keyboard.row = session.phase, session.current_row
        if session.phase == 'menu':
            self.rig.completed_runs.append([dict(row) for row in session.last_results])
            keyboard.menu_choice = self.rig.menu_actions.pop(0) if self.rig.menu_actions else 'escape'
        if session.phase == 'guide' and session.current_row['trial'] == 1:
            self.rig.guide_runs += 1


class DemoKeyboard:
    def __init__(self, rig):
        if rig.fail_keyboard:
            raise RuntimeError('simulated keyboard failure')
        self.rig, self.phase, self.row = rig, None, None
        rig.demo_keyboard = self
        self.clock = InputClock(rig)
        self.emitted = set()

    def clearEvents(self):
        self.emitted.clear()

    def getKeys(self, keyList, waitRelease=False, clear=True):
        assert waitRelease is False and clear is True
        elapsed = self.clock.getTime()
        if self.rig.abort_phase == self.phase and elapsed > .05:
            return [types.SimpleNamespace(name='escape', rt=elapsed)]
        if self.phase == 'menu':
            script = [(self.menu_choice, .06)]
        elif self.phase in ('intro', 'practice_ready', 'summary', 'review'):
            script = [('space', .06)]
        elif self.phase == 'guide':
            row = self.row
            if not row['response_required']:
                script = [('right', .05), ('space', .1)]
            elif self.rig.task == 'arithmetic':
                script = [('space', .05), ('num_9', .1), ('backspace', .15), ('num_1', .2), ('delete', .25)]
                script += [('num_' + digit, .3 + index * .05) for index, digit in enumerate(str(row['expected']))]
                script += [('return', .6), ('space', .7)]
            else:
                opposite = 'left' if row['expected'] == 'right' else 'right'
                script = [('space', .05), (opposite, .1), (row['expected'], .2), ('space', .3)]
        elif self.phase == 'practice':
            row, local = self.row, self.row['trial']
            deadline = row['input_deadline_rt_seconds']
            if self.rig.task == 'arithmetic':
                answer = str(row['expected'])
                if local == 1:
                    script = [('num_9', .1), ('backspace', .2)]
                    script += [('num_' + digit, .3 + i * .1) for i, digit in enumerate(answer)]
                    script += [('return', .6)]
                elif local == 2:
                    script = [('num_0', .2)]
                elif local == 3:
                    script = []
                else:
                    script = [('num_' + digit, .2 + i * .1) for i, digit in enumerate(answer[:-1])]
                    script += [('num_' + answer[-1], deadline - .00001), ('num_9', deadline + .00001)]
            else:
                expected = row['expected']
                opposite = 'left' if expected == 'right' else 'right'
                if not row['response_required']:
                    script = [('right', .2)]
                elif local == 1:
                    script = [(expected, .2), (opposite, .3)]
                elif local == 2:
                    script = [(opposite, .6)]
                elif local == 3:
                    script = []
                elif local == 4:
                    script = [(expected, deadline - .00001), (opposite, deadline + .00001)]
                elif local == 5:
                    script = [(expected, deadline + .00001)]
                elif local == 6:
                    script = [(opposite, .200002), (expected, .200001)]
                else:
                    script = [(expected, .6)]
        else:
            script = []
        keys = []
        for index, (name, rt) in enumerate(script):
            if index not in self.emitted and name in keyList and rt <= elapsed:
                self.emitted.add(index)
                keys.append(types.SimpleNamespace(name=name, rt=rt))
        return keys


class DemoRig(Rig):
    def __init__(self, task, abort_phase=None, menu_actions=(), fail_keyboard=False, fail_view=False):
        super().__init__()
        self.task, self.abort_phase = task, abort_phase
        self.menu_actions = list(menu_actions)
        self.fail_keyboard, self.fail_view = fail_keyboard, fail_view
        self.session, self.demo_keyboard = None, None
        self.guide_runs, self.completed_runs = 0, []
        self.frames = []

    def run(self):
        psychopy = types.ModuleType('psychopy')
        psychopy.core = types.SimpleNamespace(Clock=lambda: Clock(self))
        def text(win, **settings):
            if self.fail_view:
                raise RuntimeError('simulated text creation failure')
            return Text(win, **settings)
        psychopy.visual = types.SimpleNamespace(Window=self.window, TextStim=text)
        hardware = types.ModuleType('psychopy.hardware')
        def keyboard():
            # A visible preparation page must precede keyboard initialisation.
            assert self.windows and self.windows[0].history
            assert 'デモを準備しています。' in self.windows[0].history[0][1]
            return DemoKeyboard(self)
        hardware.keyboard = types.SimpleNamespace(Keyboard=keyboard)
        pyglet = types.ModuleType('pyglet')
        pyglet.canvas = types.SimpleNamespace(get_display=lambda: types.SimpleNamespace(get_screens=lambda: [0]))
        session_class, view_class = runtime.DemoSession, runtime.DemoView
        def session(*args, **kwargs):
            self.session = session_class(*args, rng=random.Random(123), **kwargs)
            return self.session
        rig = self
        class View(view_class):
            def paint(self, *args, **kwargs):
                super().paint(*args, **kwargs)
                phase = rig.session.phase if rig.session is not None else 'preparation'
                rig.frames.append((rig.now, phase, self.stimulus.text, self.answer.text, self.detail.text))
        with patch.dict(sys.modules, {'psychopy': psychopy, 'psychopy.hardware': hardware, 'pyglet': pyglet}), \
                patch.object(runtime, 'DemoSession', side_effect=session), patch.object(runtime, 'DemoView', View), \
                redirect_stdout(io.StringIO()):
            return runtime.run_demo(self.task)


class DemoRuntimeTests(unittest.TestCase):
    def test_three_independent_entrypoints_use_their_fixed_tasks(self):
        for task in ('nback', 'arithmetic', 'pvsat'):
            spec = importlib.util.spec_from_file_location('demo_' + task, str(ROOT / ('demo_' + task + '.py')))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            with patch.object(module, 'run_demo', return_value='done') as run:
                self.assertEqual(module.main(), 'done')
            run.assert_called_once_with(task, screen_index=1, full_screen=True, window_size=(1280, 720))

    def test_single_monitor_fallback_and_invalid_screen(self):
        pyglet = types.ModuleType('pyglet')
        pyglet.canvas = types.SimpleNamespace(get_display=lambda: types.SimpleNamespace(get_screens=lambda: [0]))
        with patch.dict(sys.modules, {'pyglet': pyglet}):
            self.assertEqual(runtime.select_demo_screen(1), 0)
            self.assertEqual(runtime.select_demo_screen(0), 0)
            for value in (-1, True, 3):
                with self.assertRaises(ValueError):
                    runtime.select_demo_screen(value)
            pyglet.canvas.get_display = lambda: types.SimpleNamespace(get_screens=lambda: [0, 1])
            self.assertEqual(runtime.select_demo_screen(1), 1)

    def test_full_guides_20_second_practice_and_review_without_output_files(self):
        for task, count, correct in (('nback', 10, 7), ('arithmetic', 4, 2), ('pvsat', 8, 4)):
            with self.subTest(task=task), tempfile.TemporaryDirectory() as directory:
                original_cwd = Path.cwd()
                os.chdir(directory)
                try:
                    rig = DemoRig(task)
                    results = rig.run()
                    self.assertEqual(list(Path(directory).iterdir()), [])
                finally:
                    os.chdir(str(original_cwd))
                self.assertEqual(len(rig.windows), 1)
                self.assertEqual(rig.windows[0].settings['screen'], 0)
                self.assertTrue(rig.windows[0].settings['waitBlanking'])
                self.assertTrue(rig.windows[0].closed)
                self.assertEqual(len(results), count)
                self.assertEqual(rig.guide_runs, 1)
                self.assertEqual(sum(row['correct'] is True for row in results), correct)
                self.assertTrue(all(row['completed'] for row in results))
                self.assertAlmostEqual(rig.session.last_duration, 20, delta=.04)
                spec = rig.session.spec
                for index, row in enumerate(results):
                    self.assertAlmostEqual(row['onset_seconds'], index * spec.interval, delta=.04)
                    self.assertAlmostEqual(row['stimulus_end_seconds'] - row['onset_seconds'], spec.stimulus_seconds, delta=.04)
                    self.assertAlmostEqual(row['response_window_end_seconds'] - row['onset_seconds'], spec.interval, delta=.04)
                    if not row['response_required']:
                        self.assertIsNone(row['correct'])
                        self.assertEqual(row['response'], '回答なし（記憶のみ）')
                    elif (task == 'arithmetic' and index == 3) or (task != 'arithmetic' and index == 3):
                        self.assertTrue(row['correct'])  # Press just before deadline, delivered on closing flip.
                practice = [frame for frame in rig.frames if frame[1] == 'practice']
                self.assertTrue(all(not frame[4] for frame in practice))
                self.assertTrue(any('正解：' in frame[4] for frame in rig.frames if frame[1] == 'guide'))
                self.assertTrue(any('正解です' in frame[4] for frame in rig.frames if frame[1] == 'guide'))
                self.assertTrue(any('回答し直してください' in frame[4] for frame in rig.frames if frame[1] == 'guide'))
                if task != 'arithmetic':
                    self.assertTrue(any(frame[2] == '+' for frame in practice))
                    self.assertTrue(any('違います' in frame[4] for frame in rig.frames))
                self.assertTrue(any(frame[1] == 'review' and '自分の回答' in frame[4] for frame in rig.frames))
                self.assertTrue(all(stim.settings['font'] in ('Yu Gothic', 'Arial') for stim in rig.texts))

    def test_r_repeats_only_practice_g_restarts_guide_with_fresh_state(self):
        for task in ('nback', 'arithmetic', 'pvsat'):
            with self.subTest(task=task):
                rig = DemoRig(task, menu_actions=('r', 'g', 'escape'))
                rig.run()
                self.assertEqual(rig.guide_runs, 2)
                self.assertEqual(len(rig.completed_runs), 3)
                self.assertEqual(len(set(rig.session.practice_seeds)), 3)
                self.assertNotEqual(rig.completed_runs[0], rig.completed_runs[1])
                for run in rig.completed_runs:
                    self.assertEqual([row['trial'] for row in run], list(range(1, len(run) + 1)))
                    self.assertIsNone(run[0]['previous'])
                    self.assertTrue(all(row['completed'] for row in run))
                self.assertTrue(rig.windows[0].closed)

    def test_escape_closes_window_from_every_phase(self):
        for phase in ('intro', 'guide', 'practice_ready', 'practice', 'summary', 'review', 'menu'):
            with self.subTest(phase=phase):
                rig = DemoRig('nback', abort_phase=phase)
                rig.run()
                self.assertTrue(rig.windows[0].closed)

    def test_startup_errors_close_created_window(self):
        for options in ({'fail_keyboard': True}, {'fail_view': True}):
            with self.subTest(options=options):
                rig = DemoRig('nback', **options)
                with self.assertRaisesRegex(RuntimeError, 'simulated'):
                    rig.run()
                self.assertTrue(rig.windows[0].closed)


if __name__ == '__main__':
    unittest.main()
