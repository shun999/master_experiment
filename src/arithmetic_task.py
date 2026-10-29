# -*- coding: utf-8 -*-
"""Deterministic addition questions and timestamped numeric input; no GUI/SDK."""

import math
import random


# Canonical addend definitions, not fixed left/right positions.
OPERAND_DIGIT_PAIRS = {1: (1, 1), 2: (2, 2), 3: (3, 2)}
OPERAND_RANGES = {
    level: tuple((10 ** (digits - 1), 10 ** digits - 1) for digits in pair)
    for level, pair in OPERAND_DIGIT_PAIRS.items()
}


def count_carries(left, right):
    if any(type(value) is not int or value < 0 for value in (left, right)):
        raise ValueError("Operands must be nonnegative integers")
    count, carry = 0, 0
    while left or right:
        carry = int(left % 10 + right % 10 + carry >= 10)
        count += carry
        left, right = left // 10, right // 10
    return count


def make_arithmetic_design(difficulty, count, seed):
    """Balance carry status; level 3 also balances both addend orientations."""
    if type(difficulty) is not int or difficulty not in OPERAND_RANGES or type(count) is not int or count < 2 or count % 2:
        raise ValueError("Use difficulty 1..3 and an even positive question count")
    if difficulty == 3 and count % 4:
        raise ValueError("Difficulty 3 requires a question count that is a multiple of 4")
    if difficulty == 1 and count // 2 > 20:
        raise ValueError("Too many unique one-digit questions without carry")
    rng = random.Random(seed)
    first_range, second_range = OPERAND_RANGES[difficulty]
    rows, seen = [], set()
    for needs_carry in (False, True):
        selected = 0
        for _ in range(10000):
            left, right = rng.randint(*first_range), rng.randint(*second_range)
            carries = count_carries(left, right)
            pair = tuple(sorted((left, right)))
            if bool(carries) != needs_carry or pair in seen:
                continue
            seen.add(pair)
            # Half of each carry group is reversed before shuffling all rows.
            if difficulty == 3 and selected >= count // 4:
                left, right = right, left
            rows.append({"operand_a": left, "operand_b": right,
                         "stimulus": "{} + {} = ?".format(left, right),
                         "correct_answer": left + right,
                         "carry_count": carries, "has_carry": int(needs_carry)})
            selected += 1
            if selected == count // 2:
                break
        else:
            raise ValueError("Could not generate enough distinct questions")
    rng.shuffle(rows)
    for index, row in enumerate(rows, 1):
        row["trial"] = index
    return rows


class ArithmeticInput:
    def __init__(self, duration_seconds=5.0, max_digits=4):
        if not math.isfinite(duration_seconds) or duration_seconds <= 0 or type(max_digits) is not int or max_digits < 1:
            raise ValueError("Input duration and digit limit must be positive")
        self.duration_seconds = duration_seconds
        self.max_digits = max_digits
        self.text = ""
        self.first_input_rt_seconds = None
        self.last_edit_rt_seconds = None

    def edit(self, key_name, rt):
        """Return the effective edit action, or None for ignored/late keys."""
        if not math.isfinite(rt) or not 0 <= rt < self.duration_seconds:
            return None
        digit = key_name[4:] if key_name.startswith("num_") else key_name
        if len(digit) == 1 and digit in "0123456789" and len(self.text) < self.max_digits:
            self.text += digit
            if self.first_input_rt_seconds is None:
                self.first_input_rt_seconds = round(rt, 6)
            action = "append"
        elif key_name in ("return", "num_enter") and self.text:
            self.text = ""
            action = "clear"
        else:
            return None
        self.last_edit_rt_seconds = round(rt, 6)
        return action

    def snapshot(self, correct_answer, completed=False):
        value = int(self.text) if self.text else None
        return {"answer_text": self.text, "answer_value": value,
                "unanswered": int(value is None),
                "first_input_rt_seconds": self.first_input_rt_seconds,
                "last_edit_rt_seconds": self.last_edit_rt_seconds,
                "correct": int(value == correct_answer) if completed else "",
                "completed": int(completed)}
