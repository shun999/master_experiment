# -*- coding: utf-8 -*-
"""Lightweight N-back, Tobii Pro Spectrum 1200 Hz, PsychoPy 2022.2.4 Coder.

Starting settings record full SDK gaze quantities in ONE CSV (incl. both pupils,
2D/3D gaze, gaze origins, validities, timestamps) at 1200 Hz. Eye images and
other SDK streams can be enabled separately once baseline runs correctly.

This example has NOT been validated on real Spectrum hardware.
Compatible Python/Tobii SDK versions depend on the exact Standalone install.
"""

# ========================= EDIT THESE SETTINGS ===========================
PARTICIPANT_ID = "P001"       # Use a pseudonym; do not put participants' names here
N_BACK = 1
N_TRIALS = 10
STIMULUS_SECONDS = 0.50
ISI_SECONDS = 1.50
TARGET_PROBABILITY = 0.30
DIGITS = "0123456789"
TOBII_FREQUENCY_HZ = 1200     # Fail fast if this device cannot actually offer 1200 Hz
TOBII_SERIAL = None           # Set if more than one tracker is on the network
SCREEN_INDEX = 1             # Participant monitor (extended desktop)
OPERATOR_SCREEN_INDEX = 0    # Separate operator monitor
OPERATOR_REFRESH_HZ = 5.0    # Maximum dashboard refresh rate during the task
SYNC_COUNTDOWN_SECONDS = 10  # Countdown before each manual marker cue
SYNC_CUE_SECONDS = 1.0       # Fixation continues while the marker cue is visible
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
import re
import sys
import threading
import time

from psychopy import core, visual
from psychopy.hardware import keyboard
import psychopy
import tobii_research as tr

OUTPUT_DIR = Path(__file__).resolve().parent / "nback_data"

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

EVENT_COLUMNS = ["event", "trial", "stimulus", "system_time_stamp_us"]
TRIAL_COLUMNS = [
    "trial", "stimulus", "is_target", "response_key", "correct",
    "rt_seconds", "stim_on_us", "stim_off_us", "response_detected_us", "completed",
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

    def __init__(self, win, subject):
        self.win = win
        self.subject = subject
        self.gaze_frequency = None
        self.stage = "準備中"
        self.detail = ""
        self.trial = 0
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
            "数字集合: {}    総試行数: {}\n"
            "刺激: {:.2f} 秒    ISI: {:.2f} 秒\n"
            "ターゲット確率: {:.0f}%    取得周波数: {} Hz\n\n"
            "現在の試行: {}/{}    完了: {}\n"
            "課題経過時間: {} 秒\n"
            "{}\n"
            "累積正答率: {}\n\n"
            "回答: 一致は右矢印 / 不一致は左矢印\n"
            "操作: SPACEで準備段階を進む / ESCで中断"
        ).format(self.stage, self.subject, N_BACK, DIGITS, N_TRIALS,
                 STIMULUS_SECONDS, ISI_SECONDS, TARGET_PROBABILITY * 100,
                 self.gaze_frequency if self.gaze_frequency is not None else "未確認",
                 self.trial, N_TRIALS, self.completed, int(elapsed), last_response, accuracy)
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


def run_sync_phase(win, cross, kb, operator, stamp, phase):
    """Keep participant fixation visible throughout countdown and manual cue."""
    label = "開始" if phase == "start" else "終了"
    prefix = "sync_" + phase
    operator.stage = label + "同期"
    operator.detail = "{}マーカーまで {} 秒".format(label, SYNC_COUNTDOWN_SECONDS)
    cross.draw()
    win.flip()
    check_escape(kb)
    timer = core.Clock()

    def countdown_onset():
        timer.reset()
        stamp(prefix + "_countdown", 0, "")

    operator.render(force=True, on_flip=countdown_onset)
    last_remaining = SYNC_COUNTDOWN_SECONDS
    while timer.getTime() < SYNC_COUNTDOWN_SECONDS:
        check_escape(kb)
        remaining = max(1, int(math.ceil(SYNC_COUNTDOWN_SECONDS - timer.getTime())))
        if remaining != last_remaining:
            operator.detail = "{}マーカーまで {} 秒".format(label, remaining)
            operator.render(force=True)
            last_remaining = remaining
        cross.draw()
        win.flip()

    check_escape(kb)
    operator.detail = "{}マーカーを入力".format(label)

    def cue_onset():
        timer.reset()
        stamp(prefix + "_cue", 0, "")

    operator.render(force=True, on_flip=cue_onset)
    while timer.getTime() < SYNC_CUE_SECONDS:
        check_escape(kb)
        cross.draw()
        win.flip()
    check_escape(kb)
    operator.stage = "課題実行中" if phase == "start" else "記録終了"
    operator.detail = "課題を開始します" if phase == "start" else "記録を保存します"
    operator.render(force=True, on_flip=lambda: stamp(prefix + "_cue_end", 0, ""))


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


