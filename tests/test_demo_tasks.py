# -*- coding: utf-8 -*-
import unittest

from src.demo_tasks import DemoAnswer, demo_spec, expected_label, guide_rows, practice_rows


class DemoTaskTests(unittest.TestCase):
    def test_fixed_guides_and_first_digit_rules(self):
        nback = guide_rows('nback')
        self.assertEqual([row['stimulus'] for row in nback], list('388225'))
        self.assertEqual([row['expected'] for row in nback], ['left', 'left', 'right', 'left', 'right', 'left'])
        self.assertIsNone(nback[0]['previous'])
        self.assertTrue(nback[0]['response_required'])
        pvsat = guide_rows('pvsat')
        self.assertEqual([row['stimulus'] for row in pvsat], list('564729'))
        self.assertEqual([row['expected'] for row in pvsat], ['', 'right', 'left', 'right', 'left', 'right'])
        self.assertFalse(pvsat[0]['response_required'])
        self.assertIn('記憶のみ', expected_label(pvsat[0]))
        arithmetic = guide_rows('arithmetic')
        self.assertEqual([row['stimulus'] for row in arithmetic], ['3 + 4 = ?', '8 + 5 = ?', '2 + 6 = ?', '7 + 9 = ?'])
        self.assertEqual([row['expected'] for row in arithmetic], [7, 13, 8, 16])

    def test_short_practice_counts_targets_and_reproducibility(self):
        for task, count, required, targets in (('nback', 10, 10, 3), ('pvsat', 8, 7, 2), ('arithmetic', 4, 4, None)):
            spec = demo_spec(task)
            self.assertEqual(spec.interval * spec.count, 20)
            for seed in range(20):
                rows = practice_rows(task, seed)
                self.assertEqual(rows, practice_rows(task, seed))
                self.assertEqual(len(rows), count)
                self.assertEqual(sum(row['response_required'] for row in rows), required)
                if targets is not None:
                    self.assertEqual(sum(row['expected'] == 'right' for row in rows), targets)
                    for index, row in enumerate(rows):
                        self.assertIn(int(row['stimulus']), range(10) if task == 'nback' else range(1, 10))
                        if index:
                            self.assertEqual(row['previous'], int(rows[index - 1]['stimulus']))
                            target = (row['previous'] == int(row['stimulus']) if task == 'nback'
                                      else row['previous'] + int(row['stimulus']) == 11)
                            self.assertEqual(row['expected'], 'right' if target else 'left')
                else:
                    self.assertEqual(sum(row['expected'] >= 10 for row in rows), 2)
                    self.assertEqual(len(set(row['stimulus'] for row in rows)), 4)
            self.assertNotEqual(practice_rows(task, 10), practice_rows(task, 11))
        with self.assertRaises(ValueError):
            demo_spec('other')

    def test_unlimited_guide_retries_and_correct_answer_lock(self):
        row = guide_rows('nback')[2]
        answer = DemoAnswer('nback', row)
        self.assertTrue(answer.press('left', 120))
        self.assertFalse(answer.correct)
        self.assertTrue(answer.press('right', 121))
        self.assertTrue(answer.correct)
        self.assertFalse(answer.press('left', 122))
        memory = DemoAnswer('pvsat', guide_rows('pvsat')[0])
        self.assertFalse(memory.press('right', 10))
        self.assertIsNone(memory.correct)

    def test_keypad_editing_matches_production_rules_without_guide_deadline(self):
        answer = DemoAnswer('arithmetic', guide_rows('arithmetic')[1])
        for name in ('num_9', 'backspace', '1', 'delete', 'num_0', 'num_0', 'num_1', 'num_3'):
            self.assertTrue(answer.press(name, 500))
        self.assertEqual(answer.text, '0013')
        self.assertTrue(answer.correct)
        self.assertFalse(answer.press('num_7', 501))
        self.assertFalse(answer.press('return', 501))
        self.assertFalse(answer.press('num_enter', 501))
        self.assertTrue(answer.press('delete', 501))
        self.assertFalse(answer.correct)

    def test_practice_uses_first_arrow_and_timestamp_deadline(self):
        row = guide_rows('nback')[2]
        answer = DemoAnswer('nback', row, deadline=2)
        for name, rt in (('right', -1), ('right', float('nan')), ('right', 2), ('right', 3)):
            self.assertFalse(answer.press(name, rt))
        self.assertTrue(answer.press('left', 1.99999))
        self.assertFalse(answer.press('right', 1.999995))
        self.assertFalse(answer.correct)
        arithmetic = DemoAnswer('arithmetic', guide_rows('arithmetic')[0], deadline=5)
        self.assertFalse(arithmetic.press('7', 5))
        self.assertFalse(arithmetic.correct)
        self.assertEqual(arithmetic.response_label, '未回答')


if __name__ == '__main__':
    unittest.main()
