# -*- coding: utf-8 -*-
"""Lightweight N-back, Tobii Pro Spectrum 1200 Hz, PsychoPy 2022.2.4 Coder.

Starting settings record full SDK gaze quantities in ONE CSV (incl. both pupils,
2D/3D gaze, gaze origins, validities, timestamps) at 1200 Hz. Eye images and
other SDK streams can be enabled separately once baseline runs correctly.

This example has NOT been validated on real Spectrum hardware.
Compatible Python/Tobii SDK versions depend on the exact Standalone install.
"""

# ========================= EDIT THESE SETTINGS ===========================
# Participant/session and N value are selected from counterbalance.csv at startup.
N_TRIALS = 30               # Trials per block
N_BLOCKS = 6
REST_SECONDS = 30.0
STIMULUS_SECONDS = 0.50
ISI_SECONDS = 1.50
TARGET_PROBABILITY = 0.30
DIGITS = "0123456789"
ZERO_BACK_TARGET = "X"       # Fixed for all participants and blocks
TOBII_FREQUENCY_HZ = 1200     # Fail fast if this device cannot actually offer 1200 Hz
TOBII_SERIAL = None           # Set if more than one tracker is on the network
SCREEN_INDEX = 1             # Participant monitor (extended desktop)
OPERATOR_SCREEN_INDEX = 0    # Separate operator monitor
OPERATOR_WINDOW_SIZE = (960, 640)  # Operator window size in pixels
OPERATOR_REFRESH_HZ = 5.0    # Maximum dashboard refresh rate during the task
SYNC_COUNTDOWN_SECONDS = 10  # Countdown before each manual marker cue
SYNC_CUE_SECONDS = 1.0       # Cue runs concurrently with the task/rest
SKIP_CALIBRATION = False
# Start with only 1200-Hz gaze CSV. Turn on options one at a time after testing.
RECORD_EYE_OPENNESS = True       # Adds ~1200 events/sec; try enabling SECOND
RECORD_OTHER_STREAMS = False     # Time sync, external TTL, notifications, errors
RECORD_EYE_IMAGES = False        # More bandwidth/data; enable LAST
SAVE_GAZE_RAW_JSONL = False      # Duplicate of gaze CSV for documented gaze fields
SAVE_CALIBRATION_DATA = True    # Skip extra post-calibration SDK request for diagnosis
RECORD_USER_POSITION_DURING_TASK = False  # 1.10.1 interference risk
QUEUE_CAPACITY = 20000          # Maximum pending events; reducing does not boost FPS
START_KEY_TIMEOUT_SECONDS = 120 # Diagnoses "freeze" at post-calibration start screen
SEED = None                 # None: new random sequence each run
# ========================================================================

import csv
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import queue
import random
import sys
import threading
import time

from psychopy import core, visual
from psychopy.hardware import keyboard
import psychopy
import tobii_research as tr
from nasa_tlx import RawTLXSession, run_raw_tlx
from experiment_assignment import (NBACK_BY_DIFFICULTY, load_nback_assignment,
                                   prompt_nback_assignment)

OUTPUT_DIR = Path(__file__).resolve().parent / "nback_data"
COUNTERBALANCE_CSV = Path(__file__).resolve().parent / "counterbalance.csv"

# Gaze data: all documented left/right-eye gaze and pupil quantities,
# plus timestamps. Raw dictionaries are separately written to JSON Lines.
EYE_FIELDS = (
    ("gaze_point_on_display_area", ("x_norm", "y_norm")),
    ("gaze_point_in_user_coordinate_system", ("x_mm", "y_mm", "z_mm")),
    ("gaze_point_validity", ("valid",)),
    ("gaze_origin_in_trackbox_coordinate_system", ("x_norm", "y_norm", "z_norm")),
    ("gaze_origin_in_user_coordinate_system", ("x_mm", "y_mm", "z_mm")),
    ("gaze_origin_validity", ("valid",)),
    ("pupil_diameter", ("diameter_mm",)),
    ("pupil_validity", ("valid",)),
)
GAZE_COLUMNS = ["device_time_stamp_us", "system_time_stamp_us"]
for _side in ("left", "right"):
    for _field, _components in EYE_FIELDS:
        GAZE_COLUMNS.extend(f"{_side}_{_field}_{c}" for c in _components)

EVENT_COLUMNS = ["event", "trial", "stimulus", "system_time_stamp_us", "block"]
TRIAL_COLUMNS = [
    "trial", "stimulus", "is_target", "response_key", "correct",
    "rt_seconds", "stim_on_us", "stim_off_us", "response_detected_us",
    "block", "trial_in_block", "response_window_end_us",
    "planned_onset_seconds", "onset_error_seconds", "completed",
]

OPTIONAL_STREAMS = [
    ("eye_openness", "EYETRACKER_EYE_OPENNESS_DATA"),
    ("time_sync", "EYETRACKER_TIME_SYNCHRONIZATION_DATA"),
    ("external_signal", "EYETRACKER_EXTERNAL_SIGNAL"),
    ("stream_errors", "EYETRACKER_STREAM_ERRORS"),
]
NOTIFICATIONS = [
    "EYETRACKER_NOTIFICATION_CONNECTION_LOST",
    "EYETRACKER_NOTIFICATION_CONNECTION_RESTORED",
    "EYETRACKER_NOTIFICATION_CALIBRATION_MODE_ENTERED",
    "EYETRACKER_NOTIFICATION_CALIBRATION_MODE_LEFT",
    "EYETRACKER_NOTIFICATION_CALIBRATION_CHANGED",
    "EYETRACKER_NOTIFICATION_DISPLAY_AREA_CHANGED",
    "EYETRACKER_NOTIFICATION_GAZE_OUTPUT_FREQUENCY_CHANGED",
    "EYETRACKER_NOTIFICATION_EYE_TRACKING_MODE_CHANGED",
    "EYETRACKER_NOTIFICATION_DEVICE_FAULTS",
    "EYETRACKER_NOTIFICATION_DEVICE_WARNINGS",
    "EYETRACKER_NOTIFICATION_STREAM_BUFFER_OVERFLOW",
]


class ExperimentAborted(Exception):
    pass


