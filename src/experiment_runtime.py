# -*- coding: utf-8 -*-
"""Shared recording, calibration and nonblocking sync. No task-global settings.

PsychoPy APIs and the SDK are supplied by each entry point so settings and
clock contexts remain local to an experiment.
"""

import csv
from collections import Counter
from contextlib import ExitStack
import json
import math
import queue
import threading

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


def check_escape(kb):
    if kb.getKeys(keyList=["escape"], waitRelease=False, clear=True):
        raise ExperimentAborted("ESC pressed")


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


def frame_timing_summary(win):
    intervals = list(win.frameIntervals)
    return {
        "recorded_intervals": len(intervals),
        "mean_interval_seconds": sum(intervals) / len(intervals) if intervals else None,
        "max_interval_seconds": max(intervals) if intervals else None,
        "dropped_frames": int(win.nDroppedFrames),
        "refresh_threshold_seconds": float(win.refreshThreshold),
    }


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


def validate_screen_configuration(participant_screen, operator_screen):
    """Reject missing/duplicate monitors rather than pyglet's screen fallback."""
    import pyglet

    if any(type(index) is not int or index < 0
           for index in (participant_screen, operator_screen)):
        raise ValueError("SCREEN_INDEX and OPERATOR_SCREEN_INDEX must be nonnegative integers")
    if participant_screen == operator_screen:
        raise ValueError("SCREEN_INDEX and OPERATOR_SCREEN_INDEX must select different monitors")
    screens = pyglet.canvas.get_display().get_screens()
    if max(participant_screen, operator_screen) >= len(screens):
        raise ValueError("Two extended-desktop monitors are required. Found {}; check "
                         "SCREEN_INDEX={} and OPERATOR_SCREEN_INDEX={}".format(
                             len(screens), participant_screen, operator_screen))


def select_tracker(sdk, serial=None):
    found = list(sdk.find_all_eyetrackers())
    if serial:
        found = [t for t in found if t.serial_number == serial]
    if not found:
        raise RuntimeError("Tobii not found: check connection, Tobii software and network")
    if len(found) > 1:
        raise RuntimeError("Several trackers found: set TOBII_SERIAL in the entry script")
    return found[0]


def calibrate_tracker(tracker, win, progress=None, kb=None, *, core, visual, sdk):
    calibration = sdk.ScreenBasedCalibration(tracker)
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
            if result != sdk.CALIBRATION_STATUS_SUCCESS:
                if progress: progress("CALIBRATION: retry point {} {}".format(x, y))
                result = calibration.collect_data(x, y)
            if result != sdk.CALIBRATION_STATUS_SUCCESS:
                raise RuntimeError(f"Calibration point {(x, y)} failed: {result}")
        if progress: progress("CALIBRATION: compute/apply")
        result = calibration.compute_and_apply()
        if result.status != sdk.CALIBRATION_STATUS_SUCCESS:
            raise RuntimeError(f"Calibration not applied: {result.status}")
        return {"status": str(result.status), "points": points}
    finally:
        if entered:
            if progress: progress("CALIBRATION: leave calibration mode")
            calibration.leave_calibration_mode()
            if progress: progress("CALIBRATION: left calibration mode")
        win.flip()


class StreamRecorder:
    """Nonblocking Tobii callbacks; dedicated disk writer; bounded queue."""

    def __init__(self, base, settings, sdk):
        self.settings = dict(settings)
        self.sdk = sdk
        self.base = base
        self.items = queue.Queue(maxsize=self.settings["QUEUE_CAPACITY"])
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
        stream_type = getattr(self.sdk, const_name, None)
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
                if self.settings["SAVE_GAZE_RAW_JSONL"]:
                    json_kinds.append("gaze_raw")
                if self.settings["RECORD_EYE_OPENNESS"]:
                    json_kinds.append("eye_openness")
                if self.settings["RECORD_OTHER_STREAMS"]:
                    json_kinds.extend(("time_sync", "external_signal", "user_position",
                                       "notifications", "stream_errors"))
                if self.settings["RECORD_EYE_IMAGES"]:
                    json_kinds.append("eye_images_index")
                for kind in json_kinds:
                    json_files[kind] = stack.enter_context(open(
                        f"{self.base}_{kind}.jsonl", "w", encoding="utf-8", buffering=1024 * 1024))
                image_bin = stack.enter_context(open(
                    f"{self.base}_eye_images.bin", "wb", buffering=1024 * 1024)) if self.settings["RECORD_EYE_IMAGES"] else None
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
                        if self.settings["SAVE_GAZE_RAW_JSONL"]:
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


class BlockSyncDisplay:
    """Nonblocking cues on the operator display, sharing the set's clock."""

    def __init__(self, operator, stamp, clock, active_seconds, rest_seconds, blocks,
                 countdown_seconds, cue_seconds, core, check_escape):
        self.rest_seconds, self.blocks = rest_seconds, blocks
        self.countdown_seconds, self.cue_seconds = countdown_seconds, cue_seconds
        self.core, self.check_escape = core, check_escape
        self.operator, self.stamp, self.clock = operator, stamp, clock
        self.countdown = None
        self.cue = None
        self.schedule = []
        active = active_seconds
        for block in range(1, self.blocks + 1):
            start = (block - 1) * (active + self.rest_seconds)
            for phase, boundary in (("start", start), ("end", start + active)):
                if block != 1 or phase != "start":
                    self.schedule.append((boundary - self.countdown_seconds, block, phase, "countdown", boundary))
                self.schedule.append((boundary, block, phase, "cue", boundary))
                self.schedule.append((boundary + self.cue_seconds, block, phase, "cue_end", boundary))
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
        timer = self.core.Clock()
        self.operator.stage = "開始同期"
        self.operator.detail = "ブロック1 開始マーカーまで {} 秒".format(self.countdown_seconds)

        def onset():
            timer.reset()
            self.stamp("sync_start_countdown", 0, "", block=1)

        self.operator.render(force=True, on_flip=onset)
        tolerance = (win.monitorFramePeriod or 1 / 60) * .5
        while timer.getTime() < self.countdown_seconds - tolerance:
            self.check_escape(kb)
            remaining = max(1, int(math.ceil(self.countdown_seconds - timer.getTime())))
            detail = "ブロック1 開始マーカーまで {} 秒".format(remaining)
            if detail != self.operator.detail:
                self.operator.detail = detail
                self.operator.render(force=True)
            cross.draw()
            win.flip()


