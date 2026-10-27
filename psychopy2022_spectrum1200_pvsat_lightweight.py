# -*- coding: utf-8 -*-
"""PVSAT: adjacent-digit sum detection, PsychoPy 2022.2.4 / Spectrum 1200 Hz.

Run this file in Coder. ID and PVSAT session 1..3 select the presentation rate.
One difficulty has three 60-second task / 30-second rest blocks, then Raw TLX.
Hardware timing must be confirmed on the experiment PC.
"""

# ========================= EDIT THESE SETTINGS ===========================
N_BLOCKS = 3
TASK_SECONDS = 60.0
REST_SECONDS = 30.0
STIMULUS_SECONDS = 0.5
TARGET_SUM = 11
TARGET_PROPORTION = 0.30
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

from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random
import sys

# PsychoPy Coder may start with another working directory.
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from psychopy import core, visual
from psychopy.hardware import keyboard
import psychopy
import tobii_research as tr
from src.experiment_paths import create_output_prefix
from src.nasa_tlx import RawTLXSession, run_raw_tlx
from src.experiment_assignment import load_pvsat_assignment, prompt_pvsat_assignment
from src.pvsat_task import (DIGITS, INTERVAL_BY_DIFFICULTY, PvsatResponse,
                            make_pvsat_design, presentation_settings)

import src.experiment_runtime as runtime
from src.experiment_runtime import (OPTIONAL_STREAMS, NOTIFICATIONS,
    ExperimentAborted, check_escape, json_safe,
    frame_timing_summary, save_csv, tracker_info)

OUTPUT_DIR = PROJECT_ROOT / "data"
COUNTERBALANCE_CSV = PROJECT_ROOT / "counterbalance.csv"

EVENT_COLUMNS = ["event", "trial", "stimulus", "system_time_stamp_us", "block"]
TRIAL_COLUMNS = [
    "trial", "block", "trial_in_block", "stimulus", "previous_digit", "pair_sum",
    "target_sum", "response_required", "is_target", "expected_key", "response_key",
    "correct", "rt_seconds", "stim_on_us", "stim_off_us", "response_detected_us",
    "response_window_end_us", "input_deadline_rt_seconds", "planned_onset_seconds",
    "onset_error_seconds", "completed",
]


def participant_instructions(assignment):
    interval, isi, count = presentation_settings(assignment.difficulty_level, TASK_SECONDS, STIMULUS_SECONDS)
    example_a, example_b = next((a, b) for a in DIGITS for b in DIGITS if a + b == TARGET_SUM)
    example_c = next(digit for digit in DIGITS if example_b + digit != TARGET_SUM)
    return ("PVSAT課題\n\n"
            "今の数字と、その直前に表示された数字を足してください。\n"
            "和が{}と一致：右矢印    一致しない：左矢印\n"
            "例：{}→{}は右、{}→{}は左です。\n"
            "各ブロックの最初の数字は覚えるだけで、回答しません。\n"
            "以降は、数字とその後の＋の間に1回回答してください。\n"
            "毎回、直前の数字と足します。合計を足し続けないでください。\n"
            "数字は1〜9です。ブロックが変わったら前の数字を忘れてください。\n"
            "数字{}秒＋注視点{}秒で、{}秒ごとに切り替わります。\n"
            "1ブロック{}個・課題{}秒＋休憩{}秒を{}回実施します。\n"
            "休憩中は＋を見てください。最後に質問紙へ回答します。\n\n"
            "SPACE：準備完了    ESC：中断").format(
                TARGET_SUM, example_a, example_b, example_b, example_c,
                STIMULUS_SECONDS, isi, interval, count,
                TASK_SECONDS, REST_SECONDS, N_BLOCKS)


def validate_screen_configuration():
    return runtime.validate_screen_configuration(SCREEN_INDEX, OPERATOR_SCREEN_INDEX)


