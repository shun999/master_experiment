import unittest

from src.arithmetic_task import ArithmeticInput, OPERAND_RANGES, count_carries, make_arithmetic_design


class ArithmeticLogicTests(unittest.TestCase):
    def test_carry_counts(self):
        for left, right, expected in [(1, 2, 0), (5, 5, 1), (18, 27, 1), (59, 41, 2),
                                      (95, 17, 2), (123, 45, 0), (999, 99, 3), (0, 0, 0)]:
            with self.subTest(left=left, right=right):
                self.assertEqual(count_carries(left, right), expected)

    def test_balanced_ranges_unique_pairs_and_seeds(self):
        for level, ranges in ((1, ((1, 9), (1, 9))), (2, ((10, 99), (10, 99))),
                              (3, ((100, 999), (10, 99)))):
            self.assertEqual(OPERAND_RANGES[level], ranges)
            (first_low, first_high), (second_low, second_high) = ranges
            for seed in range(20):
                rows = make_arithmetic_design(level, 12, seed)
                self.assertEqual(rows, make_arithmetic_design(level, 12, seed))
                self.assertEqual(len(rows), 12)
                self.assertEqual(sum(row['has_carry'] for row in rows), 6)
                self.assertEqual(len({tuple(sorted((row['operand_a'], row['operand_b']))) for row in rows}), 12)
                if level == 3:
                    for left_is_three_digits in (False, True):
                        group = [row for row in rows if (row['operand_a'] >= 100) == left_is_three_digits]
                        self.assertEqual(len(group), 6)
                        self.assertEqual(sum(row['has_carry'] for row in group), 3)
                for index, row in enumerate(rows, 1):
                    self.assertEqual(row['trial'], index)
                    left, right = row['operand_a'], row['operand_b']
                    first, second = (right, left) if level == 3 and left < 100 else (left, right)
                    self.assertTrue(first_low <= first <= first_high and second_low <= second <= second_high)
                    self.assertEqual(row['stimulus'], '{} + {} = ?'.format(left, right))
                    self.assertEqual(row['correct_answer'], left + right)
                    self.assertEqual(row['carry_count'], count_carries(left, right))
                    self.assertEqual(bool(row['carry_count']), bool(row['has_carry']))
        self.assertNotEqual(make_arithmetic_design(1, 12, 1), make_arithmetic_design(1, 12, 2))
        self.assertNotEqual(make_arithmetic_design(3, 12, 1), make_arithmetic_design(3, 12, 2))

    def test_level3_balances_each_orientation_and_carry_group_at_other_counts(self):
        for count in (4, 8, 16):
            rows = make_arithmetic_design(3, count, 10)
            for left_is_three_digits in (False, True):
                for carry in (False, True):
                    group = [row for row in rows if (row['operand_a'] >= 100) == left_is_three_digits
                             and bool(row['has_carry']) == carry]
                    self.assertEqual(len(group), count // 4)

    def test_invalid_design_parameters(self):
        for level, count in [(0, 12), (4, 12), (1, 0), (1, 11), (1, 42), (3, 2), (3, 6), (3, 10)]:
            with self.subTest(level=level, count=count), self.assertRaises(ValueError):
                make_arithmetic_design(level, count, 0)

    def test_four_digit_input_for_maximum_level3_answer(self):
        state = ArithmeticInput()
        for index, digit in enumerate('1098'):
            self.assertEqual(state.edit('num_' + digit, .2 + index * .2), 'append')
        final = state.snapshot(999 + 99, completed=True)
        self.assertEqual(final['answer_value'], 1098)
        self.assertEqual(final['correct'], 1)

    def test_numeric_input_editing_and_leading_zeros(self):
        state = ArithmeticInput()
        for key, rt in [('num_0', .1), ('0', .2), ('num_1', .3), ('num_2', .6)]:
            self.assertIsNotNone(state.edit(key, rt))
        final = state.snapshot(12, completed=True)
        self.assertEqual(final['answer_text'], '0012')
        self.assertEqual(final['answer_value'], 12)
        self.assertEqual(final['correct'], 1)
        self.assertEqual(final['first_input_rt_seconds'], .1)
        self.assertEqual(final['last_edit_rt_seconds'], .6)
        self.assertIsNone(state.edit('num_3', .7))
        self.assertEqual(state.text, '0012')
        self.assertEqual(state.edit('return', .8), 'clear')
        omitted = state.snapshot(12, completed=True)
        self.assertEqual(omitted['unanswered'], 1)
        self.assertIsNone(omitted['answer_value'])
        self.assertEqual(omitted['correct'], 0)
        self.assertEqual(omitted['first_input_rt_seconds'], .1)
        self.assertEqual(omitted['last_edit_rt_seconds'], .8)

    def test_only_enter_clears_and_empty_enter_preserves_timestamps(self):
        for enter in ('return', 'num_enter'):
            with self.subTest(enter=enter):
                state = ArithmeticInput()
                initial = state.snapshot(2)
                self.assertIsNone(state.edit(enter, .1))
                self.assertEqual(state.snapshot(2), initial)
                state.edit('num_9', .2)
                state.edit('num_1', .4)
                before = state.snapshot(2)
                for key in ('backspace', 'delete'):
                    self.assertIsNone(state.edit(key, .6))
                    self.assertEqual(state.snapshot(2), before)
                self.assertEqual(state.edit(enter, .8), 'clear')
                cleared = state.snapshot(2, completed=True)
                self.assertEqual(cleared['answer_text'], '')
                self.assertEqual(cleared['unanswered'], 1)
                self.assertEqual(cleared['correct'], 0)
                self.assertEqual(cleared['first_input_rt_seconds'], .2)
                self.assertEqual(cleared['last_edit_rt_seconds'], .8)
                self.assertIsNone(state.edit(enter, .9))
                self.assertEqual(state.snapshot(2, completed=True), cleared)
                self.assertEqual(state.edit('num_2', 1), 'append')
                final = state.snapshot(2, completed=True)
                self.assertEqual(final['correct'], 1)
                self.assertEqual(final['first_input_rt_seconds'], .2)
                self.assertEqual(final['last_edit_rt_seconds'], 1)

    def test_enter_clear_respects_timestamp_deadline(self):
        for enter in ('return', 'num_enter'):
            with self.subTest(enter=enter):
                state = ArithmeticInput()
                state.edit('num_2', .2)
                before = state.snapshot(2)
                for rt in (-.1, float('nan'), float('inf'), 5, 5.001):
                    self.assertIsNone(state.edit(enter, rt))
                    self.assertEqual(state.snapshot(2), before)
                self.assertEqual(state.edit(enter, 4.999999), 'clear')
                final = state.snapshot(2, completed=True)
                self.assertEqual(final['correct'], 0)
                self.assertEqual(final['unanswered'], 1)
                self.assertEqual(final['first_input_rt_seconds'], .2)
                self.assertEqual(final['last_edit_rt_seconds'], 4.999999)

    def test_deadline_ignored_keys_omission_and_partial_result(self):
        state = ArithmeticInput()
        for key in ('return', 'num_enter', 'num_add', 'num_decimal', 'a', 'space', 'backspace', 'delete'):
            self.assertIsNone(state.edit(key, 1))
        self.assertEqual(state.snapshot(2, completed=True)['unanswered'], 1)
        self.assertEqual(state.edit('num_2', 4.999999), 'append')
        self.assertIsNone(state.edit('num_3', 5))
        self.assertIsNone(state.edit('return', 5.001))
        self.assertIsNone(state.edit('num_4', -.1))
        self.assertEqual(state.snapshot(2, completed=True)['correct'], 1)
        self.assertEqual(state.snapshot(2)['correct'], '')
        self.assertEqual(state.snapshot(2)['completed'], 0)
        state = ArithmeticInput()
        state.edit('num_0', 1)
        self.assertEqual(state.snapshot(2, completed=True)['unanswered'], 0)
        self.assertEqual(state.snapshot(2, completed=True)['answer_value'], 0)


if __name__ == '__main__':
    unittest.main()
