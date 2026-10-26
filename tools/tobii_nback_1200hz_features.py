# -*- coding: utf-8 -*-
"""PsychoPy 2022.2.4 / Tobii Pro Spectrum: offline sample-aligned features.

Use in PsychoPy Coder *after* N-back recording. No Tobii connection needed.
Designed for Python 3.8+; STANDARD LIBRARY ONLY (no pip installs).

Reads the *_gaze.csv written by psychopy2022_spectrum1200_nback_lightweight.py.
Optionally exact-joins *_eye_openness.jsonl on Tobii DEVICE timestamps.
Writes one output row per gaze input row, retaining all original gaze columns.

For ML: output is unsmoothed, unstandardized, and does not include correctness/RT
labels as predictors. Use subject-wise train/test splits; fit transforms on train.
"""

# ===================== EDIT IN PSYCHOPY CODER ===========================
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data" / "nback"
LEGACY_DATA_DIR = PROJECT_ROOT / "nback_data"
RUN_PREFIX = ""           # "": newest *_gaze.csv, or e.g. "P001_20261022_..."
MAX_DERIVATIVE_GAP_MS = 3.0  # >3 ms or timestamp reversal: derivative = missing
OPENNESS_MATCH_TOLERANCE_US = 0  # default EXACT device timestamp match
SCREEN_CENTER_X_NORM = 0.5
SCREEN_CENTER_Y_NORM = 0.5
PUPIL_BASELINE_LEFT_MM = None   # None = first VALID left-eye pupil sample
PUPIL_BASELINE_RIGHT_MM = None  # None = first VALID right-eye pupil sample
ADD_TRIAL_TIMING = True        # adds trial_index, trial_phase, time_since_trial_on_ms
PRINT_EVERY_N_ROWS = 100000
# ========================================================================

import argparse
from bisect import bisect_right
from collections import Counter
import csv
from datetime import datetime
import json
import math
import sys

NAN = float("nan")

# All outputs are scalar values on the original gaze-sample timestamp grid.
# The original gaze.csv columns are preserved separately, before these features.
FEATURE_COLUMNS = [
    # sample bookkeeping and preprocessing masks
    "time_from_first_sample_ms", "sample_dt_ms", "derivative_dt_usable",
    "valid_pupil_eye_count", "valid_gaze_ray_eye_count", "valid_openness_eye_count",
    # 1-sample pupil values and baseline contrasts (mm)
    "left_pupil_mm_clean", "right_pupil_mm_clean", "pupil_mean_mm",
    "pupil_left_minus_right_mm", "pupil_left_baseline_delta_mm",
    "pupil_right_baseline_delta_mm", "pupil_mean_baseline_delta_mm",
    # 1-sample openness, joined on identical device timestamp (mm)
    "left_openness_mm", "right_openness_mm", "openness_mean_mm",
    "openness_left_minus_right_mm", "openness_device_time_stamp_us",
    # 1-sample eye position: gaze origin in Tobii user-coordinate system (mm)
    "left_eye_position_x_mm", "left_eye_position_y_mm", "left_eye_position_z_mm",
    "right_eye_position_x_mm", "right_eye_position_y_mm", "right_eye_position_z_mm",
    "interocular_distance_mm",
    # 1-sample direction angles relative to Tobii -Z forward axis (degrees)
    "left_gaze_yaw_deg", "left_gaze_pitch_deg", "left_gaze_forward_angle_deg",
    "right_gaze_yaw_deg", "right_gaze_pitch_deg", "right_gaze_forward_angle_deg",
    "gaze_yaw_deg", "gaze_pitch_deg", "gaze_forward_angle_deg",
    # 1-sample screen/stimulus centre offset and binocular disparity
    "left_screen_center_offset_norm", "right_screen_center_offset_norm",
    "gaze_screen_center_offset_norm",  # normalized distance, not visual angle
    "left_gaze_to_stim_center_deg", "right_gaze_to_stim_center_deg",
    "gaze_to_stim_center_deg",   # degrees, requires display-area geometry
    "left_right_gaze_dx_norm", "left_right_gaze_dy_norm",
    "left_right_gaze_distance_norm", "left_right_gaze_distance_deg",
    "vergence_angle_deg",  # between the two eye-specific 3D gaze rays
    # sample-to-sample derivatives (no averages, use measured timestamps)
    "gaze_sample_displacement_deg", "gaze_angular_speed_deg_s",
    "gaze_angular_acceleration_deg_s2",
    "gaze_horizontal_velocity_deg_s", "gaze_vertical_velocity_deg_s",
    "left_pupil_velocity_mm_s", "right_pupil_velocity_mm_s",
    "pupil_mean_velocity_mm_s",
    "left_pupil_acceleration_mm_s2", "right_pupil_acceleration_mm_s2",
    "pupil_mean_acceleration_mm_s2",
    "left_openness_velocity_mm_s", "right_openness_velocity_mm_s",
    "openness_mean_velocity_mm_s",
    "left_openness_acceleration_mm_s2", "right_openness_acceleration_mm_s2",
    "openness_mean_acceleration_mm_s2", "vergence_velocity_deg_s",
]
TRIAL_COLUMNS = ["trial_index", "trial_phase", "time_since_trial_on_ms"]


