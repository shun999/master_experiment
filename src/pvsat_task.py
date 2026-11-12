"""PVSAT timing, exact target counts and timestamped responses; no GUI/SDK."""

import math
import random


DIGITS = tuple(range(1, 10))
INTERVAL_BY_DIFFICULTY = {1: 2.5, 2: 2.0, 3: 1.5}


def presentation_settings(difficulty, active_seconds=60.0, stimulus_seconds=0.5):
    if type(difficulty) is not int or difficulty not in INTERVAL_BY_DIFFICULTY:
        raise ValueError("PVSAT difficulty must be 1..3")
    interval = INTERVAL_BY_DIFFICULTY[difficulty]
    if not math.isfinite(active_seconds) or active_seconds <= 0:
        raise ValueError("Task duration must be finite and positive")
    if not math.isfinite(stimulus_seconds) or not 0 < stimulus_seconds < interval:
        raise ValueError("Stimulus duration must be shorter than the presentation interval")
    count = int(round(active_seconds / interval))
    if count < 2 or not math.isclose(count * interval, active_seconds, abs_tol=1e-9):
        raise ValueError("Task duration must contain a whole number of presentation intervals")
    return interval, interval - stimulus_seconds, count


def make_pvsat_design(count, seed, target_sum=11, target_proportion=0.30):
    """Shuffle target positions, then construct a feasible adjacent-digit chain.

    A backwards feasibility pass avoids accidental targets and impossible
    transitions (e.g. digit 1 cannot precede a sum-11 target with digits 1..9).
    The first presentation is memory-only. Target counts use nearest integer.
    """
    if type(count) is not int or count < 2:
        raise ValueError("At least two presentations are required")
    if type(target_sum) is not int or not 2 <= target_sum <= 18:
        raise ValueError("TARGET_SUM must be an integer from 2 to 18 for digits 1..9")
    if not math.isfinite(target_proportion) or not 0 <= target_proportion <= 1:
        raise ValueError("Target proportion must be between 0 and 1")
    rng = random.Random(seed)
    target_count = int(math.floor((count - 1) * target_proportion + .5))
    for _ in range(128):
        targets = set(rng.sample(range(1, count), target_count))
        suffix_valid = set(DIGITS)
        choices = {}
        for index in range(count - 1, 0, -1):
            choices[index] = {previous: [digit for digit in DIGITS
                if digit in suffix_valid and ((previous + digit == target_sum) == (index in targets))]
                for previous in DIGITS}
            suffix_valid = {previous for previous in DIGITS if choices[index][previous]}
        if not suffix_valid:
            continue
        sequence = [rng.choice([digit for digit in DIGITS if digit in suffix_valid])]
        for index in range(1, count):
            sequence.append(rng.choice(choices[index][sequence[-1]]))
        return [{"trial": index + 1, "stimulus": str(digit),
                 "previous_digit": sequence[index - 1] if index else "",
                 "pair_sum": sequence[index - 1] + digit if index else "",
                 "target_sum": target_sum, "response_required": int(index > 0),
                 "is_target": int(index in targets) if index else "",
                 "expected_key": ("right" if index in targets else "left") if index else ""}
                for index, digit in enumerate(sequence)]
    raise ValueError("Cannot generate a PVSAT sequence for these target settings")


class PvsatResponse:
    """Keep only the first eligible keydown strictly before the deadline."""

    def __init__(self, response_required, duration_seconds):
        self.response_required = bool(response_required)
        self.duration_seconds = duration_seconds
        self.key = ""
        self.rt_seconds = ""

    def press(self, name, rt):
        if (not self.response_required or self.key or name not in ("left", "right")
                or not math.isfinite(rt) or not 0 <= rt < self.duration_seconds):
            return False
        self.key, self.rt_seconds = name, round(rt, 6)
        return True
