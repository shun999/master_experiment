import csv
from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import types
import unittest

from src.experiment_runtime import GAZE_COLUMNS, StreamRecorder
from src.experiment_paths import create_output_prefix


class Tracker:
    def subscribe_to(self, kind, callback, as_dictionary):
        self.callback = callback

    def unsubscribe_from(self, kind, callback):
        self.callback = None


class SharedRecorderTests(unittest.TestCase):
    def settings(self, raw=False):
        return {'QUEUE_CAPACITY': 8, 'SAVE_GAZE_RAW_JSONL': raw, 'RECORD_EYE_OPENNESS': False,
                'RECORD_OTHER_STREAMS': False, 'RECORD_EYE_IMAGES': False}

    def test_real_writer_keeps_settings_local_and_records_fields_and_quality(self):
        sdk = types.SimpleNamespace(EYETRACKER_GAZE_DATA=1)
        with tempfile.TemporaryDirectory() as root, redirect_stdout(io.StringIO()):
            prefixes = [create_output_prefix(root, 'nback', 'AFX', 1, 3, 2),
                        create_output_prefix(root, 'arithmetic', 'AFX', 1, 1)]
            settings = self.settings(True)
            recorders = [StreamRecorder(prefixes[0], settings, sdk),
                         StreamRecorder(prefixes[1], self.settings(False), sdk)]
            settings['SAVE_GAZE_RAW_JSONL'] = False  # Snapshot must remain independent.
            for recorder in recorders:
                tracker = Tracker()
                recorder.subscribe(tracker, 'gaze', 'EYETRACKER_GAZE_DATA', required=True)
                for timestamp in (1000, 8000, 8000):
                    tracker.callback({'device_time_stamp': timestamp, 'system_time_stamp': timestamp + 100,
                                      'left_pupil_diameter': 3.5, 'left_pupil_validity': 1,
                                      'left_gaze_point_on_display_area': (.5, .4)})
                recorder.unsubscribe_all(tracker)
                recorder.close()
                self.assertIsNone(recorder.writer_error)
                self.assertFalse(recorder.thread.is_alive())
                self.assertEqual(recorder.written['gaze'], 3)
                self.assertEqual(recorder.large_device_gaps['gaze'], 1)
                self.assertEqual(recorder.time_backwards_or_equal['gaze'], 1)
            for prefix in prefixes:
                with Path(str(prefix) + '_gaze.csv').open(encoding='utf-8') as stream:
                    reader = csv.DictReader(stream)
                    rows = list(reader)
                    self.assertEqual(reader.fieldnames, GAZE_COLUMNS)
                self.assertEqual(len(rows), 3)
                self.assertEqual(rows[0]['left_pupil_diameter_diameter_mm'], '3.5')
                self.assertEqual(rows[0]['left_gaze_point_on_display_area_x_norm'], '0.5')
            self.assertTrue(Path(str(prefixes[0]) + '_gaze_raw.jsonl').exists())
            self.assertFalse(Path(str(prefixes[1]) + '_gaze_raw.jsonl').exists())

    def test_open_error_drains_queue_and_closes(self):
        with tempfile.TemporaryDirectory() as root, redirect_stdout(io.StringIO()):
            recorder = StreamRecorder(Path(root) / 'missing' / 'run', self.settings(), types.SimpleNamespace())
            callback = recorder._callback('gaze', 'test')
            callback({'device_time_stamp': 1})
            recorder.close()
            self.assertIn('Opening output files', recorder.writer_error)
            self.assertFalse(recorder.thread.is_alive())


if __name__ == '__main__':
    unittest.main()