def make_design(n, count, seed):
    rng = random.Random(seed)
    sequence, design = [], []
    for i in range(count):
        if n == 0:
            target = rng.random() < TARGET_PROBABILITY
            stimulus = ZERO_BACK_TARGET if target else rng.choice(DIGITS)
            design.append({"trial": i + 1, "stimulus": stimulus, "is_target": int(target)})
            continue
        target = i >= n and rng.random() < TARGET_PROBABILITY
        if target:
            digit = sequence[i - n]
        else:
            candidate = DIGITS if i < n else DIGITS.replace(sequence[i - n], "")
            digit = rng.choice(candidate)
        sequence.append(digit)
        design.append({"trial": i + 1, "stimulus": digit,
                       "is_target": int(i >= n and digit == sequence[i - n])})
    return design


def participant_instructions(assignment):
    n = assignment.n_back
    if n == 0:
        rule = (f"RIGHT arrow: {ZERO_BACK_TARGET}.\n"
                "LEFT arrow: any digit (0-9).\n")
    else:
        rule = (f"RIGHT arrow: same digit as {n} trials ago.\n"
                "LEFT arrow: different digit.\n"
                f"For the first {n} trials of EACH block, press LEFT.\n")
    return (f"Calibration complete.\n\n{n}-BACK\n" + rule
            + "Respond once on every trial.\n"
            + f"{N_BLOCKS} blocks of {N_TRIALS} trials; {REST_SECONDS:g}-second rest after each block.\n"
            + "Answer the questionnaire after all blocks.\n"
            + "Keep looking at + while waiting for the task.\n\nSPACE: ready   ESC: exit")


def validate_screen_configuration():
    """Reject missing/duplicate monitors rather than pyglet's screen fallback."""
    import pyglet

    if any(type(index) is not int or index < 0
           for index in (SCREEN_INDEX, OPERATOR_SCREEN_INDEX)):
        raise ValueError("SCREEN_INDEX and OPERATOR_SCREEN_INDEX must be nonnegative integers")
    if SCREEN_INDEX == OPERATOR_SCREEN_INDEX:
        raise ValueError("SCREEN_INDEX and OPERATOR_SCREEN_INDEX must select different monitors")
    screens = pyglet.canvas.get_display().get_screens()
    if max(SCREEN_INDEX, OPERATOR_SCREEN_INDEX) >= len(screens):
        raise ValueError("Two extended-desktop monitors are required. Found {}; check "
                         "SCREEN_INDEX={} and OPERATOR_SCREEN_INDEX={}".format(
                             len(screens), SCREEN_INDEX, OPERATOR_SCREEN_INDEX))


def check_escape(kb):
    if kb.getKeys(keyList=["escape"], waitRelease=False, clear=True):
        raise ExperimentAborted("ESC pressed")


class OperatorDisplay:
    """Reusable operator stimuli, updated only on the main/UI thread."""

    def __init__(self, win, assignment):
        self.win = win
        self.assignment = assignment
        self.subject = assignment.participant_id
        self.gaze_frequency = None
        self.stage = "準備中"
        self.detail = ""
        self.trial = 0
        self.block = 0
        self.rest_remaining = None
        self.normal_detail = ""
        self.completed = 0
        self.correct = 0
        self.last_result = None
        self.clock = core.Clock()
        self.task_clock = core.Clock()
        self.task_started = False
        self.elapsed_at_end = None
        self.last_update = float("-inf")
        self.last_text = None
        width, height = win.size
        self.banner = visual.TextStim(
            win, font="Yu Gothic", color="white", height=height * .045,
            pos=(0, height * .39), wrapWidth=width * .92, autoLog=False)
        self.body = visual.TextStim(
            win, font="Yu Gothic", color="white", height=height * .030,
            pos=(-width * .45, height * .27), wrapWidth=width * .90,
            alignText="left", anchorHoriz="left", anchorVert="top", autoLog=False)

    def start_task(self):
        self.task_clock.reset()
        self.task_started = True

    def finish_task(self):
        if self.task_started and self.elapsed_at_end is None:
            self.elapsed_at_end = self.task_clock.getTime()

    def complete_trial(self, record):
        if record["completed"]:
            self.completed += 1
            self.correct += record["correct"]
            self.last_result = dict(record)

    def render(self, force=False, on_flip=None):
        now = self.clock.getTime()
        if not force and on_flip is None and now - self.last_update < 1.0 / OPERATOR_REFRESH_HZ:
            return False
        # Throttle text composition too; elapsed time is shown in whole seconds.
        self.last_update = now
        elapsed = self.elapsed_at_end
        if elapsed is None:
            elapsed = self.task_clock.getTime() if self.task_started else 0
        accuracy = "未集計" if not self.completed else "{:.1f}% ({}/{})".format(
            100.0 * self.correct / self.completed, self.correct, self.completed)
        if self.last_result is None:
            last_response = "直近の完了試行: なし"
        else:
            last = self.last_result
            response = {"left": "左", "right": "右", "": "未回答"}[last["response_key"]]
            rt = "未回答" if last["rt_seconds"] == "" else "{:.3f} 秒".format(last["rt_seconds"])
            last_response = "直近の完了試行 {}: {} / {} / RT {}".format(
                last["trial"], response, "正解" if last["correct"] else "不正解", rt)
        body = (
            "段階: {}\n"
            "協力者ID: {}    課題: {}-back\n"
            "実験回数（セット）: {}/4    難易度番号: {}\n"
            "刺激集合: {}    総試行数: {}\n"
            "刺激: {:.2f} 秒    ISI: {:.2f} 秒\n"
            "ターゲット確率: {:.0f}%    取得周波数: {} Hz\n\n"
            "ブロック: {}/{}    現在の試行: {}/{}\n"
            "完了試行: {}    セット経過: {} 秒    休憩残り: {}\n"
            "{}\n"
            "累積正答率: {}\n\n"
            "回答: {}\n"
            "操作: SPACEで準備段階を進む / ESCで中断"
        ).format(self.stage, self.subject, self.assignment.n_back,
                 self.assignment.session_number, self.assignment.difficulty_level,
                 DIGITS + ZERO_BACK_TARGET if self.assignment.n_back == 0 else DIGITS,
                 N_TRIALS * N_BLOCKS,
                 STIMULUS_SECONDS, ISI_SECONDS, TARGET_PROBABILITY * 100,
                 self.gaze_frequency if self.gaze_frequency is not None else "未確認",
                 self.block, N_BLOCKS, self.trial, N_TRIALS, self.completed, int(elapsed),
                 "-" if self.rest_remaining is None else "{} 秒".format(self.rest_remaining),
                 last_response, accuracy,
                 f"{ZERO_BACK_TARGET}は右矢印 / 数字は左矢印" if self.assignment.n_back == 0
                 else "一致は右矢印 / 不一致は左矢印")
        text = (self.detail or self.stage, body)
        if not force and on_flip is None and text == self.last_text:
            return False
        if text != self.last_text:
            self.banner.text, self.body.text = text
            self.banner.color = "yellow" if "マーカーを入力" in self.detail else "white"
        self.banner.draw()
        self.body.draw()
        if on_flip is not None:
            self.win.callOnFlip(on_flip)
        self.win.flip()
        self.last_update = self.clock.getTime()
        self.last_text = text
        return True