def number(value):
    """Convert an SDK/CSV scalar to finite float or NaN; never use invalid values."""
    try:
        x = float(value)
        return x if math.isfinite(x) else NAN
    except (TypeError, ValueError):
        return NAN


def integer(value):
    """Parse timestamps without floating point round-off."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def valid(value):
    return str(value).strip().lower() in ("true", "1", "valid")


def finite(*values):
    return all(isinstance(v, (int, float)) and math.isfinite(v) for v in values)


def vec(v):
    return tuple(number(x) for x in v)


def subtract(a, b):
    return tuple(x - y for x, y in zip(a, b))


def length(v):
    return math.sqrt(sum(x * x for x in v))


def normalize(v):
    if not finite(*v):
        return None
    size = length(v)
    return tuple(x / size for x in v) if size > 1e-9 else None


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def angular_distance_deg(a, b):
    """Stable for tiny angular changes (important at 1200 Hz)."""
    if a is None or b is None:
        return NAN
    ax, ay, az = a
    bx, by, bz = b
    cx, cy, cz = ay*bz - az*by, az*bx - ax*bz, ax*by - ay*bx
    sin_angle = math.sqrt(cx*cx + cy*cy + cz*cz)
    return math.degrees(math.atan2(sin_angle, dot(a, b)))


def angle_to_point_deg(origin, gaze, target):
    if origin is None or gaze is None or target is None:
        return NAN
    return angular_distance_deg(normalize(subtract(gaze, origin)),
                                normalize(subtract(target, origin)))


def direction_angles(ray):
    """Yaw (+right) and pitch (+up) of 3D eye ray in Tobii UCS."""
    if ray is None:
        return (NAN, NAN, NAN)
    x, y, z = ray
    return (math.degrees(math.atan2(x, -z)),
            math.degrees(math.atan2(y, math.hypot(x, z))),
            angular_distance_deg(ray, (0., 0., -1.)))


def point(row, side, field, suffixes, validity_field):
    if not valid(row.get("{}_{}".format(side, validity_field))):
        return None
    values = tuple(number(row.get("{}_{}_{}".format(side, field, s))) for s in suffixes)
    return values if finite(*values) else None


def average_good(x, y):
    if finite(x, y):
        return (x + y) / 2.
    if finite(x):
        return x
    if finite(y):
        return y
    return NAN


def xyz(raw):
    if not isinstance(raw, (list, tuple)) or len(raw) != 3:
        return None
    p = vec(raw)
    return p if finite(*p) else None


def get_display_center(metadata):
    """Uses 3D corners in UCS mm, not screen pixel coordinates."""
    area = metadata.get("tracker", {}).get("display_area", {})
    if not isinstance(area, dict):
        return None
    tl, tr, bl = (xyz(area.get(key)) for key in ("top_left", "top_right", "bottom_left"))
    if tl is None or tr is None or bl is None:
        return None
    x = SCREEN_CENTER_X_NORM
    y = SCREEN_CENTER_Y_NORM
    return tuple(tl[j] + x * (tr[j] - tl[j]) + y * (bl[j] - tl[j])
                 for j in range(3))


def read_json(path):
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (ValueError, OSError) as exc:
        print("WARNING: metadata read failed:", exc)
        return {}


class OpennessMatcher:
    """Streaming exact/nearest join to gaze by DEVICE timestamp; no huge DataFrame."""

    def __init__(self, filename, tolerance_us):
        self.tolerance = tolerance_us
        self.filename = filename
        self.fp = filename.open("r", encoding="utf-8-sig") if filename.exists() else None
        self.rows_parsed = 0
        self.bad_rows = 0
        self.matched = 0
        self.previous = None
        self.ahead = self._read_next()

    def _read_next(self):
        if self.fp is None:
            return None
        for line in self.fp:
            try:
                d = json.loads(line)
                ts = integer(d.get("device_time_stamp"))
                if ts is None:
                    raise ValueError("Missing timestamp")
                self.rows_parsed += 1
                return (ts, d)
            except (ValueError, AttributeError, TypeError):
                self.bad_rows += 1
        return None

    def at(self, ts):
        if ts is None:
            return None
        # Keep last sample <=ts and one future sample, then choose closest.
        while self.ahead is not None and self.ahead[0] <= ts:
            self.previous = self.ahead
            self.ahead = self._read_next()
        choices = (self.previous, self.ahead)
        matching = [r for r in choices if r is not None and
                    abs(r[0] - ts) <= self.tolerance]
        if not matching:
            return None
        selected = min(matching, key=lambda r: abs(r[0] - ts))
        self.matched += 1
        return selected[1]

    def close(self):
        if self.fp:
            self.fp.close()


class TrialLabels:
    def __init__(self, base, metadata):
        self.starts, self.stim_ends, self.ends, self.ids = [], [], [], []
        self.available = False
        trials_csv = Path(str(base) + "_trials.csv")
        event_csv = Path(str(base) + "_events.csv")
        if not trials_csv.exists():
            return
        ev_ends = []
        response_ends = []
        if event_csv.exists():
            with event_csv.open("r", encoding="utf-8-sig", newline="") as f:
                for ev in csv.DictReader(f):
                    if ev.get("event") == "task_end":
                        end = integer(ev.get("system_time_stamp_us"))
                        if end is not None:
                            ev_ends.append(end)
        try:
            with trials_csv.open("r", encoding="utf-8-sig", newline="") as f:
                for r in csv.DictReader(f):
                    start = integer(r.get("stim_on_us"))
                    offset = integer(r.get("stim_off_us"))
                    if start is None:
                        continue
                    self.starts.append(start)
                    self.stim_ends.append(offset)
                    self.ids.append(r.get("trial", ""))
                    response_ends.append(integer(r.get("response_window_end_us")))
            if not self.starts:
                return
            order = sorted(range(len(self.starts)), key=self.starts.__getitem__)
            self.starts = [self.starts[i] for i in order]
            self.stim_ends = [self.stim_ends[i] for i in order]
            self.ids = [self.ids[i] for i in order]
            response_ends = [response_ends[i] for i in order]
            isi = number(metadata.get("isi_seconds"))
            isi_us = int(isi * 1000000) if finite(isi) else 0
            for i, start in enumerate(self.starts):
                candidates = []
                if i < len(self.starts) - 1:
                    candidates.append(self.starts[i + 1])
                if response_ends[i] is not None:
                    # Explicit response windows keep rests and sync outside trials,
                    # including a partially completed trial on ESC.
                    candidates.append(max(start, response_ends[i]))
                else:
                    if self.stim_ends[i] is not None:
                        candidates.append(self.stim_ends[i] + isi_us)
                    later_task_ends = [end for end in ev_ends if end > start]
                    if later_task_ends:
                        candidates.append(min(later_task_ends))
                self.ends.append(min(candidates) if candidates else start)
            self.available = True
        except (OSError, ValueError) as exc:
            print("WARNING: trial metadata could not be read:", exc)

    def for_timestamp(self, ts):
        if not self.available or ts is None:
            return ("", "", "")
        index = bisect_right(self.starts, ts) - 1
        if index < 0 or ts >= self.ends[index]:
            return ("", "", "")
        off = self.stim_ends[index]
        phase = "stimulus" if off is None or ts < off else "isi"
        return (self.ids[index], phase, (ts - self.starts[index]) / 1000.)


class FeatureEngine:
    def __init__(self, metadata):
        self.stim_center_3d = get_display_center(metadata)
        self.baseline = {
            "left": number(PUPIL_BASELINE_LEFT_MM),
            "right": number(PUPIL_BASELINE_RIGHT_MM),
        }
        self.first_ts = None
        self.prev_ts = None
        self.prev = None
        self.prev_vel = {}
        self.prev_dir = None
        self.stats = Counter()
        self.ts_gap_count = 0
        self.ts_bad_count = 0
        self.reading_count = 0

    def process(self, row, openness):
        f = {key: NAN for key in FEATURE_COLUMNS}
        self.reading_count += 1
        ts = integer(row.get("device_time_stamp_us"))
        if self.first_ts is None and ts is not None:
            self.first_ts = ts
        if ts is not None and self.first_ts is not None:
            f["time_from_first_sample_ms"] = (ts - self.first_ts) / 1000.

        current = {}
        # pupil validity and normalized pupil values
        for side in ("left", "right"):
            pupil = number(row.get(side + "_pupil_diameter_diameter_mm"))
            if not valid(row.get(side + "_pupil_validity_valid")):
                pupil = NAN
            current[side + "_pupil"] = pupil
            f[side + "_pupil_mm_clean"] = pupil
            if finite(pupil) and not finite(self.baseline[side]):
                self.baseline[side] = pupil
            f["pupil_{}_baseline_delta_mm".format(side)] = (
                pupil - self.baseline[side] if finite(pupil, self.baseline[side]) else NAN)

            open_value = NAN
            if openness is not None and valid(openness.get(side + "_eye_validity")):
                open_value = number(openness.get(side + "_eye_openness_value"))
            current[side + "_openness"] = open_value
            f[side + "_openness_mm"] = open_value
        f["valid_pupil_eye_count"] = sum(finite(current[s + "_pupil"]) for s in ("left", "right"))
        f["valid_openness_eye_count"] = sum(finite(current[s + "_openness"]) for s in ("left", "right"))
        f["openness_device_time_stamp_us"] = integer(openness.get("device_time_stamp")) if openness else NAN
        f["pupil_mean_mm"] = average_good(current["left_pupil"], current["right_pupil"])
        f["pupil_left_minus_right_mm"] = (current["left_pupil"] - current["right_pupil"]
                                           if finite(current["left_pupil"], current["right_pupil"]) else NAN)
        f["pupil_mean_baseline_delta_mm"] = average_good(
            f["pupil_left_baseline_delta_mm"], f["pupil_right_baseline_delta_mm"])
        f["openness_mean_mm"] = average_good(current["left_openness"], current["right_openness"])
        f["openness_left_minus_right_mm"] = (
            current["left_openness"] - current["right_openness"]
            if finite(current["left_openness"], current["right_openness"]) else NAN)
        current["pupil_mean"] = f["pupil_mean_mm"]
        current["openness_mean"] = f["openness_mean_mm"]

        origin, gaze, ray, gaze2d = {}, {}, {}, {}
        for side in ("left", "right"):
            origin[side] = point(row, side, "gaze_origin_in_user_coordinate_system",
                                 ("x_mm", "y_mm", "z_mm"), "gaze_origin_validity_valid")
            gaze[side] = point(row, side, "gaze_point_in_user_coordinate_system",
                               ("x_mm", "y_mm", "z_mm"), "gaze_point_validity_valid")
            gaze2d[side] = point(row, side, "gaze_point_on_display_area",
                                 ("x_norm", "y_norm"), "gaze_point_validity_valid")
            if origin[side] is not None:
                for j, axis in enumerate(("x", "y", "z")):
                    f["{}_eye_position_{}_mm".format(side, axis)] = origin[side][j]
            ray[side] = (normalize(subtract(gaze[side], origin[side]))
                         if origin[side] is not None and gaze[side] is not None else None)
            yaw, pitch, forward = direction_angles(ray[side])
            f[side + "_gaze_yaw_deg"] = yaw
            f[side + "_gaze_pitch_deg"] = pitch
            f[side + "_gaze_forward_angle_deg"] = forward
            if gaze2d[side] is not None:
                px, py = gaze2d[side]
                f[side + "_screen_center_offset_norm"] = math.hypot(
                    px - SCREEN_CENTER_X_NORM, py - SCREEN_CENTER_Y_NORM)
            f[side + "_gaze_to_stim_center_deg"] = angle_to_point_deg(
                origin[side], gaze[side], self.stim_center_3d)

        if origin["left"] is not None and origin["right"] is not None:
            f["interocular_distance_mm"] = length(subtract(origin["left"], origin["right"]))

        f["valid_gaze_ray_eye_count"] = sum(ray[s] is not None for s in ("left", "right"))
        if ray["left"] is not None and ray["right"] is not None:
            f["vergence_angle_deg"] = angular_distance_deg(ray["left"], ray["right"])
            gaze_dir = normalize(tuple((a + b) / 2. for a, b in zip(ray["left"], ray["right"])))
        else:
            gaze_dir = ray["left"] if ray["left"] is not None else ray["right"]
        f["gaze_yaw_deg"], f["gaze_pitch_deg"], f["gaze_forward_angle_deg"] = direction_angles(gaze_dir)
        f["gaze_to_stim_center_deg"] = average_good(
            f["left_gaze_to_stim_center_deg"], f["right_gaze_to_stim_center_deg"])
        # Use the averaged binocular POSITION (rather than the mean of distances)
        # so opposite left/right offsets may cancel as they should.
        two_dim = [gaze2d[s] for s in ("left", "right") if gaze2d[s] is not None]
        if two_dim:
            xavg = sum(q[0] for q in two_dim) / len(two_dim)
            yavg = sum(q[1] for q in two_dim) / len(two_dim)
            f["gaze_screen_center_offset_norm"] = math.hypot(
                xavg - SCREEN_CENTER_X_NORM, yavg - SCREEN_CENTER_Y_NORM)

        if gaze2d["left"] is not None and gaze2d["right"] is not None:
            dx = gaze2d["left"][0] - gaze2d["right"][0]
            dy = gaze2d["left"][1] - gaze2d["right"][1]
            f["left_right_gaze_dx_norm"] = dx
            f["left_right_gaze_dy_norm"] = dy
            f["left_right_gaze_distance_norm"] = math.hypot(dx, dy)
        if origin["left"] is not None and origin["right"] is not None and \
                gaze["left"] is not None and gaze["right"] is not None:
            midpoint = tuple((a + b) / 2. for a, b in zip(origin["left"], origin["right"]))
            f["left_right_gaze_distance_deg"] = angle_to_point_deg(
                midpoint, gaze["left"], gaze["right"])
            # NOTE: unlike vergence, this is the angular separation of gaze
            # *points* from a single cyclopean midpoint origin.

        current.update({
            "pupil_mean": f["pupil_mean_mm"],
            "openness_mean": f["openness_mean_mm"],
            "vergence": f["vergence_angle_deg"],
            "yaw": f["gaze_yaw_deg"], "pitch": f["gaze_pitch_deg"],
        })
        dt_s = NAN
        if ts is not None and self.prev_ts is not None:
            dt_s = (ts - self.prev_ts) / 1000000.
            f["sample_dt_ms"] = dt_s * 1000.
            if dt_s <= 0:
                self.ts_bad_count += 1
            elif dt_s * 1000. > MAX_DERIVATIVE_GAP_MS:
                self.ts_gap_count += 1
        can_derivative = finite(dt_s) and 0 < dt_s * 1000. <= MAX_DERIVATIVE_GAP_MS
        f["derivative_dt_usable"] = int(bool(can_derivative))

        def diff(name, result, prev_name=None):
            before = self.prev.get(prev_name or name, NAN) if self.prev is not None else NAN
            after = current.get(name, NAN)
            if can_derivative and finite(after, before):
                f[result] = (after - before) / dt_s

        diff("left_pupil", "left_pupil_velocity_mm_s")
        diff("right_pupil", "right_pupil_velocity_mm_s")
        diff("pupil_mean", "pupil_mean_velocity_mm_s")
        diff("left_openness", "left_openness_velocity_mm_s")
        diff("right_openness", "right_openness_velocity_mm_s")
        diff("openness_mean", "openness_mean_velocity_mm_s")
        diff("yaw", "gaze_horizontal_velocity_deg_s")
        diff("pitch", "gaze_vertical_velocity_deg_s")
        diff("vergence", "vergence_velocity_deg_s")
        if can_derivative and gaze_dir is not None and self.prev_dir is not None:
            f["gaze_sample_displacement_deg"] = angular_distance_deg(gaze_dir, self.prev_dir)
            f["gaze_angular_speed_deg_s"] = f["gaze_sample_displacement_deg"] / dt_s

        # Acceleration = change of successive velocity estimates. If a step
        # was invalid, its velocity is NaN and acceleration will also be NaN.
        velocity_to_accel = {
            "gaze_angular_speed_deg_s": "gaze_angular_acceleration_deg_s2",
            "left_pupil_velocity_mm_s": "left_pupil_acceleration_mm_s2",
            "right_pupil_velocity_mm_s": "right_pupil_acceleration_mm_s2",
            "pupil_mean_velocity_mm_s": "pupil_mean_acceleration_mm_s2",
            "left_openness_velocity_mm_s": "left_openness_acceleration_mm_s2",
            "right_openness_velocity_mm_s": "right_openness_acceleration_mm_s2",
            "openness_mean_velocity_mm_s": "openness_mean_acceleration_mm_s2",
        }
        for v_name, a_name in velocity_to_accel.items():
            previous_velocity = self.prev_vel.get(v_name, NAN)
            if can_derivative and finite(f[v_name], previous_velocity):
                f[a_name] = (f[v_name] - previous_velocity) / dt_s
        self.prev_vel = {name: f[name] for name in velocity_to_accel}
        self.prev = current
        self.prev_ts = ts
        self.prev_dir = gaze_dir
        return f


def find_input(explicit=None):
    if explicit is not None:
        p = Path(explicit).expanduser()
        if p.is_dir():
            raise ValueError("--gaze must be a *_gaze.csv file, not a folder")
        return p
    if RUN_PREFIX.strip():
        candidate = Path(RUN_PREFIX.strip()).expanduser()
        if not str(candidate).endswith("_gaze.csv"):
            candidate = Path(str(candidate) + "_gaze.csv")
        if candidate.is_absolute():
            return candidate
        for directory in (DATA_DIR, LEGACY_DATA_DIR):
            direct = directory / candidate
            if direct.is_file():
                return direct
            if candidate.parent == Path("."):
                matches = [p for p in directory.rglob("*_gaze.csv") if p.name == candidate.name]
                if len(matches) > 1:
                    raise ValueError("Multiple recordings match RUN_PREFIX; specify the full path or --gaze")
                if matches:
                    return matches[0]
        return DATA_DIR / candidate
    for directory in (DATA_DIR, LEGACY_DATA_DIR):
        matches = [p for p in directory.rglob("*_gaze.csv") if p.is_file()]
        if matches:
            return max(matches, key=lambda p: (p.stat().st_mtime, str(p)))
    raise FileNotFoundError("No *_gaze.csv was found in {} or {}".format(DATA_DIR, LEGACY_DATA_DIR))


def output_float(value):
    """Blank represents missing numeric value; pandas/numpy will read NaN."""
    if value is None:
        return ""
    if isinstance(value, float) and not math.isfinite(value):
        return ""
    return value


def run(source, output=None):
    source = Path(source)
    if not source.exists():
        raise FileNotFoundError(str(source))
    if not str(source).endswith("_gaze.csv"):
        raise ValueError("Expected recording file ending in _gaze.csv")
    prefix = Path(str(source)[:-len("_gaze.csv")])
    out = Path(output) if output else Path(str(prefix) + "_features_1200Hz.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() == out.resolve():
        raise ValueError("Refusing to overwrite raw gaze input")
    metadata = read_json(Path(str(prefix) + "_metadata.json"))
    openness = OpennessMatcher(Path(str(prefix) + "_eye_openness.jsonl"),
                               OPENNESS_MATCH_TOLERANCE_US)
    trials = TrialLabels(prefix, metadata) if ADD_TRIAL_TIMING else None
    engine = FeatureEngine(metadata)
    nan_counts = Counter()
    print("Input:      ", source)
    print("Openness:   ", openness.filename if openness.fp else "not recorded")
    print("Display 3D:", "available" if engine.stim_center_3d else "missing (degree offsets will be blank)")
    print("Trials:     ", "available" if trials and trials.available else "not available")
    print("Output:     ", out)
    try:
        with source.open("r", encoding="utf-8-sig", newline="") as inp, \
                out.open("w", encoding="utf-8-sig", newline="", buffering=1024*1024) as dst:
            reader = csv.DictReader(inp)
            input_columns = reader.fieldnames
            if not input_columns or "device_time_stamp_us" not in input_columns:
                raise ValueError("Input file is not the expected lightweight-script gaze CSV")
            columns = list(input_columns) + [col for col in FEATURE_COLUMNS if col not in input_columns]
            if ADD_TRIAL_TIMING:
                columns.extend(col for col in TRIAL_COLUMNS if col not in columns)
            writer = csv.DictWriter(dst, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for row in reader:
                if None in row:  # malformed extra comma
                    raise ValueError("Malformed raw CSV row {}".format(engine.reading_count + 2))
                eye = openness.at(integer(row.get("device_time_stamp_us")))
                f = engine.process(row, eye)
                if ADD_TRIAL_TIMING:
                    t, phase, elapsed = (trials.for_timestamp(integer(row.get("system_time_stamp_us")))
                                         if trials else ("", "", ""))
                    f.update({"trial_index": t, "trial_phase": phase,
                              "time_since_trial_on_ms": elapsed})
                for col, val in f.items():
                    if isinstance(val, float) and not math.isfinite(val):
                        nan_counts[col] += 1
                    row[col] = output_float(val)
                writer.writerow(row)
                if PRINT_EVERY_N_ROWS and engine.reading_count % PRINT_EVERY_N_ROWS == 0:
                    print("... {:,} gaze rows processed".format(engine.reading_count), flush=True)
    finally:
        openness.close()

    count = engine.reading_count
    report = {
        "generated_at_local": datetime.now().isoformat(timespec="seconds"),
        "source": str(source), "output": str(out),
        "gaze_rows_in_and_out": count,
        "nominal_gaze_hz": metadata.get("tracker", {}).get("gaze_frequency_hz"),
        "eye_openness_jsonl_exists": openness.filename.exists(),
        "openness_lines_processed": openness.rows_parsed,
        "openness_malformed_lines": openness.bad_rows,
        "openness_gaze_rows_matched": openness.matched,
        "openness_match_fraction": openness.matched / count if count else 0,
        "display_area_3d_available": engine.stim_center_3d is not None,
        "stimulus_center_normalized": [SCREEN_CENTER_X_NORM, SCREEN_CENTER_Y_NORM],
        "derivative_max_allowed_dt_ms": MAX_DERIVATIVE_GAP_MS,
        "timestamp_nonincreasing_count": engine.ts_bad_count,
        "timestamp_large_gaps_count": engine.ts_gap_count,
        "pupil_baseline_strategy": "first valid sample per eye unless values explicitly configured",
        "pupil_baseline_left_mm": output_float(engine.baseline["left"]),
        "pupil_baseline_right_mm": output_float(engine.baseline["right"]),
        "feature_columns": FEATURE_COLUMNS,
        "missing_feature_fraction": {
            col: round(nan_counts.get(col, 0) / count, 4) if count else None
            for col in FEATURE_COLUMNS
        },
        "trial_annotations_added": bool(trials and trials.available and ADD_TRIAL_TIMING),
        "notes": [
            "Rows are NOT upsampled, decimated, smoothed, or interpolated.",
            "A data row does not guarantee valid eye tracking at every timestamp.",
            "Angular derivatives of noisy raw eye data can be noisy; validate filtering separately.",
            "Openness is not available if not captured during experiment.",
            "Baseline deltas use first valid gaze sample unless provided; not an event baseline.",
            "Feature files are unsupervised inputs; use separate trials.csv for outcome labels.",
            "Velocity/acceleration are computed with real device timestamp intervals.",
            "Gaze angular acceleration is the derivative of unsigned angular speed.",
        ]
    }
    report_file = Path(str(prefix) + "_features_quality.json")
    with report_file.open("w", encoding="utf-8") as fp:
        json.dump(report, fp, ensure_ascii=False, indent=2, allow_nan=False)
    print("Completed: {:,} gaze samples; {:,} matching openness samples.".format(count, openness.matched))
    print("Time issues: nonincreasing={}, gaps>{}ms={}".format(
        engine.ts_bad_count, MAX_DERIVATIVE_GAP_MS, engine.ts_gap_count))
    print("Saved features:", out)
    print("Saved quality report:", report_file)
    return out, report_file


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gaze", default=None, help="Full path to a *_gaze.csv recording")
    parser.add_argument("--output", default=None, help="Output CSV path (optional)")
    args, _ = parser.parse_known_args()  # ignore PsychoPy Coder extra CLI flags
    run(find_input(args.gaze), args.output)


if __name__ == "__main__":
    main()
