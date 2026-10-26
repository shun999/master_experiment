import unittest

from src.pvsat_task import (PvsatResponse, make_pvsat_design, presentation_settings)


class PvsatDesignTests(unittest.TestCase):
    def test_timing_conditions(self):
        for level, expected in ((1, (2.5, 2, 24)), (2, (2, 1.5, 30)), (3, (1.5, 1, 40))):
            self.assertEqual(presentation_settings(level), expected)
        for args in ((0,), (4,), (True,), (1, 59), (1, 60, 2.5), (1, float('nan'))):
            with self.assertRaises(ValueError):
                presentation_settings(*args)

    def test_exact_targets_adjacent_sums_and_reproducibility(self):
        for count, expected in ((24, 7), (30, 9), (40, 12)):
            for seed in range(40):
                rows = make_pvsat_design(count, seed)
                self.assertEqual(rows, make_pvsat_design(count, seed))
                self.assertEqual(len(rows), count)
                self.assertEqual(sum(row['is_target'] == 1 for row in rows), expected)
                for index, row in enumerate(rows):
                    self.assertIn(int(row['stimulus']), range(1, 10))
                    self.assertEqual(row['trial'], index + 1)
                    self.assertEqual(row['response_required'], int(index > 0))
                    if not index:
                        for key in ('previous_digit', 'pair_sum', 'is_target', 'expected_key'):
                            self.assertEqual(row[key], '')
                    else:
                        self.assertEqual(row['previous_digit'], int(rows[index - 1]['stimulus']))
                        self.assertEqual(row['pair_sum'], row['previous_digit'] + int(row['stimulus']))
                        self.assertEqual(row['is_target'], int(row['pair_sum'] == 11))
                        self.assertEqual(row['expected_key'], 'right' if row['pair_sum'] == 11 else 'left')
        self.assertNotEqual(make_pvsat_design(40, 1), make_pvsat_design(40, 2))

    def test_configurable_goal_and_invalid_settings(self):
        for goal in (2, 10, 12, 18):
            rows = make_pvsat_design(24, 1, goal)
            self.assertEqual(sum(row['is_target'] == 1 for row in rows), 7)
            self.assertTrue(all((row['pair_sum'] == goal) == bool(row['is_target']) for row in rows[1:]))
        for proportion in (0, 1):
            rows = make_pvsat_design(24, 1, target_proportion=proportion)
            self.assertEqual(sum(row['is_target'] == 1 for row in rows), 23 * proportion)
        for args in ((1, 1), (24, 1, 1), (24, 1, 19), (24, 1, True),
                     (24, 1, 11, -.1), (24, 1, 11, float('nan'))):
            with self.assertRaises(ValueError):
                make_pvsat_design(*args)

    def test_only_first_eligible_keydown_before_deadline_is_accepted(self):
        state = PvsatResponse(True, 1.5)
        for name, rt in (('left', -.1), ('space', .1), ('left', float('nan')), ('right', 1.5), ('right', 1.6)):
            self.assertFalse(state.press(name, rt))
        self.assertTrue(state.press('left', 1.499999))
        self.assertFalse(state.press('right', 1.4999995))
        self.assertEqual((state.key, state.rt_seconds), ('left', 1.499999))
        memory = PvsatResponse(False, 2.5)
        self.assertFalse(memory.press('right', .1))
        self.assertEqual((memory.key, memory.rt_seconds), ('', ''))


if __name__ == '__main__':
    unittest.main()