class BlockSyncDisplay:
    """Nonblocking cues on the operator display, sharing the set's clock."""

    def __init__(self, operator, stamp, clock):
        self.operator, self.stamp, self.clock = operator, stamp, clock
        self.countdown = None
        self.cue = None
        self.schedule = []
        active = N_TRIALS * (STIMULUS_SECONDS + ISI_SECONDS)
        for block in range(1, N_BLOCKS + 1):
            start = (block - 1) * (active + REST_SECONDS)
            for phase, boundary in (("start", start), ("end", start + active)):
                if block != 1 or phase != "start":
                    self.schedule.append((boundary - SYNC_COUNTDOWN_SECONDS, block, phase, "countdown", boundary))
                self.schedule.append((boundary, block, phase, "cue", boundary))
                self.schedule.append((boundary + SYNC_CUE_SECONDS, block, phase, "cue_end", boundary))
        self.schedule.sort()

    def detail(self, now):
        if self.cue:
            block, phase = self.cue
            return "ブロック{} {}マーカーを入力".format(block, "開始" if phase == "start" else "終了")
        if self.countdown:
            block, phase, deadline = self.countdown
            return "ブロック{} {}マーカーまで {} 秒".format(
                block, "開始" if phase == "start" else "終了", max(1, int(math.ceil(deadline - now))))
        return self.operator.normal_detail

    def update(self):
        now = self.clock.getTime()
        while self.schedule and self.schedule[0][0] <= now + 1e-7:
            _, block, phase, kind, boundary = self.schedule.pop(0)
            if kind == "countdown":
                self.countdown, self.cue = (block, phase, boundary), None
            elif kind == "cue":
                self.countdown, self.cue = None, (block, phase)
            else:
                self.cue = None
            self.operator.detail = self.detail(now)
            name = "sync_{}_{}".format(phase, kind)
            self.operator.render(force=True, on_flip=lambda name=name, block=block:
                                 self.stamp(name, 0, "", block=block))
            now = self.clock.getTime()
        text = self.detail(now)
        force = self.countdown is not None and text != self.operator.detail
        self.operator.detail = text
        self.operator.render(force=force)

    def prepare(self, win, cross, kb):
        timer = core.Clock()
        self.operator.stage = "開始同期"
        self.operator.detail = "ブロック1 開始マーカーまで {} 秒".format(SYNC_COUNTDOWN_SECONDS)

        def onset():
            timer.reset()
            self.stamp("sync_start_countdown", 0, "", block=1)

        self.operator.render(force=True, on_flip=onset)
        tolerance = (win.monitorFramePeriod or 1 / 60) * .5
        while timer.getTime() < SYNC_COUNTDOWN_SECONDS - tolerance:
            check_escape(kb)
            remaining = max(1, int(math.ceil(SYNC_COUNTDOWN_SECONDS - timer.getTime())))
            detail = "ブロック1 開始マーカーまで {} 秒".format(remaining)
            if detail != self.operator.detail:
                self.operator.detail = detail
                self.operator.render(force=True)
            cross.draw()
            win.flip()


