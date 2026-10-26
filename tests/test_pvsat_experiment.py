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

from src.experiment_assignment import (AssignmentTableError, load_pvsat_assignment,
    prompt_pvsat_assignment, read_pvsat_table)
from src.nasa_tlx import SCALE_KEYS
from tests.test_experiment_assignment import Dialogs
from tests.test_nback_dual_screen import Rig, Clock
from tools.tobii_nback_1200hz_features import TrialLabels


TASK_PATH = Path(__file__).resolve().parents[1] / 'psychopy2022_spectrum1200_pvsat_lightweight.py'


class InputClock(Clock):
    def reset(self):
        super().reset()
        self.rig.trial += 1
        if self.rig.trial == 1:
            self.rig.set_anchor = self.rig.now


class PvsatKeyboard:
    def __init__(self, rig):
        self.rig = rig
        rig.pvsat_keyboard = self
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
        if not self.rig.trial or self.rig.trial > len(self.rig.plans):
            return []
        row = self.rig.plans[self.rig.trial - 1]
        count, task = self.rig.trial_count, self.rig.task
        block = (self.rig.trial - 1) // count
        local = (self.rig.trial - 1) % count + 1
        interval = task.INTERVAL_BY_DIFFICULTY[self.rig.level]
        deadline = (self.rig.set_anchor + block * (task.TASK_SECONDS + task.REST_SECONDS)
                    + local * interval - self.clock.origin)
        expected = row['expected_key']
        opposite = 'left' if expected == 'right' else 'right'
        if local == 1:
            script = [('right', .2)]  # Ignore the memory-only key press.
        elif local == 2:
            script = [(expected, .2), (opposite, .4)]
        elif local == 3:
            script = [(opposite, .6)]
        elif local == 4:
            script = []
        elif local == 5:
            script = [(expected, deadline - .00001), (opposite, deadline + .00001)]
        elif local == 6:
            script = [(expected, deadline + .00001)]  # Too late; don't spill.
        elif local == 7:
            script = [(opposite, .200002), (expected, .200001)]  # Same poll, out of order.
        else:
            script = [(expected, .6)]
        keys = []
        for index, (name, rt) in enumerate(script):
            if index not in self.emitted and name in keyList and rt <= self.clock.getTime():
                self.emitted.add(index)
                keys.append(types.SimpleNamespace(name=name, rt=rt))
        return keys


class PvsatRig(Rig):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.plans, self.set_anchor = [], None

    def should_abort(self):
        if self.abort == 'answered':
            if self.trial == 2 and self.pvsat_keyboard.clock.getTime() > .3:
                self.abort_sent = True
                return True
            return False
        return super().should_abort()

    def load_pvsat(self, root):
        original = super().load(root, full=True)
        psychopy = types.ModuleType('psychopy')
        psychopy.__version__ = 'mock'
        psychopy.core, psychopy.visual = original.core, original.visual
        hardware = types.ModuleType('psychopy.hardware')
        hardware.keyboard = types.SimpleNamespace(Keyboard=lambda: PvsatKeyboard(self))
        with patch.dict(sys.modules, {'psychopy': psychopy, 'psychopy.hardware': hardware,
                                      'tobii_research': original.tr}):
            spec = importlib.util.spec_from_file_location('pvsat_under_test', str(TASK_PATH))
            task = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(task)
        self.task = task
        task.OUTPUT_DIR = Path(root)
        task.SEED = 20261026
        task.SKIP_CALIBRATION, task.SAVE_CALIBRATION_DATA = True, False
        task.validate_screen_configuration = lambda: None
        task.StreamRecorder, task.select_tracker = original.StreamRecorder, original.select_tracker
        task.tracker_info = original.tracker_info
        generate = task.make_pvsat_design
        def design(count, seed, target_sum, target_proportion):
            self.trial_count = count
            self.level = {24: 1, 30: 2, 40: 3}[count]
            rows = generate(count, seed, target_sum, target_proportion)
            self.plans.extend(rows)
            return rows
        task.make_pvsat_design = design
        def questionnaire(win, operator, session, stamp, check_abort):
            assert self.recorder.closed and self.recorder.close_calls == 1
            assert session.blocks == task.N_BLOCKS and session.task == 'pvsat'
            assert session.n_back is None
            self.operator = operator
            with Path(str(self.recorder.base) + '_trials.csv').open(encoding='utf-8') as stream:
                assert len(list(csv.DictReader(stream))) == len(self.plans)
            self.tlx_active = True
            session.start(stamp('tlx_start', ''))
            for key in SCALE_KEYS:
                check_abort()
                session.set_rating(key, 50)
                self.tlx_answers += 1
                stamp('tlx_response', key)
            session.finish(stamp('tlx_end', ''))
        task.run_raw_tlx = questionnaire
        return task


