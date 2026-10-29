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
    def assert_brief_controls(self, text):
        self.assertIn('スペースキー（SPACE）：準備完了', text)
        self.assertIn('エスケープキー（ESC）：中断', text)
        for phrase in ('例：', '1ブロック', '休憩', '質問紙', '回実施', '試行（課題'):
            self.assertNotIn(phrase, text)

    def test_brief_nback_rules_in_japanese(self):
        with tempfile.TemporaryDirectory() as root:
            task = Rig().load(root, full=True)
            for session, n in ((1, 0), (2, 1), (4, 2), (3, 3)):
                assignment = task.load_nback_assignment(task.COUNTERBALANCE_CSV, 'DNV', session)
                text = task.participant_instructions(assignment)
                self.assertIn('{}-back課題'.format(n), text)
                self.assertIn('刺激・「＋」表示中に1回回答', text)
                self.assert_brief_controls(text)
                if n == 0:
                    self.assertIn('「X」：右矢印／数字：左矢印', text)
                    self.assertNotIn('各ブロックの最初', text)
                else:
                    self.assertIn('{}個前の数字と一致：右矢印／不一致：左矢印'.format(n), text)
                    self.assertIn('各ブロックの最初の{}試行：左矢印'.format(n), text)
            task.ZERO_BACK_TARGET = 'Z'
            zero = task.load_nback_assignment(task.COUNTERBALANCE_CSV, 'DNV', 1)
            self.assertIn('「Z」：右矢印／数字：左矢印', task.participant_instructions(zero))

    def test_arithmetic_and_pvsat_keep_condition_specific_rules(self):
        with tempfile.TemporaryDirectory() as root:
            arithmetic = ArithmeticRig().load_arithmetic(root)
            for subject, condition in (('AFX', '1桁 + 1桁'), ('BKR', '2桁 + 2桁'),
                                       ('DNV', '3桁と2桁・左右ランダム')):
                assignment = arithmetic.load_arithmetic_assignment(arithmetic.COUNTERBALANCE_CSV, subject, 1)
                text = arithmetic.participant_instructions(assignment)
                self.assertIn('暗算課題（{}）'.format(condition), text)
                self.assertNotIn('3桁 + 3桁', text)
                self.assertIn('テンキーで和を入力', text)
                self.assertIn('ナムロック（NumLock）：ON', text)
                self.assertIn('エンターキー（Enter）：全消去', text)
                self.assertNotIn('Backspace', text)
                self.assertNotIn('Delete', text)
                self.assertIn('5秒後に自動採点', text)
                self.assert_brief_controls(text)
            arithmetic.TRIAL_SECONDS = 4.5
            self.assertIn('4.5秒後に自動採点', arithmetic.participant_instructions(assignment))
            pvsat = PvsatRig().load_pvsat(root)
            for subject, session, interval in (('DNV', 1, 2.5), ('JXZ', 2, 2.0), ('AFX', 1, 1.5)):
                assignment = pvsat.load_pvsat_assignment(pvsat.COUNTERBALANCE_CSV, subject, session)
                text = pvsat.participant_instructions(assignment)
                self.assertIn('PVSAT課題（提示間隔{:g}秒）'.format(interval), text)
                self.assertIn('直前の数字との和が11：右矢印／それ以外：左矢印', text)
                self.assertIn('各ブロックの最初の数字は記憶のみ（回答なし）', text)
                self.assertIn('2個目から、数字・「＋」表示中に1回回答', text)
                self.assert_brief_controls(text)
            pvsat.TARGET_SUM = 10
            self.assertIn('直前の数字との和が10：右矢印／それ以外：左矢印',
                          pvsat.participant_instructions(assignment))

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
