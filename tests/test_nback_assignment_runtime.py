# -*- coding: utf-8 -*-
import csv
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import sys
import types
import unittest
from unittest.mock import patch

from tests.test_nback_dual_screen import Rig
from tests.test_experiment_assignment import Dialogs


class AssignmentRuntimeTests(unittest.TestCase):
    def test_default_main_uses_the_input_dialog(self):
        with tempfile.TemporaryDirectory() as root:
            rig = Rig()
            task = rig.load(root)
            dialogs = Dialogs([[' dnv ', '2']])
            psychopy = types.ModuleType('psychopy')
            psychopy.gui = types.SimpleNamespace(Dlg=dialogs.dialog)
            with patch.dict(sys.modules, {'psychopy': psychopy}), redirect_stdout(io.StringIO()):
                task.main()
            metadata = json.loads(next(Path(root).rglob('*_metadata.json')).read_text(encoding='utf-8'))
            self.assertEqual(metadata['participant'], 'DNV')
            self.assertEqual(metadata['session_number'], 2)
            self.assertEqual(metadata['difficulty_level'], 2)
            self.assertEqual(metadata['n_back'], 1)
            self.assertEqual(len(dialogs.shown), 1)

    def test_cancel_and_invalid_inputs_do_not_start_devices_or_create_files(self):
        with tempfile.TemporaryDirectory() as root:
            rig = Rig()
            task = rig.load(root)
            task.OUTPUT_DIR = Path(root) / 'outputs'
            task.prompt_nback_assignment = lambda path: None
            task.main()
            for args in [('ZZZ', 1), ('AFX', 0), ('AFX', 5), ('AFX', None), (None, 1)]:
                with self.subTest(args=args), self.assertRaises(ValueError):
                    task.main(*args)
            task.COUNTERBALANCE_CSV = Path(root) / 'missing.csv'
            with self.assertRaises(ValueError):
                task.main('AFX', 1)
            self.assertFalse(task.OUTPUT_DIR.exists())
            self.assertEqual(rig.windows, [])
            self.assertIsNone(rig.recorder)

    def test_zero_back_generation_and_reproducibility(self):
        with tempfile.TemporaryDirectory() as root:
            task = Rig().load(root, full=True)
            for probability in (0, .3, 1):
                task.TARGET_PROBABILITY = probability
                rows = task.make_design(0, 1000, 123)
                self.assertEqual(rows, task.make_design(0, 1000, 123))
                for row in rows:
                    self.assertIn(row['stimulus'], '0123456789X')
                    self.assertEqual(row['is_target'], int(row['stimulus'] == 'X'))
                    if probability in (0, 1):
                        self.assertEqual(row['is_target'], probability)
                if probability == .3:
                    self.assertGreater(sum(row['is_target'] for row in rows), 250)
                    self.assertLess(sum(row['is_target'] for row in rows), 350)

    def test_full_zero_back_assignment_is_used_in_stimuli_screens_and_saved_data(self):
        with tempfile.TemporaryDirectory() as root:
            rig = Rig()
            task = rig.load(root, full=True)
            task.SEED = 20261026
            with redirect_stdout(io.StringIO()):
                task.main(' dnv ', 1)
            prefix = 'DNV_nback_s01_d01_n00_'
            files = [path for path in Path(root).rglob('*') if path.is_file()]
            self.assertTrue(files)
            self.assertTrue(all(path.name.startswith(prefix) for path in files))
            metadata = json.loads(next(Path(root).rglob('*_metadata.json')).read_text(encoding='utf-8'))
            self.assertEqual(metadata['participant'], 'DNV')
            self.assertEqual(metadata['session_number'], 1)
            self.assertEqual(metadata['difficulty_level'], 1)
            self.assertEqual(metadata['n_back'], 0)
            self.assertEqual(metadata['counterbalance_column'], 'nback_1')
            self.assertEqual(metadata['difficulty_to_nback'], {'1': 0, '2': 1, '3': 2, '4': 3})
            self.assertEqual(metadata['zero_back_target'], 'X')
            self.assertEqual(metadata['stimulus_symbols'], '0123456789X')
            self.assertEqual(metadata['completed_trials'], 180)
            self.assertAlmostEqual(metadata['actual_set_seconds'], 540, delta=.04)
            with next(Path(root).rglob('*_trials.csv')).open(encoding='utf-8') as stream:
                rows = list(csv.DictReader(stream))
            self.assertTrue(any(row['stimulus'] == 'X' for row in rows))
            self.assertTrue(all(int(row['is_target']) == int(row['stimulus'] == 'X') for row in rows))
            with next(Path(root).rglob('*_tlx.csv')).open(encoding='utf-8-sig') as stream:
                tlx = next(csv.DictReader(stream))
            self.assertEqual((tlx['participant_id'], tlx['session_number'], tlx['difficulty_level'], tlx['n_back']),
                             ('DNV', '1', '1', '0'))
            self.assertTrue(any('実験回数（セット）: 1/4' in str(drawn) and '課題: 0-back' in str(drawn)
                                and 'Xは右矢印' in str(drawn) for _, drawn, _ in rig.windows[0].history))
            text = task.participant_instructions(task.load_nback_assignment(task.COUNTERBALANCE_CSV, 'DNV', 1))
            self.assertIn('RIGHT arrow: X.', text)
            self.assertNotIn('first 0', text)

    def test_positive_conditions_reach_generator_and_instructions(self):
        for session, expected_n in [(2, 1), (4, 2), (3, 3)]:
            with self.subTest(session=session), tempfile.TemporaryDirectory() as root:
                rig = Rig()
                task = rig.load(root, full=True)
                task.N_TRIALS, task.N_BLOCKS, task.REST_SECONDS, task.SYNC_COUNTDOWN_SECONDS = 6, 2, 3, 2
                with redirect_stdout(io.StringIO()):
                    task.main('DNV', session)
                metadata = json.loads(next(Path(root).rglob('*_metadata.json')).read_text(encoding='utf-8'))
                self.assertEqual(metadata['n_back'], expected_n)
                self.assertEqual(metadata['difficulty_level'], expected_n + 1)
                self.assertEqual(metadata['session_number'], session)
                with next(Path(root).rglob('*_trials.csv')).open(encoding='utf-8') as stream:
                    rows = list(csv.DictReader(stream))
                self.assertTrue(all(row['stimulus'] in '0123456789' for row in rows))
                for block in (1, 2):
                    block_rows = [row for row in rows if int(row['block']) == block]
                    self.assertTrue(all(row['is_target'] == '0' for row in block_rows[:expected_n]))
                    for i, row in enumerate(block_rows):
                        self.assertEqual(int(row['is_target']), int(i >= expected_n and row['stimulus'] == block_rows[i-expected_n]['stimulus']))
                instructions = task.participant_instructions(task.load_nback_assignment(task.COUNTERBALANCE_CSV, 'DNV', session))
                self.assertIn('same digit as {} trials ago'.format(expected_n), instructions)

    def test_zero_back_scoring_uses_x_as_target(self):
        with tempfile.TemporaryDirectory() as root:
            rig = Rig()
            task = rig.load(root)
            task.make_design = lambda n, count, seed: [
                dict(trial=1, stimulus='7', is_target=0),
                dict(trial=2, stimulus='X', is_target=1),
                dict(trial=3, stimulus='0', is_target=0),
            ]
            with redirect_stdout(io.StringIO()):
                task.main('DNV', 1)
            with next(Path(root).rglob('*_trials.csv')).open(encoding='utf-8') as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual([row['response_key'] for row in rows[:3]], ['left', 'right', ''])
            self.assertEqual([row['correct'] for row in rows[:3]], ['1', '1', '0'])


if __name__ == '__main__':
    unittest.main()
