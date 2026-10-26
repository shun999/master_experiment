# -*- coding: utf-8 -*-
import csv
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from experiment_assignment import (AssignmentTableError, load_nback_assignment,
    prompt_nback_assignment, read_nback_table, select_nback_assignment)


CSV_PATH = Path(__file__).with_name('counterbalance.csv')
HEADER = 'participant_id,nback_1,nback_2,nback_3,nback_4\n'


class Dialogs:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.shown = []

    def dialog(self, title):
        reply = next(self.replies)
        dialog = types.SimpleNamespace(title=title, texts=[], fields=[], OK=reply is not None)
        dialog.addText = dialog.texts.append
        dialog.addField = lambda label, **kwargs: dialog.fields.append((label, kwargs))
        dialog.show = lambda: reply
        self.shown.append(dialog)
        return dialog

    def prompt(self, path):
        psychopy = types.ModuleType('psychopy')
        psychopy.gui = types.SimpleNamespace(Dlg=self.dialog)
        with patch.dict(sys.modules, {'psychopy': psychopy}):
            return prompt_nback_assignment(path)


class AssignmentTests(unittest.TestCase):
    def test_all_40_assignments_and_examples(self):
        before = CSV_PATH.read_bytes()
        with CSV_PATH.open(encoding='utf-8-sig') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 10)
        for row in rows:
            for session in range(1, 5):
                with self.subTest(id=row['participant_id'], session=session):
                    assignment = load_nback_assignment(CSV_PATH, row['participant_id'], session)
                    self.assertEqual(assignment.difficulty_level, int(row['nback_{}'.format(session)]))
                    self.assertEqual(assignment.n_back, {1: 0, 2: 1, 3: 2, 4: 3}[assignment.difficulty_level])
                    self.assertEqual(assignment.column, 'nback_{}'.format(session))
        self.assertEqual(load_nback_assignment(CSV_PATH, 'AFX', 1).n_back, 2)
        self.assertEqual(load_nback_assignment(CSV_PATH, 'BKR', 1).n_back, 3)
        self.assertEqual(load_nback_assignment(CSV_PATH, 'DNV', 1).n_back, 0)
        self.assertEqual(CSV_PATH.read_bytes(), before)

    def test_id_normalization_and_input_errors(self):
        table = read_nback_table(CSV_PATH)
        assignment = select_nback_assignment(table, '  afx ', ' 2 ')
        self.assertEqual(assignment.participant_id, 'AFX')
        self.assertEqual(assignment.session_number, 2)
        for participant_id in ('', 'ZZZ', 'AF', 'AFX1', 'ＡＦＸ', None):
            with self.subTest(id=participant_id), self.assertRaises(ValueError):
                select_nback_assignment(table, participant_id, 1)
        for session in (0, 5, True, 1.0, '1.5', '', None, '5'):
            with self.subTest(session=session), self.assertRaises(ValueError):
                select_nback_assignment(table, 'AFX', session)

    def test_invalid_csv_is_fatal(self):
        bad_tables = [
            '', HEADER,
            'participant_id,nback_1\nAFX,1\n',
            'participant_id,nback_1,nback_1,nback_3,nback_4\nAFX,1,2,3,4\n',
            HEADER + 'AFX,1,2,3,4\n afx ,4,3,2,1\n',
            HEADER + 'AFX,1,1,3,4\n', HEADER + 'AFX,0,2,3,4\n',
            HEADER + 'AFX,1,2,3,5\n', HEADER + 'AFX,1,2,no,4\n',
            HEADER + 'AFX,1,2\n', HEADER + 'AFX,1,2,3,4,5\n',
            HEADER + 'P001,1,2,3,4\n',
        ]
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'assignments.csv'
            for value in bad_tables:
                path.write_text(value, encoding='utf-8-sig')
                with self.subTest(csv=value), self.assertRaises(AssignmentTableError):
                    read_nback_table(path)
            with self.assertRaises(AssignmentTableError):
                read_nback_table(Path(root) / 'missing.csv')

    def test_minimal_nback_table_without_other_tasks_is_supported(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'assignments.csv'
            path.write_text(HEADER + 'AFX,1,2,3,4\n', encoding='utf-8')
            self.assertEqual(load_nback_assignment(path, 'AFX', 4).n_back, 3)

    def test_dialog_success_retry_and_cancel(self):
        dialogs = Dialogs([[' afx ', '1']])
        assignment = dialogs.prompt(CSV_PATH)
        self.assertEqual((assignment.participant_id, assignment.n_back), ('AFX', 2))
        self.assertEqual(dialogs.shown[0].fields[0][1]['initial'], '')
        self.assertEqual(dialogs.shown[0].fields[1][1]['choices'], ['1', '2', '3', '4'])
        dialogs = Dialogs([['ZZZ', '1'], ['AFX', '4']])
        assignment = dialogs.prompt(CSV_PATH)
        self.assertEqual(assignment.n_back, 0)
        self.assertTrue(any('登録されていない' in text for text in dialogs.shown[1].texts))
        self.assertEqual(dialogs.shown[1].fields[0][1]['initial'], 'ZZZ')
        self.assertIsNone(Dialogs([None]).prompt(CSV_PATH))
        with tempfile.TemporaryDirectory() as root:
            dialogs = Dialogs([None])
            self.assertIsNone(dialogs.prompt(Path(root) / 'missing.csv'))
            self.assertEqual(dialogs.shown[0].fields, [])
            self.assertIn('エラー', dialogs.shown[0].title)


if __name__ == '__main__':
    unittest.main()
