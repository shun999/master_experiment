# -*- coding: utf-8 -*-
from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest

from src.experiment_runtime import calibration_message_ja
from tests.test_arithmetic_experiment import ArithmeticRig
from tests.test_nback_dual_screen import Rig
from tests.test_pvsat_experiment import PvsatRig


class JapaneseInstructionsTests(unittest.TestCase):
    def test_nback_rules_and_configured_times_in_japanese(self):
        with tempfile.TemporaryDirectory() as root:
            task = Rig().load(root, full=True)
            task.N_TRIALS, task.N_BLOCKS, task.REST_SECONDS = 12, 2, 15
            for session, n in ((1, 0), (2, 1), (4, 2), (3, 3)):
                assignment = task.load_nback_assignment(task.COUNTERBALANCE_CSV, 'DNV', session)
                text = task.participant_instructions(assignment)
                self.assertIn('{}-back課題'.format(n), text)
                self.assertIn('1ブロック12試行（課題24秒）', text)
                self.assertIn('休憩15秒を含むブロックを2回', text)
                self.assertIn('その後の「＋」表示中に回答できます', text)
                self.assertIn('スペースキー（SPACE）：準備完了', text)
                self.assertIn('エスケープキー（ESC）：中断', text)
                if n == 0:
                    self.assertIn('「X」が表示されたら、右矢印キー', text)
                    self.assertIn('0〜9の数字が表示されたら、左矢印キー', text)
                    self.assertNotIn('各ブロックの最初', text)
                else:
                    self.assertIn('{}個前の数字と同じなら、右矢印キー'.format(n), text)
                    self.assertIn('異なる数字なら、左矢印キー', text)
                    self.assertIn('最初の{}試行は、左矢印キー'.format(n), text)

    def test_arithmetic_and_pvsat_keep_condition_specific_rules(self):
        with tempfile.TemporaryDirectory() as root:
            arithmetic = ArithmeticRig().load_arithmetic(root)
            for subject, digits in (('AFX', 1), ('BKR', 2), ('DNV', 3)):
                assignment = arithmetic.load_arithmetic_assignment(arithmetic.COUNTERBALANCE_CSV, subject, 1)
                text = arithmetic.participant_instructions(assignment)
                self.assertIn('暗算課題（{}桁 + {}桁）'.format(digits, digits), text)
                self.assertIn('ナムロック（NumLock）を有効', text)
                self.assertIn('バックスペースキー（Backspace）：1文字削除', text)
                self.assertIn('デリートキー（Delete）：全削除', text)
                self.assertIn('エンターキー（Enter）での確定は不要', text)
                self.assertIn('5.0秒後に自動採点', text)
                self.assertIn('1ブロック12問', text)
                self.assertIn('3回実施', text)
            pvsat = PvsatRig().load_pvsat(root)
            for subject, session, count, interval in (('DNV', 1, 24, 2.5), ('JXZ', 2, 30, 2.0), ('AFX', 1, 40, 1.5)):
                assignment = pvsat.load_pvsat_assignment(pvsat.COUNTERBALANCE_CSV, subject, session)
                text = pvsat.participant_instructions(assignment)
                self.assertIn('和が11と一致：右矢印', text)
                self.assertIn('一致しない：左矢印', text)
                self.assertIn('最初の数字は覚えるだけで、回答しません', text)
                self.assertIn('合計を足し続けないでください', text)
                self.assertIn('1ブロック{}個'.format(count), text)
                self.assertIn('{}秒ごとに切り替わります'.format(interval), text)

    def test_all_three_tasks_show_japanese_guidance_and_keep_original_calibration_logs(self):
        for task_name in ('nback', 'arithmetic', 'pvsat'):
            with self.subTest(task=task_name), tempfile.TemporaryDirectory() as root:
                if task_name == 'nback':
                    rig = Rig()
                    task = rig.load(root)
                elif task_name == 'arithmetic':
                    rig = ArithmeticRig()
                    task = rig.load_arithmetic(root)
                else:
                    rig = PvsatRig()
                    task = rig.load_pvsat(root)
                task.SKIP_CALIBRATION = False
                with redirect_stdout(io.StringIO()):
                    task.main('DNV', 2)
                participant = [text for _, drawn, _ in rig.windows[1].history for text in drawn]
                operator = [text for _, drawn, _ in rig.windows[0].history for text in drawn]
                self.assertTrue(any('赤い点を順番に見てください' in text for text in participant))
                self.assertTrue(any('スペースキー（SPACE）で開始' in text for text in participant))
                self.assertTrue(any('スペースキー（SPACE）で終了します' in text for text in participant))
                for label in ('校正モードを開始しています', '校正結果を計算して適用しています',
                              '校正モードを終了しています', '校正モードを終了しました'):
                    self.assertIn(label, operator)
                self.assertEqual(sum(text.startswith('校正点を取得しています') for text in operator), 5)
                all_text = '\n'.join(participant + operator)
                for phrase in ('Look at each red dot', 'Calibration complete', 'RIGHT arrow:',
                               'LEFT arrow:', 'CALIBRATION:', 'SPACE: ready', 'ESC: exit'):
                    self.assertNotIn(phrase, all_text)
                messages = [stim for stim in rig.texts if stim.win == rig.windows[1]
                            and stim.text.startswith('回答を保存しました')]
                self.assertEqual(len(messages), 1)
                self.assertEqual(messages[0].settings['font'], 'Yu Gothic')
                log = Path(str(rig.recorder.base) + '_progress.log').read_text(encoding='utf-8')
                self.assertIn('CALIBRATION: enter SDK calibration mode', log)
                self.assertIn('CALIBRATION: collect point 0.5 0.5', log)
                self.assertIn('CALIBRATION: compute/apply', log)
                self.assertIn('CALIBRATION: left calibration mode', log)

    def test_calibration_retry_coordinates_and_unknown_stage_are_japanese(self):
        retry = calibration_message_ja('CALIBRATION: retry point 0.12 0.88')
        self.assertIn('再取得', retry)
        self.assertIn('0.12', retry)
        self.assertIn('0.88', retry)
        self.assertNotIn('CALIBRATION:', retry)
        self.assertEqual(calibration_message_ja('CALIBRATION: unknown diagnostic detail'), '校正処理を実行しています')


if __name__ == '__main__':
    unittest.main()