def run_nback_blocks(win, cross, digit, kb, operator, designs, trials, protocol,
                     stamp, poll_keys, checkpoint):
    """Use absolute set deadlines so rest/cues do not accumulate extra time."""
    clock = core.Clock()
    sync = BlockSyncDisplay(operator, stamp, clock)
    sync.prepare(win, cross, kb)
    active = N_TRIALS * (STIMULUS_SECONDS + ISI_SECONDS)
    block_duration = active + REST_SECONDS
    tolerance = (win.monitorFramePeriod or 1 / 60) * .5

    def wait_until(deadline, stimulus, record=None):
        while clock.getTime() < deadline - tolerance:
            if record is None:
                check_escape(kb)
            else:
                poll_keys(kb, record)
            if operator.stage == "休憩":
                operator.rest_remaining = max(0, int(math.ceil(deadline - clock.getTime())))
            stimulus.draw()
            win.flip()
            sync.update()
        if record is not None:
            poll_keys(kb, record)
        else:
            check_escape(kb)

    def finish_trial(record):
        stamp("response_window_end", record["trial"], record["stimulus"],
              record, "response_window_end_us")
        record["correct"] = int(record["response_key"] == ("right" if record["is_target"] else "left"))
        record["completed"] = 1
        operator.complete_trial(record)

    def finish_rest(summary):
        summary["rest_end_us"] = stamp("rest_end", 0, "", block=summary["block"])
        summary["completed"] = True
        protocol["completed_blocks"] += 1

    previous_summary = None
    for block, design in enumerate(designs, 1):
        start = (block - 1) * block_duration
        summary = {"block": block, "planned_task_start_seconds": start,
                   "task_start_us": None, "task_end_us": None, "rest_start_us": None,
                   "rest_end_us": None, "completed": False, "frame_timing": None}
        protocol["blocks"].append(summary)
        operator.block, operator.trial = block, 0
        operator.stage, operator.rest_remaining = "課題実行中", None
        win.frameIntervals, win.nDroppedFrames = [], 0
        win.recordFrameIntervals = True
        previous_record = None
        for local_trial, plan in enumerate(design, 1):
            onset = start + (local_trial - 1) * (STIMULUS_SECONDS + ISI_SECONDS)
            record = dict(plan)
            record.update(trial=(block - 1) * N_TRIALS + local_trial, block=block,
                          trial_in_block=local_trial, response_key="", correct="", rt_seconds="",
                          stim_on_us="", stim_off_us="", response_detected_us="",
                          response_window_end_us="", planned_onset_seconds=onset,
                          onset_error_seconds="", completed=0)
            trials.append(record)
            if previous_record is not None:
                win.callOnFlip(finish_trial, previous_record)
            if local_trial == 1:
                if block == 1:
                    win.callOnFlip(clock.reset)
                    win.callOnFlip(operator.start_task)
                    win.callOnFlip(lambda: protocol.update(set_start_us=stamp("set_start", 0, "")))
                else:
                    win.callOnFlip(finish_rest, previous_summary)
                win.callOnFlip(lambda summary=summary, block=block, start=start: summary.update(
                    task_start_us=stamp("task_start", 0, "", block=block),
                    onset_error_seconds=clock.getTime() - start))
            operator.trial = local_trial
            operator.normal_detail = "数字を提示中"
            digit.text = record["stimulus"]
            digit.draw()
            win.callOnFlip(kb.clock.reset)
            win.callOnFlip(kb.clearEvents)
            win.callOnFlip(stamp, "stim_on", record["trial"], record["stimulus"], record, "stim_on_us")
            win.callOnFlip(lambda record=record, onset=onset: record.update(onset_error_seconds=clock.getTime() - onset))
            win.flip()
            sync.update()
            if local_trial == 1 and block > 1:
                checkpoint()
            wait_until(onset + STIMULUS_SECONDS, digit, record)
            operator.normal_detail = "注視点・回答受付中"
            cross.draw()
            win.callOnFlip(stamp, "stim_off", record["trial"], record["stimulus"], record, "stim_off_us")
            win.flip()
            sync.update()
            wait_until(onset + STIMULUS_SECONDS + ISI_SECONDS, cross, record)
            previous_record = record

        operator.stage, operator.normal_detail = "休憩", "休憩してください"
        operator.rest_remaining = int(math.ceil(REST_SECONDS))
        cross.draw()
        win.callOnFlip(finish_trial, previous_record)
        win.callOnFlip(lambda summary=summary, block=block: summary.update(
            task_end_us=stamp("task_end", 0, "", block=block),
            rest_start_us=stamp("rest_start", 0, "", block=block)))
        win.flip()
        win.recordFrameIntervals = False
        summary["frame_timing"] = frame_timing_summary(win)
        sync.update()
        checkpoint()
        wait_until(start + block_duration, cross)
        previous_summary = summary
    cross.draw()
    win.callOnFlip(finish_rest, previous_summary)
    win.callOnFlip(lambda: protocol.update(set_end_us=stamp("set_end", 0, "")))
    win.callOnFlip(operator.finish_task)
    win.flip()
    operator.stage = "記録終了"
    operator.rest_remaining = 0
    operator.normal_detail = "Raw TLXを準備しています"
    sync.update()
    operator.render(force=True)
    checkpoint()


def frame_timing_summary(win):
    intervals = list(win.frameIntervals)
    return {
        "recorded_intervals": len(intervals),
        "mean_interval_seconds": sum(intervals) / len(intervals) if intervals else None,
        "max_interval_seconds": max(intervals) if intervals else None,
        "dropped_frames": int(win.nDroppedFrames),
        "refresh_threshold_seconds": float(win.refreshThreshold),
    }


def select_tracker():
    found = list(tr.find_all_eyetrackers())
    if TOBII_SERIAL:
        found = [t for t in found if t.serial_number == TOBII_SERIAL]
    if not found:
        raise RuntimeError("Tobii not found: check connection, Tobii software and network")
    if len(found) > 1:
        raise RuntimeError("Several trackers found: set TOBII_SERIAL at top of script")
    return found[0]


def calibrate_tracker(tracker, win, progress=None, kb=None):
    calibration = tr.ScreenBasedCalibration(tracker)
    points = [(0.5, 0.5), (0.12, 0.12), (0.88, 0.12),
              (0.88, 0.88), (0.12, 0.88)]
    dot = visual.Circle(win, radius=9, units="pix", fillColor="red",
                        lineColor="white", lineWidth=2)
    entered = False
    try:
        if progress: progress("CALIBRATION: enter SDK calibration mode")
        calibration.enter_calibration_mode()
        entered = True
        w, h = win.size
        for x, y in points:
            dot.pos = ((x - .5) * w, (.5 - y) * h)
            dot.draw()
            win.flip()
            timer = core.Clock()
            while timer.getTime() < .85:
                if kb is not None:
                    check_escape(kb)
                core.wait(.01)
            if progress: progress("CALIBRATION: collect point {} {}".format(x, y))
            if kb is not None:
                check_escape(kb)
            result = calibration.collect_data(x, y)
            if result != tr.CALIBRATION_STATUS_SUCCESS:
                if progress: progress("CALIBRATION: retry point {} {}".format(x, y))
                result = calibration.collect_data(x, y)
            if result != tr.CALIBRATION_STATUS_SUCCESS:
                raise RuntimeError(f"Calibration point {(x, y)} failed: {result}")
        if progress: progress("CALIBRATION: compute/apply")
        result = calibration.compute_and_apply()
        if result.status != tr.CALIBRATION_STATUS_SUCCESS:
            raise RuntimeError(f"Calibration not applied: {result.status}")
        return {"status": str(result.status), "points": points}
    finally:
        if entered:
            if progress: progress("CALIBRATION: leave calibration mode")
            calibration.leave_calibration_mode()
            if progress: progress("CALIBRATION: left calibration mode")
        win.flip()


def json_safe(value):
    """Convert tuples, SDK metadata and non-finite floats into JSON-safe types."""
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def list_components(value, n):
    if isinstance(value, (list, tuple)):
        return [value[i] if len(value) > i else "" for i in range(n)]
    if n == 1:
        return [value if value is not None else ""]
    return [""] * n


def gaze_csv_row(d):
    row = [d.get("device_time_stamp", ""), d.get("system_time_stamp", "")]
    for side in ("left", "right"):
        for sdk_field, comps in EYE_FIELDS:
            value = d.get(f"{side}_{sdk_field}")
            row.extend(list_components(value, len(comps)))
    return row


