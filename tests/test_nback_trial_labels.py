import csv
from pathlib import Path
import tempfile
import unittest

from tools.tobii_nback_1200hz_features import TrialLabels


class TrialLabelTests(unittest.TestCase):
    def labels(self, root, rows, events=()):
        base = Path(root) / 'sample'
        with Path(str(base) + '_trials.csv').open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        with Path(str(base) + '_events.csv').open('w', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['event', 'system_time_stamp_us'])
            writer.writerows(events)
        return TrialLabels(base, {'isi_seconds': 1.5})

    def test_explicit_windows_exclude_rest_and_sort_together(self):
        rows = [dict(trial=31, stim_on_us=91000000, stim_off_us=91500000, response_window_end_us=93000000),
                dict(trial=30, stim_on_us=59000000, stim_off_us=59500000, response_window_end_us=61000000)]
        with tempfile.TemporaryDirectory() as root:
            labels = self.labels(root, rows, [('task_end', 61000000), ('task_end', 151000000)])
        self.assertEqual(labels.for_timestamp(59000000)[:2], ('30', 'stimulus'))
        self.assertEqual(labels.for_timestamp(60000000)[:2], ('30', 'isi'))
        self.assertEqual(labels.for_timestamp(61000000), ('', '', ''))
        self.assertEqual(labels.for_timestamp(85000000), ('', '', ''))
        self.assertEqual(labels.for_timestamp(91000000)[:2], ('31', 'stimulus'))
        self.assertEqual(labels.for_timestamp(94000000), ('', '', ''))

    def test_old_csv_single_and_multiple_task_ends(self):
        rows = [dict(trial=1, stim_on_us=1000000, stim_off_us=1500000),
                dict(trial=2, stim_on_us=40000000, stim_off_us=40500000)]
        for events in ([('task_end', 3000000), ('task_end', 42000000)], [('task_end', 42000000)], []):
            with self.subTest(events=events), tempfile.TemporaryDirectory() as root:
                labels = self.labels(root, rows, events)
            self.assertEqual(labels.for_timestamp(2000000)[:2], ('1', 'isi'))
            self.assertEqual(labels.for_timestamp(5000000), ('', '', ''))
            self.assertEqual(labels.for_timestamp(41500000)[:2], ('2', 'isi'))
            self.assertEqual(labels.for_timestamp(43000000), ('', '', ''))

    def test_interrupted_trial_has_finite_window(self):
        rows = [dict(trial=1, stim_on_us=1000000, stim_off_us='', response_window_end_us=1200000)]
        with tempfile.TemporaryDirectory() as root:
            labels = self.labels(root, rows)
        self.assertEqual(labels.for_timestamp(1100000)[:2], ('1', 'stimulus'))
        self.assertEqual(labels.for_timestamp(1200000), ('', '', ''))


if __name__ == '__main__':
    unittest.main()