def main():
    if not (N_BACK >= 1 and N_TRIALS > N_BACK and 0 <= TARGET_PROBABILITY <= 1):
        raise ValueError("Check N_BACK, N_TRIALS, TARGET_PROBABILITY")
    if QUEUE_CAPACITY < 2:
        raise ValueError("QUEUE_CAPACITY must be >= 2")
    if type(SYNC_COUNTDOWN_SECONDS) is not int or SYNC_COUNTDOWN_SECONDS < 1:
        raise ValueError("SYNC_COUNTDOWN_SECONDS must be a positive integer")
    if not all(math.isfinite(value) and value > 0
               for value in (SYNC_CUE_SECONDS, OPERATOR_REFRESH_HZ)):
        raise ValueError("SYNC_CUE_SECONDS and OPERATOR_REFRESH_HZ must be finite and positive")
    validate_screen_configuration()
    subject = re.sub(r"[^A-Za-z0-9_-]", "_", PARTICIPANT_ID)
    if not subject:
        raise ValueError("Set PARTICIPANT_ID")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    base = OUTPUT_DIR / f"{subject}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    rng_seed = SEED if SEED is not None else random.SystemRandom().randrange(2 ** 32)
    design = make_design(N_BACK, N_TRIALS, rng_seed)
    recorder = StreamRecorder(base)
    events, trials = [], []
    tracker, win, operator_win, operator = None, None, None, None
    status, err = "failed", None
    cal_result, device_info, size, operator_size = None, {}, None, None
    participant_frame_timing = None
    cleanup_errors = []

    def progress(message):
        # The last line helps locate a true block in calibration/SDK startup.
        line = "{} | {}".format(datetime.now().isoformat(timespec="seconds"), message)
        print(line, flush=True)
        with open(f"{base}_progress.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def wait_for_space(kb, text, stage):
        # Unlike waitKeys(default waitRelease=True), this responds on KEY-DOWN.
        kb.clearEvents()
        text.draw()
        win.flip()
        operator.stage = "校正の準備" if stage == "BEFORE_CALIBRATION" else "課題の準備"
        operator.detail = "準備ができたらSPACEを押してください"
        operator.render(force=True)
        progress(stage + ": SPACE to continue; ESC to quit (click task window if needed)")
        timer = core.Clock()
        while timer.getTime() < START_KEY_TIMEOUT_SECONDS:
            keys = kb.getKeys(keyList=["space", "escape"], waitRelease=False, clear=True)
            if any(key.name == "escape" for key in keys):
                raise ExperimentAborted("ESC at " + stage)
            for key in keys:
                if key.name == "space":
                    progress(stage + ": SPACE detected")
                    return
            core.wait(0.01)
        raise RuntimeError(stage + ": no SPACE key received within timeout; check PsychoPy window focus")

    def stamp(event_name, trial_no, stimulus, record=None, field=None):
        us = tr.get_system_time_stamp()
        events.append([event_name, trial_no, stimulus, us])
        if record is not None and field is not None:
            record[field] = us

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

    try:
        # Create the participant window last, so initial keyboard focus is there.
        operator_win = visual.Window(
            fullscr=True, screen=OPERATOR_SCREEN_INDEX, units="pix", color="black",
            waitBlanking=False, checkTiming=False, allowGUI=False, winType="pyglet",
            autoLog=False)
        operator_size = [int(x) for x in operator_win.size]
        win = visual.Window(fullscr=True, screen=SCREEN_INDEX, units="pix",
                            color="black", waitBlanking=True, allowGUI=False,
                            winType="pyglet")
        size = [int(x) for x in win.size]
        kb = keyboard.Keyboard()
        operator = OperatorDisplay(operator_win, subject)
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

        message.text = (f"Calibration complete.\n\n{N_BACK}-BACK\n"
                        f"RIGHT arrow: same digit as {N_BACK} trials ago.\n"
                        "LEFT arrow: different digit.\n"
                        f"For the first {N_BACK} trials, press LEFT.\n"
                        "Respond once on every trial.\n"
                        "Keep looking at + while waiting for the task.\n\n"
                        "SPACE: ready   ESC: exit")
        wait_for_space(kb, message, "AFTER_CALIBRATION_WAIT_FOR_START")
        kb.clearEvents()
        cross.draw()
        win.flip()
        operator.stage = "記録の準備"
        operator.detail = "計測データの記録を開始しています"
        operator.render(force=True)
        progress("SUBSCRIBE gaze started")
        recorder.subscribe(tracker, "gaze", "EYETRACKER_GAZE_DATA", required=True)
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

        run_sync_phase(win, cross, kb, operator, stamp, "start")
        progress("TASK about to start")

        trial_clock = core.Clock()
        frame_period = win.monitorFramePeriod if win.monitorFramePeriod else (1 / 60)
        frame_tol = min(.010, max(.001, frame_period * .5))
        win.frameIntervals = []
        win.nDroppedFrames = 0
        win.recordFrameIntervals = True
        for plan in design:
            operator.trial = plan["trial"]
            if plan["trial"] == 1 or plan["trial"] % 5 == 0:
                progress("TRIAL {}/{}; queue={}".format(plan["trial"], N_TRIALS, recorder.items.qsize()))
            rec = {**plan, "response_key": "", "correct": "", "rt_seconds": "",
                   "stim_on_us": "", "stim_off_us": "",
                   "response_detected_us": "", "completed": 0}
            try:
                digit.text = rec["stimulus"]
                digit.draw()
                win.callOnFlip(trial_clock.reset)
                win.callOnFlip(kb.clock.reset)
                win.callOnFlip(kb.clearEvents)
                if rec["trial"] == 1:
                    win.callOnFlip(operator.start_task)
                    win.callOnFlip(stamp, "task_start", 0, "")
                win.callOnFlip(stamp, "stim_on", rec["trial"], rec["stimulus"],
                               rec, "stim_on_us")
                win.flip()
                operator.detail = "数字を提示中"
                operator.render()

                while trial_clock.getTime() < STIMULUS_SECONDS - frame_tol:
                    poll_keys(kb, rec)
                    digit.draw()
                    win.flip()
                    operator.render()
                cross.draw()
                win.callOnFlip(stamp, "stim_off", rec["trial"], rec["stimulus"],
                               rec, "stim_off_us")
                win.flip()
                operator.detail = "注視点・回答受付中"
                operator.render()
                while trial_clock.getTime() < STIMULUS_SECONDS + ISI_SECONDS - frame_tol:
                    poll_keys(kb, rec)
                    cross.draw()
                    win.flip()
                    operator.render()
                poll_keys(kb, rec)
                rec["completed"] = 1
                expected_key = "right" if rec["is_target"] else "left"
                rec["correct"] = int(rec["response_key"] == expected_key)
                operator.complete_trial(rec)
            finally:
                trials.append([rec.get(key, "") for key in TRIAL_COLUMNS])

        cross.draw()
        win.callOnFlip(operator.finish_task)
        win.callOnFlip(stamp, "task_end", 0, "")
        win.flip()
        win.recordFrameIntervals = False
        participant_frame_timing = frame_timing_summary(win)
        run_sync_phase(win, cross, kb, operator, stamp, "end")
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
        if operator is not None:
            operator.finish_task()
        if win is not None:
            win.recordFrameIntervals = False
            if participant_frame_timing is None and operator is not None and operator.task_started:
                try:
                    participant_frame_timing = frame_timing_summary(win)
                except Exception as exc:
                    cleanup_errors.append("frame timing: {}".format(exc))
        if tracker is not None:
            cleanup_step("cleanup log", lambda: progress("CLEANUP: unsubscribing"))
            cleanup_step("unsubscribe", lambda: recorder.unsubscribe_all(tracker))
        cleanup_step("recorder close", recorder.close)
        for window in (win, operator_win):
            if window is not None:
                cleanup_step("window close", window.close)
        if cleanup_errors:
            err = "{}; {}".format(err or "", "; ".join(cleanup_errors))
            status = "failed"
        save_csv(Path(f"{base}_events.csv"), EVENT_COLUMNS, events)
        save_csv(Path(f"{base}_trials.csv"), TRIAL_COLUMNS, trials)
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
            "python_version": sys.version, "n_back": N_BACK,
            "requested_trials": N_TRIALS,
            "completed_trials": sum(int(r[-1]) for r in trials),
            "stimulus_seconds": STIMULUS_SECONDS, "isi_seconds": ISI_SECONDS,
            "target_probability": TARGET_PROBABILITY, "seed": rng_seed,
            "stimulus_digits": DIGITS,
            "response_keys": {"target": "right", "non_target": "left"},
            "screen_index": SCREEN_INDEX, "screen_pixels": size,
            "participant_wait_blanking": True,
            "operator_screen_index": OPERATOR_SCREEN_INDEX,
            "operator_screen_pixels": operator_size,
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
                              "task_start is the first stimulus flip; task_end follows the final "
                              "trial response window, before the end-sync countdown.",
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