class StreamRecorder:
    """Nonblocking Tobii callbacks; dedicated disk writer; bounded queue."""

    def __init__(self, base):
        self.base = base
        self.items = queue.Queue(maxsize=QUEUE_CAPACITY)
        self.stats_lock = threading.Lock()
        self.dropped = Counter()
        self.written = Counter()
        self.first_device_ts = {}
        self.last_device_ts = {}
        self.large_device_gaps = Counter()
        self.time_backwards_or_equal = Counter()
        self.writer_error = None
        self.subscriptions = []
        self.attempts = {}
        self.thread = threading.Thread(target=self._write, name="TobiiDiskWriter", daemon=True)
        self.thread.start()

    def _callback(self, kind, name):
        def cb(data):
            # The SDK calls this on its own threads: do not block on disk or UI.
            try:
                self.items.put_nowait((kind, name, data))
            except queue.Full:
                with self.stats_lock:
                    self.dropped[kind] += 1
        return cb

    def subscribe(self, tracker, kind, const_name, required=False):
        stream_type = getattr(tr, const_name, None)
        if stream_type is None:
            self.attempts[const_name] = "SDK constant not available"
            if required:
                raise RuntimeError(f"SDK missing {const_name}")
            return
        callback = self._callback(kind, const_name)
        try:
            tracker.subscribe_to(stream_type, callback, as_dictionary=True)
            self.subscriptions.append((stream_type, callback))
            self.attempts[const_name] = "subscribed"
            print("Subscribed:", const_name)
        except Exception as exc:
            self.attempts[const_name] = f"unavailable: {type(exc).__name__}: {exc}"
            print("Skipped:", const_name, exc)
            if required:
                raise

    def unsubscribe_all(self, tracker):
        for stream_type, callback in reversed(self.subscriptions):
            try:
                tracker.unsubscribe_from(stream_type, callback)
            except Exception as exc:
                print("Unsubscribe warning:", stream_type, exc)
        self.subscriptions.clear()

    def close(self):
        # Only call after unsubscribe_all(), so no new SDK callbacks arrive.
        self.items.put(None)
        self.thread.join()
        if self.writer_error:
            print("DISK WRITER ERROR:", self.writer_error)

    def _write(self):
        failed = False
        with ExitStack() as stack:
            try:
                # Buffer disk I/O; do not mirror high-rate gaze samples to JSON by default.
                gaze_file = stack.enter_context(open(
                    f"{self.base}_gaze.csv", "w", newline="", encoding="utf-8", buffering=1024 * 1024))
                gaze = csv.writer(gaze_file)
                gaze.writerow(GAZE_COLUMNS)
                json_files = {}
                json_kinds = []
                if SAVE_GAZE_RAW_JSONL:
                    json_kinds.append("gaze_raw")
                if RECORD_EYE_OPENNESS:
                    json_kinds.append("eye_openness")
                if RECORD_OTHER_STREAMS:
                    json_kinds.extend(("time_sync", "external_signal", "user_position",
                                       "notifications", "stream_errors"))
                if RECORD_EYE_IMAGES:
                    json_kinds.append("eye_images_index")
                for kind in json_kinds:
                    json_files[kind] = stack.enter_context(open(
                        f"{self.base}_{kind}.jsonl", "w", encoding="utf-8", buffering=1024 * 1024))
                image_bin = stack.enter_context(open(
                    f"{self.base}_eye_images.bin", "wb", buffering=1024 * 1024)) if RECORD_EYE_IMAGES else None
            except Exception as exc:
                self.writer_error = f"Opening output files: {type(exc).__name__}: {exc}"
                failed = True

            processed = 0
            while True:
                item = self.items.get()
                if item is None:
                    break
                kind, name, data = item
                if failed:
                    continue  # Keep draining queue so shutdown is deterministic.
                try:
                    if not isinstance(data, dict):
                        data = {"value": str(data)}
                    if kind in ("gaze", "eye_openness"):
                        ts = data.get("device_time_stamp", None)
                        if isinstance(ts, (int, float)) and ts >= 0:
                            if kind not in self.first_device_ts:
                                self.first_device_ts[kind] = ts
                            prev = self.last_device_ts.get(kind)
                            if prev is not None:
                                if ts <= prev:
                                    self.time_backwards_or_equal[kind] += 1
                                elif ts - prev > 5000: # >5 ms indicates potential gaps at 1200 Hz
                                    self.large_device_gaps[kind] += 1
                            self.last_device_ts[kind] = ts
                    if kind == "gaze":
                        gaze.writerow(gaze_csv_row(data))
                        if SAVE_GAZE_RAW_JSONL:
                            json_files["gaze_raw"].write(json.dumps(
                                json_safe(data), ensure_ascii=False, separators=(",", ":")) + "\n")
                    elif kind == "eye_images":
                        raw = data.get("image_data", b"") or b""
                        raw = bytes(raw)
                        offset = image_bin.tell()
                        image_bin.write(raw)
                        meta = {k: v for k, v in data.items() if k != "image_data"}
                        meta.update({"sdk_stream": name, "byte_offset": offset,
                                     "byte_length": len(raw)})
                        json_files["eye_images_index"].write(json.dumps(
                            json_safe(meta), ensure_ascii=False, separators=(",", ":")) + "\n")
                    else:
                        # Store every SDK key. For notifications also save subscription name.
                        record = {"sdk_stream": name, **data}
                        json_files[kind].write(json.dumps(
                            json_safe(record), ensure_ascii=False, separators=(",", ":")) + "\n")
                    self.written[kind] += 1
                    processed += 1
                    if processed % 1000 == 0:
                        gaze_file.flush()
                        for f in json_files.values():
                            f.flush()
                        if image_bin is not None:
                            image_bin.flush()
                except Exception as exc:
                    self.writer_error = f"Writing {kind}: {type(exc).__name__}: {exc}"
                    failed = True


def save_csv(path, columns, rows):
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        writer.writerows(rows)


def tracker_info(tracker):
    def safe(fn):
        try:
            return json_safe(fn())
        except Exception as exc:
            return f"unavailable: {type(exc).__name__}: {exc}"
    display_area = safe(tracker.get_display_area)
    if not isinstance(display_area, str):
        display_area = json_safe({key: getattr(tracker.get_display_area(), key, None)
                                  for key in ("top_left", "top_right", "bottom_left",
                                              "bottom_right", "width", "height")})
    return {
        "model": getattr(tracker, "model", None),
        "serial": getattr(tracker, "serial_number", None),
        "address": getattr(tracker, "address", None),
        "firmware_version": getattr(tracker, "firmware_version", None),
        "device_capabilities": json_safe(getattr(tracker, "device_capabilities", [])),
        "supported_gaze_frequencies_hz": safe(tracker.get_all_gaze_output_frequencies),
        "gaze_frequency_hz": safe(tracker.get_gaze_output_frequency),
        "eye_tracking_mode": safe(tracker.get_eye_tracking_mode),
        "display_area": display_area,
    }


