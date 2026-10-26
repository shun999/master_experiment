from contextlib import redirect_stdout
from datetime import datetime
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from src.experiment_paths import create_output_prefix
from tests.test_arithmetic_experiment import ArithmeticRig
from tests.test_nback_dual_screen import Rig
from tools import tobii_nback_1200hz_features as features


ROOT = Path(__file__).resolve().parents[1]


class OutputPathTests(unittest.TestCase):
    def test_task_participant_and_session_are_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'data'
            cases = [('nback', ' afx ', 1, 3, 2), ('nback', 'AFX', 2, 1, 0),
                     ('nback', 'DNV', 1, 1, 0), ('arithmetic', 'AFX', 1, 1, None)]
            for task, subject, session, level, n_back in cases:
                base = create_output_prefix(root, task, subject, session, level, n_back)
                run_dir = base.parent
                self.assertEqual(run_dir.parent, root / task / subject.strip().upper() / 'session_{:02d}'.format(session))
                self.assertTrue(run_dir.is_dir())
                self.assertTrue(base.name.endswith(run_dir.name))
                self.assertTrue(base.name.startswith('{}_{}_s{:02d}_d{:02d}'.format(
                    subject.strip().upper(), task, session, level)))

    def test_repeated_timestamp_preserves_existing_run_and_uses_one_timestamp(self):
        with tempfile.TemporaryDirectory() as directory, patch('src.experiment_paths.datetime') as clock:
            clock.now.return_value = datetime(2026, 10, 26, 15, 30, 0, 123456)
            first = create_output_prefix(directory, 'nback', 'AFX', 1, 3, 2)
            saved = Path(str(first) + '_trials.csv')
            saved.write_text('original', encoding='utf-8')
            second = create_output_prefix(directory, 'nback', 'AFX', 1, 3, 2)
            third = create_output_prefix(directory, 'nback', 'AFX', 1, 3, 2)
            self.assertEqual(first.parent.name, '20261026_153000_123456')
            self.assertEqual(second.parent.name, first.parent.name + '_02')
            self.assertEqual(third.parent.name, first.parent.name + '_03')
            self.assertTrue(second.name.endswith(second.parent.name))
            self.assertEqual(saved.read_text(encoding='utf-8'), 'original')
            self.assertEqual(clock.now.call_count, 3)

    def test_invalid_identifiers_do_not_create_directories(self):
        invalid = [('other', 'AFX', 1, 1, None), ('nback', '../AFX', 1, 1, 0),
                   ('nback', 'AFX', 5, 1, 0), ('arithmetic', 'AFX', 1, 4, None)]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'results'
            for args in invalid:
                with self.assertRaises(ValueError):
                    create_output_prefix(root, *args)
                self.assertFalse(root.exists())

    def test_both_tasks_group_all_outputs_and_repeat_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prefixes = []
            for task_name in ('nback', 'arithmetic'):
                for attempt in range(2):
                    rig = Rig() if task_name == 'nback' else ArithmeticRig()
                    task = rig.load(root) if task_name == 'nback' else rig.load_arithmetic(root)
                    if task_name == 'arithmetic':
                        task.N_BLOCKS = 1
                    with redirect_stdout(io.StringIO()):
                        task.main('afx', 1)
                    base = rig.recorder.base
                    prefixes.append(base)
                    self.assertEqual(base.parent.parent, root / task_name / 'AFX' / 'session_01')
                    files = list(base.parent.iterdir())
                    self.assertTrue(all(path.name.startswith(base.name + '_') for path in files))
                    suffixes = {path.name[len(base.name):] for path in files}
                    self.assertTrue({'_trials.csv', '_events.csv', '_tlx.csv', '_metadata.json', '_progress.log'} <= suffixes)
                    if task_name == 'arithmetic':
                        self.assertIn('_input.csv', suffixes)
                    metadata = json.loads(Path(str(base) + '_metadata.json').read_text(encoding='utf-8'))
                    self.assertEqual(Path(metadata['output_directory']), base.parent)
                    self.assertEqual(Path(metadata['output_prefix']), base)
            self.assertEqual(len(set(prefixes)), 4)
            self.assertEqual(len(list(root.rglob('*_metadata.json'))), 4)

    def test_entry_imports_from_another_cwd_without_project_on_sys_path(self):
        # Exercise the entry scripts' path bootstrap in an isolated interpreter.
        code = '''
import importlib.util
import sys
sys.path[:] = [p for p in sys.path if p and p != sys.argv[1]]
spec = importlib.util.spec_from_file_location('rig_fixture', sys.argv[2])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
rig = module.Rig()
task = rig.load(sys.argv[3])
assert task.COUNTERBALANCE_CSV == module.TASK_PATH.parent / 'counterbalance.csv'
from tests.test_arithmetic_experiment import ArithmeticRig
other = ArithmeticRig().load_arithmetic(sys.argv[3])
assert other.COUNTERBALANCE_CSV == task.COUNTERBALANCE_CSV
'''
        with tempfile.TemporaryDirectory() as directory:
            env = os.environ.copy()
            env.pop('PYTHONPATH', None)
            result = subprocess.run([sys.executable, '-B', '-c', code, str(ROOT),
                                     str(ROOT / 'tests' / 'test_nback_dual_screen.py'), directory],
                                    cwd=directory, env=env, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, universal_newlines=True)
            self.assertEqual(result.returncode, 0, result.stderr)


class AnalysisInputPathTests(unittest.TestCase):
    def test_recursive_new_results_take_priority_and_legacy_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            new = root / 'data' / 'nback'
            legacy = root / 'nback_data'
            legacy.mkdir()
            old_file = legacy / 'old_gaze.csv'
            old_file.touch()
            with patch.object(features, 'DATA_DIR', new), patch.object(features, 'LEGACY_DATA_DIR', legacy), patch.object(features, 'RUN_PREFIX', ''):
                self.assertEqual(features.find_input(), old_file)
                run_dir = new / 'AFX' / 'session_01' / 'run'
                run_dir.mkdir(parents=True)
                first = run_dir / 'first_gaze.csv'
                first.touch()
                latest = run_dir / 'latest_gaze.csv'
                latest.touch()
                os.utime(first, (10, 10))
                os.utime(latest, (20, 20))
                os.utime(old_file, (30, 30))
                self.assertEqual(features.find_input(), latest)
                self.assertEqual(features.find_input(str(old_file)), old_file)
                with patch.object(features, 'RUN_PREFIX', 'first'):
                    self.assertEqual(features.find_input(), first)
                with patch.object(features, 'RUN_PREFIX', 'old'):
                    self.assertEqual(features.find_input(), old_file)

    def test_missing_and_ambiguous_recordings_require_explicit_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(features, 'DATA_DIR', root / 'new'), patch.object(features, 'LEGACY_DATA_DIR', root / 'old'), patch.object(features, 'RUN_PREFIX', ''):
                with self.assertRaises(FileNotFoundError):
                    features.find_input()
                for child in ('one', 'two'):
                    run_dir = root / 'new' / child
                    run_dir.mkdir(parents=True)
                    (run_dir / 'sample_gaze.csv').touch()
                with patch.object(features, 'RUN_PREFIX', 'sample'):
                    with self.assertRaises(ValueError):
                        features.find_input()
                with patch.object(features, 'RUN_PREFIX', 'one/sample'):
                    self.assertEqual(features.find_input(), root / 'new' / 'one' / 'sample_gaze.csv')


if __name__ == '__main__':
    unittest.main()