class PvsatExperimentTests(unittest.TestCase):
    def read(self, base):
        metadata = json.loads(Path(str(base) + '_metadata.json').read_text(encoding='utf-8'))
        tables = []
        for suffix in ('trials', 'events', 'tlx'):
            path = Path(str(base) + '_' + suffix + '.csv')
            with path.open(encoding='utf-8-sig', newline='') if path.exists() else io.StringIO('') as stream:
                tables.append(list(csv.DictReader(stream)))
        return (metadata,) + tuple(tables)

    def run_task(self, root, subject='AFX', session=1, **kwargs):
        rig = PvsatRig(**kwargs)
        task = rig.load_pvsat(root)
        with redirect_stdout(io.StringIO()):
            task.main(subject, session)
        return rig, task, self.read(rig.recorder.base)

    def test_all_three_rates_90_second_blocks_270_second_set_and_tlx(self):
        for subject, session, level, count, target_count in (
                ('DNV', 1, 1, 24, 7), ('JXZ', 2, 2, 30, 9), ('AFX', 1, 3, 40, 12)):
            with self.subTest(level=level), tempfile.TemporaryDirectory() as directory:
                rig, task, (meta, trials, events, tlx) = self.run_task(directory, subject, session)
                labels = TrialLabels(rig.recorder.base, meta)
                interval = {1: 2.5, 2: 2, 3: 1.5}[level]
                self.assertEqual(meta['task'], 'pvsat')
                self.assertEqual(meta['difficulty_level'], level)
                self.assertEqual(meta['trials_per_block'], count)
                self.assertEqual(meta['requested_trials'], 3 * count)
                self.assertEqual(meta['completed_trials'], 3 * count)
                self.assertEqual(meta['completed_response_trials'], 3 * (count - 1))
                self.assertEqual(meta['correct_responses'], 3 * (count - 4))
                self.assertEqual(meta['target_counts_per_block'], [target_count] * 3)
                self.assertEqual(meta['completed_blocks'], 3)
                self.assertEqual(meta['planned_set_seconds'], 270)
                self.assertAlmostEqual(meta['actual_set_seconds'], 270, delta=.04)
                self.assertEqual(meta['presentation_interval_seconds'], interval)
                self.assertEqual(meta['isi_seconds'], interval - .5)
                def time(name, block=0):
                    return next(int(event['system_time_stamp_us']) / 1e6 for event in events
                                if event['event'] == name and int(event['block']) == block)
                start = time('set_start')
                self.assertAlmostEqual(start - time('sync_start_countdown', 1), 10, delta=.04)
                for block in range(1, 4):
                    begin, end = time('task_start', block), time('task_end', block)
                    self.assertAlmostEqual(begin - start, (block - 1) * 90, delta=.04)
                    self.assertAlmostEqual(end - begin, 60, delta=.04)
                    self.assertAlmostEqual(time('rest_start', block), end, delta=.04)
                    self.assertAlmostEqual(time('rest_end', block) - end, 30, delta=.04)
                    self.assertAlmostEqual(time('sync_start_cue', block), begin, delta=.04)
                    self.assertAlmostEqual(time('sync_end_countdown', block) - begin, 50, delta=.04)
                    self.assertAlmostEqual(time('sync_end_cue', block), end, delta=.04)
                    self.assertAlmostEqual(time('sync_end_cue_end', block) - end, 1, delta=.04)
                    if block > 1:
                        self.assertAlmostEqual(time('sync_start_countdown', block) - begin, -10, delta=.04)
                    rows = [row for row in trials if int(row['block']) == block]
                    self.assertEqual(len(rows), count)
                    self.assertEqual(sum(row['is_target'] == '1' for row in rows), target_count)
                    for local, row in enumerate(rows, 1):
                        onset = int(row['stim_on_us']) / 1e6
                        self.assertAlmostEqual(onset - start, (block - 1) * 90 + (local - 1) * interval, delta=.04)
                        self.assertAlmostEqual((int(row['stim_off_us']) - int(row['stim_on_us'])) / 1e6, .5, delta=.04)
                        self.assertAlmostEqual((int(row['response_window_end_us']) - int(row['stim_on_us'])) / 1e6, interval, delta=.04)
                        if local == 1:
                            for field in ('previous_digit', 'pair_sum', 'is_target', 'expected_key', 'response_key', 'correct', 'rt_seconds'):
                                self.assertEqual(row[field], '')
                        elif local in (3, 4, 6):
                            self.assertEqual(row['correct'], '0')
                            if local != 3:
                                self.assertEqual(row['response_key'], '')
                        else:
                            self.assertEqual(row['response_key'], row['expected_key'])
                            self.assertEqual(row['correct'], '1')
                            if local == 5:
                                self.assertAlmostEqual(float(row['rt_seconds']), float(row['input_deadline_rt_seconds']) - .00001, delta=.000002)
                            elif local == 2:
                                self.assertEqual(float(row['rt_seconds']), .2)
                            elif local == 7:
                                self.assertEqual(float(row['rt_seconds']), .200001)
                    self.assertEqual(labels.for_timestamp(int((end + 5) * 1e6)), ('', '', ''))
                self.assertEqual(tlx[0]['task'], 'pvsat')
                self.assertEqual(tlx[0]['blocks'], '3')
                self.assertEqual(tlx[0]['n_back'], '')
                self.assertEqual(tlx[0]['raw_tlx'], '50.0')
                self.assertEqual(rig.operator.completed, 3 * (count - 1))
                self.assertEqual(rig.operator.correct, 3 * (count - 4))
                self.assertEqual(rig.recorder.close_calls, 1)
                self.assertEqual(rig.recorder.unsubscribe_calls, 1)
                self.assertLessEqual(time('recording_stop'), time('tlx_start'))
                self.assertTrue(all(window.closed for window in rig.windows))
                self.assertFalse(rig.windows[0].settings['fullscr'])
                self.assertFalse(rig.windows[0].settings['waitBlanking'])
                self.assertTrue(rig.windows[1].settings['waitBlanking'])
                self.assertEqual(meta['output_directory'], str(rig.recorder.base.parent))
                self.assertEqual(rig.recorder.base.parent.parent, Path(directory) / 'pvsat' / subject / 'session_{:02d}'.format(session))
                self.assertLess(meta['participant_frame_timing']['max_interval_seconds'], .04)

    def test_all_30_assignments_and_dialog(self):
        path = TASK_PATH.with_name('counterbalance.csv')
        before = path.read_bytes()
        with path.open(encoding='utf-8-sig') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 10)
        for row in rows:
            for session in range(1, 4):
                assignment = load_pvsat_assignment(path, row['participant_id'].lower(), str(session))
                self.assertEqual(assignment.difficulty_level, int(row['pvsat_{}'.format(session)]))
                self.assertEqual(assignment.column, 'pvsat_{}'.format(session))
        dialogs = Dialogs([['unknown', '1'], [' afx ', '2']])
        psychopy = types.ModuleType('psychopy')
        psychopy.gui = types.SimpleNamespace(Dlg=dialogs.dialog)
        with patch.dict(sys.modules, {'psychopy': psychopy}):
            assignment = prompt_pvsat_assignment(path)
        self.assertEqual(assignment.difficulty_level, 1)
        self.assertEqual(dialogs.shown[-1].fields[1][1]['choices'], ['1', '2', '3'])
        self.assertTrue(any('3ブロック' in text for text in dialogs.shown[-1].texts))
        self.assertEqual(path.read_bytes(), before)

    def test_normal_operator_refresh_limit_and_memory_exclusion(self):
        with tempfile.TemporaryDirectory() as root:
            rig = PvsatRig()
            task = rig.load_pvsat(root)
            window = rig.window(screen=0, waitBlanking=False)
            assignment = load_pvsat_assignment(task.COUNTERBALANCE_CSV, 'AFX', 1)
            operator = task.OperatorDisplay(window, assignment)
            operator.complete_trial({'completed': 1, 'response_required': 0, 'correct': ''})
            self.assertEqual((operator.presented, operator.completed, operator.correct), (1, 0, 0))
            for step in range(100):
                operator.detail = str(step)
                operator.render()
                rig.wait(.02)
        times = [moment for moment, _, _ in window.history]
        self.assertGreater(len(times), 5)
        self.assertTrue(all(b - a >= .2 - 1e-8 for a, b in zip(times, times[1:])))

    def test_default_dialog_executes_selected_rate_and_repeat_is_separate(self):
        with tempfile.TemporaryDirectory() as root:
            prefixes = []
            for _ in range(2):
                rig = PvsatRig()
                task = rig.load_pvsat(root)
                dialogs = Dialogs([[' afx ', '2']])
                psychopy = types.ModuleType('psychopy')
                psychopy.gui = types.SimpleNamespace(Dlg=dialogs.dialog)
                with patch.dict(sys.modules, {'psychopy': psychopy}), redirect_stdout(io.StringIO()):
                    task.main()
                meta, trials, events, tlx = self.read(rig.recorder.base)
                prefixes.append(rig.recorder.base)
                self.assertEqual(meta['difficulty_level'], 1)
                self.assertEqual(meta['trials_per_block'], 24)
                self.assertEqual(tlx[0]['session_number'], '2')
            self.assertNotEqual(prefixes[0].parent, prefixes[1].parent)
            self.assertEqual(len(list(Path(root).rglob('*_metadata.json'))), 2)

    def test_abort_preserves_partial_trial_and_closes_devices(self):
        for phase in ('start_countdown', 'start_cue', 'task', 'answered', 'end_countdown', 'end_cue', 'rest', 'tlx'):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as root:
                rig, task, (meta, trials, events, tlx) = self.run_task(root, abort=phase)
                self.assertEqual(meta['status'], 'aborted')
                self.assertTrue(all(window.closed for window in rig.windows))
                self.assertEqual(rig.recorder.close_calls, 1)
                if phase == 'answered':
                    last = trials[-1]
                    self.assertEqual(last['completed'], '0')
                    self.assertEqual(last['correct'], '')
                    self.assertNotEqual(last['response_key'], '')
                    self.assertNotEqual(last['rt_seconds'], '')
                    self.assertNotEqual(last['response_window_end_us'], '')
                if phase == 'tlx':
                    self.assertEqual(meta['completed_blocks'], 3)
                    self.assertEqual(tlx[0]['status'], 'aborted')
                    self.assertEqual(tlx[0]['raw_tlx'], '')
                else:
                    self.assertEqual(meta['tlx_status'], 'not_started')

    def test_cancel_invalid_assignment_csv_and_design_create_no_outputs(self):
        with tempfile.TemporaryDirectory() as root:
            rig = PvsatRig()
            task = rig.load_pvsat(root)
            task.OUTPUT_DIR = Path(root) / 'outputs'
            task.prompt_pvsat_assignment = lambda path, blocks: None
            task.main()
            for args in (('ZZZ', 1), ('AFX', 0), ('AFX', 4), (None, 1)):
                with self.assertRaises(ValueError):
                    task.main(*args)
            task.TARGET_SUM = 19
            with self.assertRaises(ValueError):
                task.main('AFX', 1)
            task.TARGET_SUM = 11
            task.COUNTERBALANCE_CSV = Path(root) / 'bad.csv'
            task.COUNTERBALANCE_CSV.write_text('participant_id,pvsat_1,pvsat_2,pvsat_3\nAFX,1,1,3\n')
            with self.assertRaises(AssignmentTableError):
                task.main('AFX', 1)
            self.assertFalse(task.OUTPUT_DIR.exists())
            self.assertIsNone(rig.recorder)
            self.assertEqual(rig.windows, [])

    def test_setup_and_cleanup_failures_save_and_close(self):
        for options in ({'fail_window': 1}, {'unsubscribe_error': True}):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as root:
                rig = PvsatRig(**options)
                task = rig.load_pvsat(root)
                with self.assertRaises(RuntimeError), redirect_stdout(io.StringIO()):
                    task.main('AFX', 1)
                meta, trials, events, tlx = self.read(rig.recorder.base)
                self.assertEqual(meta['status'], 'failed')
                self.assertEqual(meta['tlx_status'], 'not_started')
                self.assertTrue(all(window.closed for window in rig.windows))
                self.assertEqual(rig.recorder.close_calls, 1)


if __name__ == '__main__':
    unittest.main()