class OperatorDisplay:
    """Reusable main-thread dashboard; memory-only presentations are unscored."""

    def __init__(self, win, assignment):
        self.win, self.assignment = win, assignment
        self.interval, self.isi, self.trial_count = presentation_settings(
            assignment.difficulty_level, TASK_SECONDS, STIMULUS_SECONDS)
        self.gaze_frequency = None
        self.stage, self.detail, self.normal_detail = "準備中", "", ""
        self.trial, self.block, self.presented = 0, 0, 0
        self.completed, self.correct, self.last_result = 0, 0, None
        self.rest_remaining = None
        self.clock, self.task_clock = core.Clock(), core.Clock()
        self.task_started, self.elapsed_at_end = False, None
        self.last_update, self.last_text = float("-inf"), None
        width, height = win.size
        self.banner = visual.TextStim(win, font="Yu Gothic", color="white", height=height * .045,
                                     pos=(0, height * .39), wrapWidth=width * .92, autoLog=False)
        self.body = visual.TextStim(win, font="Yu Gothic", color="white", height=height * .029,
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
            self.presented += 1
            if record["response_required"]:
                self.completed += 1
                self.correct += record["correct"]
                self.last_result = dict(record)

    def render(self, force=False, on_flip=None):
        now = self.clock.getTime()
        if not force and on_flip is None and now - self.last_update < 1 / OPERATOR_REFRESH_HZ:
            return False
        self.last_update = now
        elapsed = self.elapsed_at_end
        if elapsed is None:
            elapsed = self.task_clock.getTime() if self.task_started else 0
        accuracy = "未集計" if not self.completed else "{:.1f}% ({}/{})".format(
            100 * self.correct / self.completed, self.correct, self.completed)
        result = "直近の回答対象試行: なし"
        if self.last_result is not None:
            last = self.last_result
            response = {"left": "左", "right": "右", "": "未回答"}[last["response_key"]]
            rt = "-" if last["rt_seconds"] == "" else "{:.3f}秒".format(last["rt_seconds"])
            result = "直近 {}: {} + {} = {} / {} / {} / RT {}".format(
                last["trial"], last["previous_digit"], last["stimulus"], last["pair_sum"],
                response, "正解" if last["correct"] else "不正解", rt)
        a = self.assignment
        body = ("段階: {}\nID: {}    課題: PVSAT    目標値: {}\n"
                "PVSAT実験回数: {}/3    難易度: {}\n"
                "数字{}秒＋注視点{}秒    提示間隔{}秒\n"
                "一致割合: 約{:.0f}%    取得周波数: {} Hz\n\n"
                "ブロック: {}/{}    提示番号: {}/{}\n"
                "完了提示: {}/{}    完了回答対象: {}/{}\n"
                "経過: {}秒    休憩残り: {}\n{}\n累積正答率: {}\n\n"
                "最初の数字は記憶のみ。一致は右／不一致は左\n"
                "SPACE：準備完了    ESC：中断").format(
                    self.stage, a.participant_id, TARGET_SUM, a.session_number, a.difficulty_level,
                    STIMULUS_SECONDS, self.isi, self.interval, TARGET_PROPORTION * 100,
                    self.gaze_frequency if self.gaze_frequency is not None else "未確認",
                    self.block, N_BLOCKS, self.trial, self.trial_count,
                    self.presented, N_BLOCKS * self.trial_count, self.completed,
                    N_BLOCKS * (self.trial_count - 1), int(elapsed),
                    "-" if self.rest_remaining is None else self.rest_remaining, result, accuracy)
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


class BlockSyncDisplay(runtime.BlockSyncDisplay):
    def __init__(self, operator, stamp, clock):
        super().__init__(operator, stamp, clock,
                         active_seconds=TASK_SECONDS,
                         rest_seconds=REST_SECONDS, blocks=N_BLOCKS,
                         countdown_seconds=SYNC_COUNTDOWN_SECONDS, cue_seconds=SYNC_CUE_SECONDS,
                         core=core, check_escape=check_escape)


def run_pvsat_blocks(win, cross, digit, kb, operator, designs, trials, protocol,
                     stamp, poll_keys, checkpoint, interval, n_trials):
    """Use absolute set deadlines so rest/cues do not accumulate extra time."""
    clock = core.Clock()
    sync = BlockSyncDisplay(operator, stamp, clock)
    sync.prepare(win, cross, kb)
    active = TASK_SECONDS
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
        poll_keys(kb, record)
        stamp("response_window_end", record["trial"], record["stimulus"],
              record, "response_window_end_us")
        record["correct"] = int(record["response_key"] == record["expected_key"]) if record["response_required"] else ""
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
            onset = start + (local_trial - 1) * interval
            record = dict(plan)
            record.update(trial=(block - 1) * n_trials + local_trial, block=block,
                          trial_in_block=local_trial, response_key="", correct="", rt_seconds="",
                          stim_on_us="", stim_off_us="", response_detected_us="",
                          response_window_end_us="", planned_onset_seconds=onset,
                          onset_error_seconds="", input_deadline_rt_seconds="", completed=0)
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
            operator.normal_detail = "数字を提示中" if record["response_required"] else "最初の数字を記憶・回答不要"
            digit.text = record["stimulus"]
            digit.draw()
            win.callOnFlip(kb.clock.reset)
            win.callOnFlip(kb.clearEvents)
            win.callOnFlip(stamp, "stim_on", record["trial"], record["stimulus"], record, "stim_on_us")
            def set_deadline(record=record, onset=onset):
                error = clock.getTime() - onset
                record.update(onset_error_seconds=error,
                              input_deadline_rt_seconds=max(0, interval - error))

            win.callOnFlip(set_deadline)
            win.flip()
            sync.update()
            if local_trial == 1 and block > 1:
                checkpoint()
            wait_until(onset + STIMULUS_SECONDS, digit, record)
            operator.normal_detail = "注視点・回答受付中" if record["response_required"] else "最初の数字を記憶・回答不要"
            cross.draw()
            win.callOnFlip(stamp, "stim_off", record["trial"], record["stimulus"], record, "stim_off_us")
            win.flip()
            sync.update()
            wait_until(onset + interval, cross, record)
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


def select_tracker():
    return runtime.select_tracker(tr, TOBII_SERIAL)


def calibrate_tracker(tracker, win, progress=None, kb=None):
    return runtime.calibrate_tracker(tracker, win, progress, kb, core=core, visual=visual, sdk=tr)


class StreamRecorder(runtime.StreamRecorder):
    def __init__(self, base):
        super().__init__(base, {
            "QUEUE_CAPACITY": QUEUE_CAPACITY,
            "SAVE_GAZE_RAW_JSONL": SAVE_GAZE_RAW_JSONL,
            "RECORD_EYE_OPENNESS": RECORD_EYE_OPENNESS,
            "RECORD_OTHER_STREAMS": RECORD_OTHER_STREAMS,
            "RECORD_EYE_IMAGES": RECORD_EYE_IMAGES,
        }, tr)


def main(participant_id=None, session_number=None):
    if participant_id is None and session_number is None:
        assignment = prompt_pvsat_assignment(COUNTERBALANCE_CSV, blocks=N_BLOCKS)
        if assignment is None:
            return
    elif participant_id is None or session_number is None:
        raise ValueError("Specify both participant_id and session_number")
    else:
        assignment = load_pvsat_assignment(COUNTERBALANCE_CSV, participant_id, session_number)
    subject = assignment.participant_id
    interval, isi_seconds, n_trials = presentation_settings(
        assignment.difficulty_level, TASK_SECONDS, STIMULUS_SECONDS)
    if QUEUE_CAPACITY < 2:
        raise ValueError("QUEUE_CAPACITY must be >= 2")
    if type(SYNC_COUNTDOWN_SECONDS) is not int or SYNC_COUNTDOWN_SECONDS < 1:
        raise ValueError("SYNC_COUNTDOWN_SECONDS must be a positive integer")
    if not all(math.isfinite(value) and value > 0
               for value in (SYNC_CUE_SECONDS, OPERATOR_REFRESH_HZ, REST_SECONDS,
                             STIMULUS_SECONDS, TASK_SECONDS)):
        raise ValueError("Timing and refresh settings must be finite and positive")
    if type(N_BLOCKS) is not int or N_BLOCKS < 1:
        raise ValueError("N_BLOCKS must be a positive integer")
    active_seconds = TASK_SECONDS
    if min(active_seconds, REST_SECONDS) < SYNC_COUNTDOWN_SECONDS + SYNC_CUE_SECONDS:
        raise ValueError("Task and rest must accommodate the countdown and marker cue")
    validate_screen_configuration()

    rng_seed = SEED if SEED is not None else random.SystemRandom().randrange(2 ** 32)
    seed_rng = random.Random(rng_seed)
    block_seeds = [seed_rng.randrange(2 ** 32) for _ in range(N_BLOCKS)]
    designs = [make_pvsat_design(n_trials, seed, TARGET_SUM, TARGET_PROPORTION) for seed in block_seeds]
    base = create_output_prefix(OUTPUT_DIR, "pvsat", subject,
                                assignment.session_number, assignment.difficulty_level)
    recorder = StreamRecorder(base)
    events, trials, response_states = [], [], {}
    tracker, win, operator_win, operator = None, None, None, None
    status, err = "failed", None
    cal_result, device_info, size, operator_size = None, {}, None, None
    participant_frame_timing = None
    cleanup_errors = []
    protocol = {"blocks": [], "completed_blocks": 0, "set_start_us": None, "set_end_us": None}
    tlx = RawTLXSession(Path(f"{base}_tlx.csv"), subject, None, N_BLOCKS,
                        session_number=assignment.session_number,
                        difficulty_level=assignment.difficulty_level, task="pvsat")
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
        state = response_states.get(record["trial"])
        if state is None:
            state = PvsatResponse(record["response_required"], record["input_deadline_rt_seconds"])
            response_states[record["trial"]] = state
        keys = kb.getKeys(keyList=["left", "right", "escape"], waitRelease=False, clear=True)
        for key in sorted(keys, key=lambda item: float(item.rt)):
            if key.name == "escape":
                raise ExperimentAborted("ESC pressed")
            if state.press(key.name, float(key.rt)):
                record.update(response_key=state.key, rt_seconds=state.rt_seconds)
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
            allowGUI=True, winType="pyglet", autoLog=False)
        operator_size = [int(x) for x in operator_win.size]
        win = visual.Window(fullscr=True, screen=SCREEN_INDEX, units="pix",
                            color="black", waitBlanking=True, allowGUI=False,
                            winType="pyglet")
        size = [int(x) for x in win.size]
        kb = keyboard.Keyboard()
        operator = OperatorDisplay(operator_win, assignment)
        operator.detail = "Tobii接続を確認しています"
        operator.render(force=True)
        message = visual.TextStim(win, font="Yu Gothic", color="white", height=28, wrapWidth=1100)
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

        run_pvsat_blocks(win, cross, digit, kb, operator, designs, trials, protocol,
                         stamp, poll_keys, checkpoint, interval, n_trials)
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
            "python_version": sys.version,
            "task": "pvsat", "session_number": assignment.session_number,
            "difficulty_level": assignment.difficulty_level,
            "counterbalance_csv": str(COUNTERBALANCE_CSV),
            "output_directory": str(base.parent), "output_prefix": str(base),
            "counterbalance_column": assignment.column,
            "interval_by_difficulty": INTERVAL_BY_DIFFICULTY,
            "presentation_interval_seconds": interval, "target_sum": TARGET_SUM,
            "target_proportion": TARGET_PROPORTION,
            "response_trials_per_block": n_trials - 1,
            "requested_response_trials": (n_trials - 1) * N_BLOCKS,
            "completed_response_trials": sum(row["completed"] and row["response_required"] for row in trials),
            "correct_responses": sum(row["correct"] for row in trials if row["completed"] and row["response_required"]),
            "target_counts_per_block": [sum(row["is_target"] == 1 for row in design) for design in designs],
            "actual_target_proportions": [sum(row["is_target"] == 1 for row in design) / (n_trials - 1) for design in designs],
            "first_presentation": "memory_only_each_block",
            "requested_trials": n_trials * N_BLOCKS,
            "completed_trials": sum(row["completed"] for row in trials),
            "trials_per_block": n_trials, "requested_blocks": N_BLOCKS,
            "completed_blocks": protocol["completed_blocks"],
            "rest_seconds": REST_SECONDS,
            "planned_set_seconds": N_BLOCKS * (active_seconds + REST_SECONDS),
            "actual_set_seconds": ((protocol["set_end_us"] - protocol["set_start_us"]) / 1e6
                                   if protocol["set_end_us"] is not None else None),
            "block_seeds": block_seeds, "blocks": protocol["blocks"],
            "tlx_status": tlx.status, "raw_tlx": tlx.score,
            "stimulus_seconds": STIMULUS_SECONDS, "isi_seconds": isi_seconds,
            "seed": rng_seed,
            "stimulus_digits": list(DIGITS),
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
                              f"set_start/set_end include {N_BLOCKS} active/rest blocks; TLX is recorded separately.",
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
