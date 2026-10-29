# -*- coding: utf-8 -*-
"""Fixed teaching examples and short practice tasks; no PsychoPy or devices."""

import math
import random
from typing import NamedTuple

from .arithmetic_task import make_arithmetic_design
from .pvsat_task import make_pvsat_design


class DemoSpec(NamedTuple):
    task: str
    title: str
    condition: str
    interval: float
    stimulus_seconds: float
    count: int
    rule: str


def demo_spec(task):
    specs = {
        'nback': DemoSpec('nback', 'N-back', '1-back', 2.0, .5, 10,
                         '1個前と同じ数字なら右矢印、異なる数字なら左矢印です。'),
        'arithmetic': DemoSpec('arithmetic', '暗算', '難易度1：1桁＋1桁', 5.0, 5.0, 4,
                              'テンキーで、表示された2つの数字の和を入力します。'),
        'pvsat': DemoSpec('pvsat', 'PVSAT', '難易度1：目標値11', 2.5, .5, 8,
                         '直前の数字との和が11なら右矢印、違うなら左矢印です。'),
    }
    if task not in specs:
        raise ValueError('Unknown demo task: ' + str(task))
    return specs[task]


def _digit_rows(task, sequence):
    rows = []
    for index, digit in enumerate(sequence):
        previous = sequence[index - 1] if index else None
        required = task == 'nback' or index > 0
        if task == 'nback':
            expected = 'right' if previous == digit else 'left'
            reason = ('最初の数字には比較する数字がないので、左矢印キーです。' if previous is None else
                      '{}と{}は{}ので、{}矢印キーです。'.format(
                          previous, digit, '同じな' if previous == digit else '異なる',
                          '右' if previous == digit else '左'))
        else:
            expected = '' if not required else ('right' if previous + digit == 11 else 'left')
            reason = ('最初の数字は覚えるだけで、回答しません。' if previous is None else
                      '{}＋{}＝{}。11と{}ので、{}矢印キーです。'.format(
                          previous, digit, previous + digit,
                          '一致する' if previous + digit == 11 else '一致しない',
                          '右' if previous + digit == 11 else '左'))
        rows.append(dict(trial=index + 1, stimulus=str(digit), previous=previous,
                         expected=expected, response_required=required, reason=reason))
    return rows


def _arithmetic_rows(rows):
    return [dict(trial=index + 1, stimulus=row['stimulus'], previous=None,
                 expected=row['correct_answer'], response_required=True,
                 reason='{}＋{}＝{}です。\nテンキーで「{}」を入力してください。'.format(
                     row['operand_a'], row['operand_b'], row['correct_answer'], row['correct_answer']))
            for index, row in enumerate(rows)]


def guide_rows(task):
    demo_spec(task)
    if task == 'nback':
        return _digit_rows(task, [3, 8, 8, 2, 2, 5])
    if task == 'pvsat':
        return _digit_rows(task, [5, 6, 4, 7, 2, 9])
    return _arithmetic_rows([dict(operand_a=a, operand_b=b, correct_answer=a + b,
                                  stimulus='{} + {} = ?'.format(a, b))
                             for a, b in ((3, 4), (8, 5), (2, 6), (7, 9))])


def practice_rows(task, seed):
    spec = demo_spec(task)
    if task == 'arithmetic':
        return _arithmetic_rows(make_arithmetic_design(1, spec.count, seed))
    if task == 'pvsat':
        plans = make_pvsat_design(spec.count, seed, target_sum=11, target_proportion=.30)
        return _digit_rows(task, [int(row['stimulus']) for row in plans])
    rng = random.Random(seed)
    targets = set(rng.sample(range(1, spec.count), 3))
    sequence = [rng.randrange(10)]
    for index in range(1, spec.count):
        previous = sequence[-1]
        sequence.append(previous if index in targets else rng.choice([d for d in range(10) if d != previous]))
    return _digit_rows(task, sequence)


def expected_label(row):
    if not row['response_required']:
        return '回答なし（記憶のみ）'
    if row['expected'] in ('left', 'right'):
        return '左矢印キー（←）' if row['expected'] == 'left' else '右矢印キー（→）'
    return str(row['expected'])


class DemoAnswer:
    """Unlimited guide editing or timestamp-bounded practice responses.

    Numeric editing follows the production task: 4 digits, leading zeros,
    Enter clears all digits. No research RT data is recorded.
    """

    def __init__(self, task, row, deadline=None):
        self.task, self.row, self.deadline = task, row, deadline
        self.text, self.key = '', ''

    @property
    def correct(self):
        if not self.row['response_required']:
            return None
        if self.task == 'arithmetic':
            return bool(self.text) and int(self.text) == self.row['expected']
        return self.key == self.row['expected']

    @property
    def response_label(self):
        if not self.row['response_required']:
            return '回答なし（記憶のみ）'
        if self.task == 'arithmetic':
            return self.text or '未回答'
        return {'left': '左矢印キー（←）', 'right': '右矢印キー（→）', '': '未回答'}[self.key]

    def press(self, name, rt):
        if (not self.row['response_required'] or not math.isfinite(rt) or rt < 0
                or (self.deadline is not None and rt >= self.deadline)):
            return False
        if self.task != 'arithmetic':
            if name not in ('left', 'right') or (self.key and (self.deadline is not None or self.correct)):
                return False
            self.key = name
            return True
        digit = name[4:] if name.startswith('num_') else name
        if len(digit) == 1 and digit in '0123456789' and len(self.text) < 4:
            self.text += digit
        elif name in ('return', 'num_enter') and self.text:
            self.text = ''
        else:
            return False
        return True
