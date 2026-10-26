# -*- coding: utf-8 -*-
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

from experiment_assignment import load_arithmetic_assignment, prompt_arithmetic_assignment, AssignmentTableError
from test_experiment_assignment import Dialogs
from test_nback_dual_screen import Rig, Clock
from tobii_nback_1200hz_features import TrialLabels


TASK_PATH = Path(__file__).with_name('psychopy2022_spectrum1200_arithmetic_lightweight.py')


class InputClock(Clock):
    def reset(self):
        super().reset()
        self.rig.trial += 1
        if self.rig.trial == 1:
            self.rig.set_anchor = self.rig.now


class NumericKeyboard:
    def __init__(self, rig):
        self.rig = rig
        rig.numeric_keyboard = self
        self.clock = InputClock(rig)
        self.emitted = set()

    def clearEvents(self):
        self.emitted.clear()

    def getKeys(self, keyList, waitRelease=False, clear=True):
        assert waitRelease is False and clear is True
        if 'escape' in keyList and self.rig.should_abort():
            return [types.SimpleNamespace(name='escape', rt=self.clock.getTime())]
        if 'space' in keyList:
            return [types.SimpleNamespace(name='space', rt=0)]
        if not self.rig.trial or self.rig.trial > len(self.rig.answers):
            return []
        answer = str(self.rig.answers[self.rig.trial - 1])
        mode = (self.rig.trial - 1) % 4
        if mode == 0:
            script = [('num_' + digit, .2 + i * .2) for i, digit in enumerate(answer)]
            script.append(('return', 1.5))
        elif mode == 1:
            script = [('num_9', .2), ('backspace', .4), ('num_1', .6), ('delete', 3)]
            script.extend(('num_' + digit, 3.2 + i * .2) for i, digit in enumerate(answer.zfill(4)))
        elif mode == 2:
            script = []
        else:
            task = self.rig.task
            block = (self.rig.trial - 1) // task.N_TRIALS
            local = (self.rig.trial - 1) % task.N_TRIALS + 1
            deadline = (self.rig.set_anchor + block * (task.N_TRIALS * task.TRIAL_SECONDS + task.REST_SECONDS)
                        + local * task.TRIAL_SECONDS - self.clock.origin)
            script = [('num_' + digit, .2 + i * .2) for i, digit in enumerate(answer[:-1])]
            script.extend([('num_' + answer[-1], deadline - .00001), ('num_9', deadline + .00001)])
        keys = []
        for index, (name, rt) in enumerate(script):
            if index not in self.emitted and rt <= self.clock.getTime() and name in keyList:
                self.emitted.add(index)
                keys.append(types.SimpleNamespace(name=name, rt=rt))
        return keys


class ArithmeticRig(Rig):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.answers = []
        self.set_anchor = None

    def should_abort(self):
        if self.abort == 'edited':
            if self.trial == 2 and self.numeric_keyboard.clock.getTime() > .65:
                self.abort_sent = True
                return True
            return False
        return super().should_abort()

    def load_arithmetic(self, root):
        original = super().load(root, full=True)
        psychopy = types.ModuleType('psychopy')
        psychopy.__version__ = 'mock'
        psychopy.core, psychopy.visual = original.core, original.visual
        hardware = types.ModuleType('psychopy.hardware')
        hardware.keyboard = types.SimpleNamespace(Keyboard=lambda: NumericKeyboard(self))
        with patch.dict(sys.modules, {'psychopy': psychopy, 'psychopy.hardware': hardware,
                                      'tobii_research': original.tr}):
            spec = importlib.util.spec_from_file_location('arithmetic_under_test', str(TASK_PATH))
            task = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(task)
        self.task = task
        task.OUTPUT_DIR = Path(root)
        task.SKIP_CALIBRATION, task.SAVE_CALIBRATION_DATA = True, False
        task.SEED = 20261026
        task.validate_screen_configuration = lambda: None
        task.StreamRecorder = original.StreamRecorder
        task.select_tracker, task.tracker_info = original.select_tracker, original.tracker_info
        generate = task.make_arithmetic_design
        def design(level, count, seed):
            rows = generate(level, count, seed)
            self.answers.extend(row['correct_answer'] for row in rows)
            return rows
        task.make_arithmetic_design = design
        def tlx(win, operator, session, stamp, check_abort):
            from nasa_tlx import SCALE_KEYS
            assert self.recorder.closed and session.blocks == task.N_BLOCKS
            with Path(str(self.recorder.base) + '_trials.csv').open(encoding='utf-8') as stream:
                assert len(list(csv.DictReader(stream))) == task.N_TRIALS * task.N_BLOCKS
            self.tlx_active = True
            session.start(stamp('tlx_start', ''))
            for key in SCALE_KEYS:
                check_abort()
                session.set_rating(key, 50)
                self.tlx_answers += 1
                stamp('tlx_response', key)
            session.finish(stamp('tlx_end', ''))
        task.run_raw_tlx = tlx
        return task