def main(participant_id=None, session_number=None):
    if participant_id is None and session_number is None:
        assignment = prompt_nback_assignment(COUNTERBALANCE_CSV)
        if assignment is None:
            return
    elif participant_id is None or session_number is None:
        raise ValueError("Specify both participant_id and session_number")
    else:
        assignment = load_nback_assignment(COUNTERBALANCE_CSV, participant_id, session_number)
    subject, n_back = assignment.participant_id, assignment.n_back
    if not (0 <= n_back and N_TRIALS > n_back and 0 <= TARGET_PROBABILITY <= 1):
        raise ValueError("Check assigned N value, N_TRIALS, TARGET_PROBABILITY")
    if QUEUE_CAPACITY < 2:
        raise ValueError("QUEUE_CAPACITY must be >= 2")
    if type(SYNC_COUNTDOWN_SECONDS) is not int or SYNC_COUNTDOWN_SECONDS < 1:
        raise ValueError("SYNC_COUNTDOWN_SECONDS must be a positive integer")
    if not all(math.isfinite(value) and value > 0
               for value in (SYNC_CUE_SECONDS, OPERATOR_REFRESH_HZ, REST_SECONDS,
                             STIMULUS_SECONDS, ISI_SECONDS)):
        raise ValueError("Timing and refresh settings must be finite and positive")
    if type(N_BLOCKS) is not int or N_BLOCKS < 1 or type(N_TRIALS) is not int:
        raise ValueError("N_BLOCKS and N_TRIALS must be positive integers")
    active_seconds = N_TRIALS * (STIMULUS_SECONDS + ISI_SECONDS)
    if min(active_seconds, REST_SECONDS) < SYNC_COUNTDOWN_SECONDS + SYNC_CUE_SECONDS:
        raise ValueError("Task and rest must accommodate the countdown and marker cue")
    validate_screen_configuration()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    base = OUTPUT_DIR / (f"{subject}_nback_s{assignment.session_number:02d}"
                         f"_d{assignment.difficulty_level:02d}_n{n_back:02d}_"
                         + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    rng_seed = SEED if SEED is not None else random.SystemRandom().randrange(2 ** 32)
    seed_rng = random.Random(rng_seed)
    block_seeds = [seed_rng.randrange(2 ** 32) for _ in range(N_BLOCKS)]
    designs = [make_design(n_back, N_TRIALS, seed) for seed in block_seeds]
    recorder = StreamRecorder(base)
    events, trials = [], []
    tracker, win, operator_win, operator = None, None, None, None
    status, err = "failed", None
    cal_result, device_info, size, operator_size = None, {}, None, None
    participant_frame_timing = None
    cleanup_errors = []
    protocol = {"blocks": [], "completed_blocks": 0, "set_start_us": None, "set_end_us": None}
    tlx = RawTLXSession(Path(f"{base}_tlx.csv"), subject, n_back, N_BLOCKS,
                        session_number=assignment.session_number,
                        difficulty_level=assignment.difficulty_level)
    recorder_closed, unsubscribed, gaze_started = False, False, False

    def checkpoint():
        save_csv(Path(f"{base}_events.csv"), EVENT_COLUMNS, events)
        save_csv(Path(f"{base}_trials.csv"), TRIAL_COLUMNS,
                 [[row.get(key, "") for key in TRIAL_COLUMNS] for row in trials])

    def progress(message):
        # The last line helps locate a true block in calibration/SDK startup.
        line = "{} | {}".format(datetime.now().isoformat(timespec="seconds"), message)
        print(line, flush=True)
        with open(f"{base}_progress.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def wait_for_space(kb, text, stage, timeout=START_KEY_TIMEOUT_SECONDS):
        # Unlike waitKeys(default waitRelease=True), this responds on KEY-DOWN.
        kb.clearEvents()
        text.draw()
        win.flip()
        operator.stage = "校正の準備" if stage == "BEFORE_CALIBRATION" else "課題の準備"
        operator.detail = "準備ができたらSPACEを押してください"
        if stage == "AFTER_TLX":
            operator.stage = "完了"
            operator.detail = "Raw TLX {:.1f} 保存済み。SPACEで終了".format(tlx.score)
        operator.render(force=True)
        progress(stage + ": SPACE to continue; ESC to quit (click task window if needed)")
        timer = core.Clock()
        while timeout is None or timer.getTime() < timeout:
            keys = kb.getKeys(keyList=["space", "escape"], waitRelease=False, clear=True)
            if any(key.name == "escape" for key in keys):
                raise ExperimentAborted("ESC at " + stage)
            for key in keys:
                if key.name == "space":
                    progress(stage + ": SPACE detected")
                    return
            core.wait(0.01)
        raise RuntimeError(stage + ": no SPACE key received within timeout; check PsychoPy window focus")

    def stamp(event_name, trial_no, stimulus, record=None, field=None, block=None):
        us = tr.get_system_time_stamp()
        if block is None:
            block = record.get("block", 0) if record is not None else 0
        events.append([event_name, trial_no, stimulus, us, block])
        if record is not None and field is not None:
            record[field] = us
        return us

    def poll_keys(kb, record):
        for key in kb.getKeys(keyList=["left", "right", "escape"], waitRelease=False, clear=True):
            if key.name == "escape":
                raise ExperimentAborted("ESC pressed")
            # Keep the first arrow-key response for this trial.
            if key.name in ("left", "right") and not record["response_key"]:
                record["response_key"] = key.name
                record["rt_seconds"] = round(float(key.rt), 6)
                stamp("response_detected", record["trial"], record["stimulus"],
                      record, "response_detected_us")

    def calibration_progress(message):
        progress(message)
        operator.stage = "キャリブレーション"
        operator.detail = message
        operator.render(force=True)

    def cleanup_step(label, action):
        try:
            action()
        except Exception as exc:
            cleanup_errors.append("{}: {}: {}".format(label, type(exc).__name__, exc))

    def unsubscribe():
        nonlocal unsubscribed
        if tracker is not None and not unsubscribed:
            recorder.unsubscribe_all(tracker)
            unsubscribed = True
            if gaze_started:
                stamp("recording_stop", 0, "")

    def close_recorder():
        nonlocal recorder_closed
        if not recorder_closed:
            recorder.close()
            recorder_closed = True

    try:
        # Create the participant window last, so initial keyboard focus is there.
        operator_win = visual.Window(
            size=OPERATOR_WINDOW_SIZE, fullscr=False, screen=OPERATOR_SCREEN_INDEX,
            units="pix", color="black", waitBlanking=False, checkTiming=False,
            allowGUI=True, winType="pyglet", title="N-back Operator", autoLog=False)
        operator_size = [int(x) for x in operator_win.size]
        win = visual.Window(fullscr=True, screen=SCREEN_INDEX, units="pix",
                            color="black", waitBlanking=True, allowGUI=False,
                            winType="pyglet")
        size = [int(x) for x in win.size]
        kb = keyboard.Keyboard()
        operator = OperatorDisplay(operator_win, assignment)
        operator.detail = "Tobii接続を確認しています"
        operator.render(force=True)
        message = visual.TextStim(win, color="white", height=30, wrapWidth=900)
        digit = visual.TextStim(win, color="white", height=86)
        cross = visual.TextStim(win, text="+", color="white", height=48)
        cross.draw()
        win.flip()
        check_escape(kb)
        progress("Detecting Tobii tracker")
        tracker = select_tracker()
        if TOBII_FREQUENCY_HZ is not None:
            allowed = tracker.get_all_gaze_output_frequencies()
            if not any(abs(float(n) - TOBII_FREQUENCY_HZ) < 0.01 for n in allowed):
                raise ValueError(f"Unsupported gaze frequency {TOBII_FREQUENCY_HZ}. Available: {allowed}")
            tracker.set_gaze_output_frequency(TOBII_FREQUENCY_HZ)
        device_info = tracker_info(tracker)
        obtained = device_info.get("gaze_frequency_hz")
        if not isinstance(obtained, (int, float)) or abs(float(obtained) - 1200.0) >= 0.01:
            raise RuntimeError("Device was not confirmed at 1200 Hz: {!r}".format(obtained))
        operator.gaze_frequency = obtained
        print("PsychoPy", psychopy.__version__, "| Python", sys.version.split()[0])
        print("Tobii:", device_info["model"], "| frequency:", device_info["gaze_frequency_hz"])

        if not SKIP_CALIBRATION:
            message.text = "Look at each red dot for calibration.\nSPACE: continue    ESC: exit"
            wait_for_space(kb, message, "BEFORE_CALIBRATION")
            progress("CALIBRATION started")
            cal_result = calibrate_tracker(tracker, win, progress=calibration_progress, kb=kb)
            progress("CALIBRATION finished")
        else:
            cal_result = {"status": "skipped (existing calibration assumed)"}
            progress("CALIBRATION skipped")

        # If an SDK request blocks immediately after calibration, logs identify it.
        if SAVE_CALIBRATION_DATA:
            check_escape(kb)
            operator.detail = "キャリブレーション情報を保存しています"
            operator.render(force=True)
            progress("CALIBRATION_DATA retrieval started")
            try:
                cal_bytes = tracker.retrieve_calibration_data()
                if cal_bytes:
                    Path(f"{base}_calibration.bin").write_bytes(cal_bytes)
                    cal_result["saved_binary_bytes"] = len(cal_bytes)
            except Exception as exc:
                cal_result["save_error"] = str(exc)
            progress("CALIBRATION_DATA retrieval finished")
            check_escape(kb)

        message.text = participant_instructions(assignment)
        wait_for_space(kb, message, "AFTER_CALIBRATION_WAIT_FOR_START")
        kb.clearEvents()
        cross.draw()
        win.flip()
        operator.stage = "記録の準備"
        operator.detail = "計測データの記録を開始しています"
        operator.render(force=True)
        progress("SUBSCRIBE gaze started")
        recorder.subscribe(tracker, "gaze", "EYETRACKER_GAZE_DATA", required=True)
        gaze_started = True
        progress("SUBSCRIBE gaze finished")
        if RECORD_EYE_OPENNESS:
            recorder.subscribe(tracker, "eye_openness", "EYETRACKER_EYE_OPENNESS_DATA")
        if RECORD_OTHER_STREAMS:
            for kind, constant in OPTIONAL_STREAMS:
                if kind == "eye_openness":
                    continue
                recorder.subscribe(tracker, kind, constant)
            if RECORD_USER_POSITION_DURING_TASK:
                recorder.subscribe(tracker, "user_position", "EYETRACKER_USER_POSITION_GUIDE")
            for constant in NOTIFICATIONS:
                recorder.subscribe(tracker, "notifications", constant)
        if RECORD_EYE_IMAGES:
            # Spectrum SDK eye images are low-rate (~10 Hz), not 1200-Hz raw camera video.
            recorder.subscribe(tracker, "eye_images", "EYETRACKER_EYE_IMAGES")

        run_nback_blocks(win, cross, digit, kb, operator, designs, trials, protocol,
                         stamp, poll_keys, checkpoint)
        unsubscribe()
        close_recorder()
        checkpoint()
        if recorder.writer_error:
            raise RuntimeError("Recording failed: " + recorder.writer_error)
        kb.clearEvents()
        run_raw_tlx(win, operator, tlx,
                    lambda name, detail: stamp(name, 0, detail), lambda: check_escape(kb))
        message.font = "Yu Gothic"
        message.text = "回答を保存しました。\nご協力ありがとうございました。"
        wait_for_space(kb, message, "AFTER_TLX", timeout=None)
        status = "completed"

    except ExperimentAborted as exc:
        status, err = "aborted", str(exc)
        stamp("experiment_aborted", 0, "")
        print(err)
    except Exception as exc:
        status, err = "failed", f"{type(exc).__name__}: {exc}"
        print("EXPERIMENT ERROR:", err)
        raise
    finally:
        for record in trials:
            if record["stim_on_us"] != "" and record["response_window_end_us"] == "":
                stamp("response_window_end", record["trial"], record["stimulus"],
                      record, "response_window_end_us")
        cleanup_step("TLX partial save", tlx.abort)
        if operator is not None:
            operator.finish_task()
        if win is not None:
            win.recordFrameIntervals = False
            if protocol["blocks"] and protocol["blocks"][-1]["frame_timing"] is None:
                try:
                    protocol["blocks"][-1]["frame_timing"] = frame_timing_summary(win)
                except Exception as exc:
                    cleanup_errors.append("frame timing: {}".format(exc))
        frames = [block["frame_timing"] for block in protocol["blocks"] if block["frame_timing"]]
        if frames:
            count = sum(frame["recorded_intervals"] for frame in frames)
            participant_frame_timing = {
                "recorded_intervals": count,
                "mean_interval_seconds": sum((frame["mean_interval_seconds"] or 0) * frame["recorded_intervals"]
                                             for frame in frames) / count if count else None,
                "max_interval_seconds": max((frame["max_interval_seconds"] or 0) for frame in frames),
                "dropped_frames": sum(frame["dropped_frames"] for frame in frames),
                "refresh_threshold_seconds": frames[0]["refresh_threshold_seconds"],
            }
        if tracker is not None:
            cleanup_step("cleanup log", lambda: progress("CLEANUP: unsubscribing"))
            cleanup_step("unsubscribe", unsubscribe)
        cleanup_step("recorder close", close_recorder)
        for window in (win, operator_win):
            if window is not None:
                cleanup_step("window close", window.close)
        if cleanup_errors:
            err = "{}; {}".format(err or "", "; ".join(cleanup_errors))
            status = "failed"
        checkpoint()
        if recorder.writer_error:
            err = f"{err or ''}; Disk writer: {recorder.writer_error}"
            status = "failed"
        sampling_stats = {}
        for kind in ("gaze", "eye_openness"):
            first = recorder.first_device_ts.get(kind)
            last = recorder.last_device_ts.get(kind)
            count = recorder.written.get(kind, 0)
            duration = (last - first) / 1e6 if first is not None and last is not None else 0
            sampling_stats[kind] = {
                "recorded_samples": count,
                "device_timestamp_span_seconds": round(duration, 6),
                "apparent_rate_hz": round((count - 1) / duration, 2) if duration > 0 else None,
                "gaps_over_5ms": recorder.large_device_gaps.get(kind, 0),
                "non_increasing_timestamps": recorder.time_backwards_or_equal.get(kind, 0),
            }
        metadata = {
            "participant": subject, "status": status, "error": err,
            "psychopy_version": getattr(psychopy, "__version__", None),
            "python_version": sys.version, "n_back": n_back,
            "task": "nback", "session_number": assignment.session_number,
            "difficulty_level": assignment.difficulty_level,
            "counterbalance_csv": str(COUNTERBALANCE_CSV),
            "counterbalance_column": assignment.column,
            "difficulty_to_nback": NBACK_BY_DIFFICULTY,
            "requested_trials": N_TRIALS * N_BLOCKS,
            "completed_trials": sum(row["completed"] for row in trials),
            "trials_per_block": N_TRIALS, "requested_blocks": N_BLOCKS,
            "completed_blocks": protocol["completed_blocks"],
            "rest_seconds": REST_SECONDS,
            "planned_set_seconds": N_BLOCKS * (active_seconds + REST_SECONDS),
            "actual_set_seconds": ((protocol["set_end_us"] - protocol["set_start_us"]) / 1e6
                                   if protocol["set_end_us"] is not None else None),
            "block_seeds": block_seeds, "blocks": protocol["blocks"],
            "tlx_status": tlx.status, "raw_tlx": tlx.score,
            "stimulus_seconds": STIMULUS_SECONDS, "isi_seconds": ISI_SECONDS,
            "target_probability": TARGET_PROBABILITY, "seed": rng_seed,
            "stimulus_digits": DIGITS,
            "stimulus_symbols": DIGITS + ZERO_BACK_TARGET if n_back == 0 else DIGITS,
            "zero_back_target": ZERO_BACK_TARGET if n_back == 0 else None,
            "response_keys": {"target": "right", "non_target": "left"},
            "screen_index": SCREEN_INDEX, "screen_pixels": size,
            "participant_wait_blanking": True,
            "operator_screen_index": OPERATOR_SCREEN_INDEX,
            "operator_screen_pixels": operator_size,
            "operator_fullscreen": False,
            "operator_wait_blanking": False,
            "operator_check_timing": False,
            "window_backend": "pyglet",
            "operator_refresh_hz": OPERATOR_REFRESH_HZ,
            "sync_countdown_seconds": SYNC_COUNTDOWN_SECONDS,
            "sync_cue_seconds": SYNC_CUE_SECONDS,
            "participant_frame_timing": participant_frame_timing,
            "calibration": cal_result, "tracker": device_info,
            "subscriptions": recorder.attempts,
            "saved_record_counts": dict(recorder.written),
            "dropped_due_to_full_queue": dict(recorder.dropped),
            "writer_error": recorder.writer_error,
            "eye_images_enabled": RECORD_EYE_IMAGES,
            "eye_openness_enabled": RECORD_EYE_OPENNESS,
            "raw_gaze_json_enabled": SAVE_GAZE_RAW_JSONL,
            "calibration_binary_save_enabled": SAVE_CALIBRATION_DATA,
            "sampling_quality": sampling_stats,
            "user_position_during_task_enabled": RECORD_USER_POSITION_DURING_TASK,
            "optional_streams_enabled": RECORD_OTHER_STREAMS,
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "timestamp_note": "Tobii SDK system_time_stamp microseconds. PsychoPy callOnFlip "
                              "marks display-flip callback; not photodiode-confirmed photon onset. "
                              "Sync events use the operator window (waitBlanking=False) and do "
                              "not measure the actual manual marker input on external devices. "
                              "Each block has task_start/task_end around its active trials and "
                              "rest_start/rest_end around its rest. Countdown/cues run concurrently. "
                              "set_start/set_end include six active/rest blocks; TLX is recorded separately.",
        }
        Path(f"{base}_metadata.json").write_text(
            json.dumps(json_safe(metadata), ensure_ascii=False, indent=2), encoding="utf-8")
        progress("FINISHED: status=" + status)
        print("Experiment:", status)
        print("Samples saved:", dict(recorder.written))
        print("Queue drops:", dict(recorder.dropped))
        print("1200-Hz QA (device timestamps):", sampling_stats)
        print("Output prefix:", str(base))


if __name__ == "__main__":
    main()