class ArithmeticExperimentTests(unittest.TestCase):
    def test_default_dialog_executes_selected_arithmetic_condition(self):
        with tempfile.TemporaryDirectory() as root:
            rig = ArithmeticRig()
            task = rig.load_arithmetic(root)
            dialogs = Dialogs([[' afx ', '2']])
            psychopy = types.ModuleType('psychopy')
            psychopy.gui = types.SimpleNamespace(Dlg=dialogs.dialog)
            with patch.dict(sys.modules, {'psychopy': psychopy}), redirect_stdout(io.StringIO()):
                task.main()
            metadata, trials, events, inputs, tlx = self.read(root)
            self.assertEqual(metadata['participant'], 'AFX')
            self.assertEqual(metadata['session_number'], 2)
            self.assertEqual(metadata['difficulty_level'], 2)
            self.assertEqual(metadata['operand_digits'], 2)
            self.assertEqual(metadata['counterbalance_column'], 'arithmetic_2')
            self.assertEqual(metadata['completed_blocks'], 3)
            self.assertTrue(all(10 <= int(row['operand_a']) <= 99 and 10 <= int(row['operand_b']) <= 99 for row in trials))
            self.assertTrue(any('3ブロック' in text for text in dialogs.shown[0].texts))
            self.assertEqual(tlx[0]['session_number'], '2')

    def test_normal_operator_refresh_limit(self):
        with tempfile.TemporaryDirectory() as root:
            rig = ArithmeticRig()
            task = rig.load_arithmetic(root)
            window = rig.window(screen=0, waitBlanking=False)
            assignment = load_arithmetic_assignment(task.COUNTERBALANCE_CSV, 'AFX', 1)
            operator = task.OperatorDisplay(window, assignment)
            for step in range(100):
                operator.detail = str(step)
                operator.render()
                rig.wait(.02)
        times = [moment for moment, _, _ in window.history]
        self.assertGreater(len(times), 5)
        self.assertTrue(all(b - a >= .2 - 1e-8 for a, b in zip(times, times[1:])))

    def read(self, root):
        root = Path(root)
        metadata = json.loads(next(root.glob('*_metadata.json')).read_text(encoding='utf-8'))
        tables = []
        for suffix in ('trials', 'events', 'input', 'tlx'):
            matches = list(root.glob('*_' + suffix + '.csv'))
            if matches:
                with matches[0].open(encoding='utf-8-sig', newline='') as stream:
                    tables.append(list(csv.DictReader(stream)))
            else:
                tables.append([])
        return (metadata,) + tuple(tables)

    def run_task(self, root, **kwargs):
        rig = ArithmeticRig(**kwargs)
        task = rig.load_arithmetic(root)
        with redirect_stdout(io.StringIO()):
            task.main('DNV', 1)  # Arithmetic difficulty 3.
        return rig, task, self.read(root)

    def test_three_blocks_36_questions_270_seconds_sync_and_tlx(self):
        with tempfile.TemporaryDirectory() as root:
            rig, task, (metadata, trials, events, inputs, tlx) = self.run_task(root)
            labels = TrialLabels(self.recorder_prefix(rig), metadata)
        self.assertEqual(metadata['task'], 'arithmetic')
        self.assertEqual(metadata['difficulty_level'], 3)
        self.assertEqual(metadata['operand_digits'], 3)
        self.assertEqual(metadata['requested_blocks'], 3)
        self.assertEqual(metadata['completed_blocks'], 3)
        self.assertEqual(metadata['completed_trials'], 36)
        self.assertEqual(metadata['planned_set_seconds'], 270)
        self.assertAlmostEqual(metadata['actual_set_seconds'], 270, delta=.04)
        self.assertEqual([int(row['trial']) for row in trials], list(range(1, 37)))
        def time(name, block=0):
            return next(int(event['system_time_stamp_us']) / 1e6 for event in events
                        if event['event'] == name and int(event['block']) == block)
        start = time('set_start')
        self.assertAlmostEqual(start - time('sync_start_countdown', 1), 10, delta=.04)
        for block in range(1, 4):
            begin, end, rest_end = time('task_start', block), time('task_end', block), time('rest_end', block)
            self.assertAlmostEqual(begin - start, (block - 1) * 90, delta=.04)
            self.assertAlmostEqual(end - begin, 60, delta=.04)
            self.assertAlmostEqual(rest_end - end, 30, delta=.04)
            self.assertAlmostEqual(time('sync_start_cue', block) - begin, 0, delta=.04)
            self.assertAlmostEqual(time('sync_end_countdown', block) - begin, 50, delta=.04)
            self.assertAlmostEqual(time('sync_end_cue', block) - end, 0, delta=.04)
            rows = [row for row in trials if int(row['block']) == block]
            self.assertEqual(sum(int(row['has_carry']) for row in rows), 6)
            self.assertEqual(len(rows), 12)
            for row in rows:
                self.assertAlmostEqual((int(row['response_window_end_us']) - int(row['stim_on_us'])) / 1e6, 5, delta=.04)
                self.assertEqual(row['stim_off_us'], row['response_window_end_us'])
                self.assertEqual(row['auto_submit_us'], row['response_window_end_us'])
            self.assertEqual(labels.for_timestamp(int((end + 5) * 1e6)), ('', '', ''))
        self.assertEqual(sum(int(row['correct']) for row in trials), 27)
        for i, row in enumerate(trials):
            if i % 4 == 2:
                self.assertEqual(row['unanswered'], '1')
                self.assertEqual(row['first_input_rt_seconds'], '')
            else:
                self.assertEqual(int(row['answer_value']), int(row['correct_answer']))
            if i % 4 == 1:
                self.assertEqual(len(row['answer_text']), 4)
                self.assertAlmostEqual(float(row['last_edit_rt_seconds']), 3.8)
        self.assertTrue(any(row['action'] == 'backspace' for row in inputs))
        self.assertTrue(any(row['action'] == 'clear' for row in inputs))
        self.assertTrue(all(row['key_name'] != 'return' for row in inputs))
        self.assertEqual(tlx[0]['task'], 'arithmetic')
        self.assertEqual(tlx[0]['blocks'], '3')
        self.assertEqual(tlx[0]['n_back'], '')
        self.assertEqual(tlx[0]['session_number'], '1')
        self.assertEqual(tlx[0]['raw_tlx'], '50.0')
        self.assertLessEqual(rig.unsubscribe_time, time('tlx_start') + 1e-6)
        self.assertEqual(rig.recorder.close_calls, 1)
        self.assertTrue(all(window.closed for window in rig.windows))
        self.assertFalse(rig.windows[0].settings['fullscr'])
        self.assertFalse(rig.windows[0].settings['waitBlanking'])
        self.assertTrue(rig.windows[1].settings['waitBlanking'])
        self.assertEqual(len([event for event in events if event['event'] == 'sync_start_cue']), 3)
        self.assertLess(metadata['participant_frame_timing']['max_interval_seconds'], .04)

    def recorder_prefix(self, rig):
        return rig.recorder.base

    def test_abort_saves_input_and_ends_devices(self):
        for phase in ('start_countdown', 'task', 'edited', 'end_countdown', 'end_cue', 'rest', 'tlx'):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as root:
                rig, task, (metadata, trials, events, inputs, tlx) = self.run_task(root, abort=phase)
                self.assertEqual(metadata['status'], 'aborted')
                self.assertEqual(rig.recorder.close_calls, 1)
                self.assertTrue(all(window.closed for window in rig.windows))
                if phase == 'edited':
                    self.assertNotEqual(trials[-1]['answer_text'], '')
                    self.assertEqual(trials[-1]['completed'], '0')
                    self.assertEqual(trials[-1]['correct'], '')
                    self.assertEqual(trials[-1]['auto_submit_us'], '')
                    self.assertGreater(len(inputs), 0)
                if phase == 'tlx':
                    self.assertEqual(metadata['completed_blocks'], 3)
                    self.assertEqual(metadata['completed_trials'], 36)
                    self.assertEqual(tlx[0]['status'], 'aborted')
                    self.assertEqual(tlx[0]['raw_tlx'], '')

    def test_all_30_assignments_and_three_block_dialog(self):
        csv_path = TASK_PATH.with_name('counterbalance.csv')
        with csv_path.open(encoding='utf-8-sig') as stream:
            rows = list(csv.DictReader(stream))
        for row in rows:
            for session in range(1, 4):
                a = load_arithmetic_assignment(csv_path, row['participant_id'], session)
                self.assertEqual(a.difficulty_level, int(row['arithmetic_{}'.format(session)]))
                self.assertEqual(a.operand_digits, a.difficulty_level)
                self.assertEqual(a.column, 'arithmetic_{}'.format(session))
        dialogs = Dialogs([[' afx ', '2']])
        psychopy = types.ModuleType('psychopy')
        psychopy.gui = types.SimpleNamespace(Dlg=dialogs.dialog)
        with patch.dict(sys.modules, {'psychopy': psychopy}):
            a = prompt_arithmetic_assignment(csv_path)
        self.assertEqual(a.difficulty_level, 2)
        self.assertTrue(any('3ブロック' in text for text in dialogs.shown[0].texts))
        self.assertEqual(dialogs.shown[0].fields[1][1]['choices'], ['1', '2', '3'])

    def test_cancel_invalid_assignment_and_csv_do_not_create_data(self):
        with tempfile.TemporaryDirectory() as root:
            rig = ArithmeticRig()
            task = rig.load_arithmetic(root)
            task.OUTPUT_DIR = Path(root) / 'output'
            task.prompt_arithmetic_assignment = lambda path, blocks: None
            task.main()
            for args in [('ZZZ', 1), ('AFX', 4), ('AFX', 0)]:
                with self.subTest(args=args), self.assertRaises(ValueError):
                    task.main(*args)
            task.COUNTERBALANCE_CSV = Path(root) / 'bad.csv'
            task.COUNTERBALANCE_CSV.write_text('participant_id,arithmetic_1,arithmetic_2,arithmetic_3\nAFX,1,1,3\n')
            with self.assertRaises(AssignmentTableError):
                task.main('AFX', 1)
            self.assertFalse(task.OUTPUT_DIR.exists())
            self.assertIsNone(rig.recorder)
            self.assertEqual(rig.windows, [])

    def test_setup_and_cleanup_errors_save_data_and_close_windows(self):
        for options in ({'fail_window': 1}, {'unsubscribe_error': True}):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as root:
                rig = ArithmeticRig(**options)
                task = rig.load_arithmetic(root)
                with self.assertRaises(RuntimeError), redirect_stdout(io.StringIO()):
                    task.main('AFX', 1)
                metadata, trials, events, inputs, tlx = self.read(root)
                self.assertEqual(metadata['status'], 'failed')
                self.assertEqual(metadata['tlx_status'], 'not_started')
                self.assertEqual(rig.recorder.close_calls, 1)
                self.assertTrue(all(window.closed for window in rig.windows))


if __name__ == '__main__':
    unittest.main()
