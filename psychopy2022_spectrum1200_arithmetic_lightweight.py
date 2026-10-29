# -*- coding: utf-8 -*-
"""Timed addition, PsychoPy 2022.2.4 / Tobii Spectrum 1200 Hz.

One difficulty: 12 questions x 5 s + 30 s rest, repeated for 3 blocks.
Run this file in Coder; ID and arithmetic session 1..3 select the difficulty.
"""

N_TRIALS = 12
N_BLOCKS = 3
TRIAL_SECONDS = 5.0
REST_SECONDS = 30.0
MAX_INPUT_DIGITS = 4
TOBII_FREQUENCY_HZ = 1200
TOBII_SERIAL = None
SCREEN_INDEX = 1
OPERATOR_SCREEN_INDEX = 0
OPERATOR_WINDOW_SIZE = (960, 640)
OPERATOR_REFRESH_HZ = 5.0
SYNC_COUNTDOWN_SECONDS = 10
SYNC_CUE_SECONDS = 1.0
SKIP_CALIBRATION = False
RECORD_EYE_OPENNESS = True
RECORD_OTHER_STREAMS = False
RECORD_EYE_IMAGES = False
SAVE_GAZE_RAW_JSONL = False
SAVE_CALIBRATION_DATA = True
RECORD_USER_POSITION_DURING_TASK = False
QUEUE_CAPACITY = 20000
START_KEY_TIMEOUT_SECONDS = 120
STARTUP_DIAGNOSTICS = True  # One-shot stack trace for each slow startup operation
STARTUP_TRACEBACK_SECONDS = 20.0  # Diagnostic only; does not terminate the process
SEED = None

import json
import math
from datetime import datetime, timezone
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

import src.experiment_runtime as runtime
from src.experiment_runtime import (ExperimentAborted, check_escape, json_safe, save_csv,
    tracker_info, frame_timing_summary, OPTIONAL_STREAMS, NOTIFICATIONS)
from src.experiment_assignment import load_arithmetic_assignment, prompt_arithmetic_assignment
from src.arithmetic_task import OPERAND_RANGES, ArithmeticInput, make_arithmetic_design
from src.experiment_paths import create_output_prefix
from src.startup_diagnostics import StartupDiagnostics
from src.nasa_tlx import RawTLXSession, run_raw_tlx

OUTPUT_DIR = PROJECT_ROOT / "data"
COUNTERBALANCE_CSV = PROJECT_ROOT / "counterbalance.csv"
EVENT_COLUMNS = ["event", "trial", "stimulus", "system_time_stamp_us", "block"]
TRIAL_COLUMNS = [
    "trial", "block", "trial_in_block", "stimulus", "operand_a", "operand_b",
    "correct_answer", "carry_count", "has_carry", "answer_text", "answer_value",
    "unanswered", "correct", "first_input_rt_seconds", "last_edit_rt_seconds",
    "stim_on_us", "stim_off_us", "response_window_end_us", "auto_submit_us",
    "input_deadline_rt_seconds", "planned_onset_seconds", "onset_error_seconds", "completed",
]
INPUT_COLUMNS = ["trial", "block", "trial_in_block", "key_name", "action", "text_after",
                 "rt_seconds", "system_time_stamp_us"]
INPUT_KEYS = (["num_" + digit for digit in "0123456789"] + list("0123456789")
              + ["return", "num_enter", "escape"])


def validate_screen_configuration():
    return runtime.validate_screen_configuration(SCREEN_INDEX, OPERATOR_SCREEN_INDEX)


def select_tracker():
    return runtime.select_tracker(tr, TOBII_SERIAL)


def calibrate_tracker(tracker, win, progress=None, kb=None):
    return runtime.calibrate_tracker(tracker, win, progress, kb, core=core, visual=visual, sdk=tr)


class StreamRecorder(runtime.StreamRecorder):
    def __init__(self, base):
        super().__init__(base, {
            "QUEUE_CAPACITY": QUEUE_CAPACITY, "SAVE_GAZE_RAW_JSONL": SAVE_GAZE_RAW_JSONL,
            "RECORD_EYE_OPENNESS": RECORD_EYE_OPENNESS, "RECORD_OTHER_STREAMS": RECORD_OTHER_STREAMS,
            "RECORD_EYE_IMAGES": RECORD_EYE_IMAGES,
        }, tr)


class BlockSyncDisplay(runtime.BlockSyncDisplay):
    def __init__(self, operator, stamp, clock):
        super().__init__(operator, stamp, clock, active_seconds=N_TRIALS * TRIAL_SECONDS,
                         rest_seconds=REST_SECONDS, blocks=N_BLOCKS,
                         countdown_seconds=SYNC_COUNTDOWN_SECONDS, cue_seconds=SYNC_CUE_SECONDS,
                         core=core, check_escape=check_escape)


def participant_instructions(assignment):
    return ("暗算課題（{}）\n\n"
            "テンキーで和を入力\n"
            "ナムロック（NumLock）：ON\n"
            "エンターキー（Enter）：全消去\n"
            "{:g}秒後に自動採点\n\n"
            "スペースキー（SPACE）：準備完了\nエスケープキー（ESC）：中断").format(
                assignment.condition_label, TRIAL_SECONDS)


class ArithmeticStimulus:
    def __init__(self, win):
        self.question = visual.TextStim(win, font="Arial", color="white", height=72,
                                       pos=(0, 0), autoLog=False)
        self.answer = visual.TextStim(win, font="Yu Gothic", color="white", height=42,
                                     pos=(0, -win.size[1] * .16), autoLog=False)
        self.last_answer = None

    def set_answer(self, text):
        if text != self.last_answer:
            self.answer.text = "答え: " + (text or "_")
            self.last_answer = text

    def draw(self):
        self.question.draw()
        self.answer.draw()


class OperatorDisplay:
    def __init__(self, win, assignment):
        self.win, self.assignment = win, assignment
        self.gaze_frequency = None
        self.stage, self.detail, self.normal_detail = "準備中", "", ""
        self.trial, self.block, self.completed, self.correct = 0, 0, 0, 0
        self.current_question, self.input_text = "-", ""
        self.rest_remaining, self.last_result = None, None
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
        result = "直近の完了試行: なし"
        if self.last_result is not None:
            last = self.last_result
            first = "-" if last["first_input_rt_seconds"] is None else "{:.3f}秒".format(last["first_input_rt_seconds"])
            edit = "-" if last["last_edit_rt_seconds"] is None else "{:.3f}秒".format(last["last_edit_rt_seconds"])
            result = "直近 {}: {} / {}\n最初の入力RT: {}    最終編集RT: {}".format(
                last["trial"], last["answer_text"] or "未回答",
                "正解" if last["correct"] else "不正解", first, edit)
        a = self.assignment
        body = ("段階: {}\n協力者ID: {}    暗算: {}\n"
                "実験回数（セット）: {}/3    難易度番号: {}\n"
                "1問{}秒    繰り上がり有・無: 各{}問/ブロック\n"
                "取得周波数: {} Hz\n"
                "ブロック: {}/{}    現在の問題: {}/{}\n"
                "完了: {}/{}    セット経過: {}秒    休憩残り: {}\n"
                "現在の式: {}\n入力（未確定）: {}\n{}\n累積正答率: {}\n"
                "操作：スペースキー（SPACE）で準備完了／エスケープキー（ESC）で中断").format(
                    self.stage, a.participant_id, a.condition_label,
                    a.session_number, a.difficulty_level, TRIAL_SECONDS, N_TRIALS // 2,
                    self.gaze_frequency if self.gaze_frequency is not None else "未確認",
                    self.block, N_BLOCKS, self.trial, N_TRIALS, self.completed, N_TRIALS * N_BLOCKS,
                    int(elapsed), "-" if self.rest_remaining is None else str(self.rest_remaining) + "秒",
                    self.current_question, self.input_text or "未入力", result, accuracy)
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
        self.last_update, self.last_text = self.clock.getTime(), text
        return True


def run_arithmetic_blocks(win, cross, stimulus, kb, operator, designs, trials, inputs,
                          protocol, stamp, checkpoint):
    clock = core.Clock()
    sync = BlockSyncDisplay(operator, stamp, clock)
    sync.prepare(win, cross, kb)
    active = N_TRIALS * TRIAL_SECONDS
    block_duration = active + REST_SECONDS
    tolerance = (win.monitorFramePeriod or 1 / 60) * .5

    def poll(record, state, update_view=True):
        keys = kb.getKeys(keyList=INPUT_KEYS, waitRelease=False, clear=True)
        for key in sorted(keys, key=lambda key: float(key.rt)):
            if key.name == "escape":
                raise ExperimentAborted("ESC pressed")
            action = state.edit(key.name, float(key.rt))
            if action is not None:
                record.update(state.snapshot(record["correct_answer"]))
                if update_view:
                    operator.input_text = state.text
                    stimulus.set_answer(state.text)
                us = stamp("input_edit", record["trial"], key.name + ":" + state.text, block=record["block"])
                inputs.append([record["trial"], record["block"], record["trial_in_block"],
                               key.name, action, state.text, round(float(key.rt), 6), us])

    def wait_until(deadline, record=None, state=None):
        while clock.getTime() < deadline - tolerance:
            if record is None:
                check_escape(kb)
                operator.rest_remaining = max(0, int(math.ceil(deadline - clock.getTime())))
                cross.draw()
            else:
                poll(record, state)
                stimulus.draw()
            win.flip()
            sync.update()
        if record is None:
            check_escape(kb)
        else:
            poll(record, state)

    def finish_trial(record, state):
        # Drain timestamped presses before resetting the next trial's keyboard
        # clock. A press just before the deadline may arrive in this frame.
        poll(record, state, update_view=False)
        end = stamp("answer_auto_submit", record["trial"], record["answer_text"], block=record["block"])
        record.update(stim_off_us=end, response_window_end_us=end, auto_submit_us=end,
                      completed=1, correct=int(record["answer_value"] == record["correct_answer"]))
        stamp("stim_off", record["trial"], record["stimulus"], block=record["block"], timestamp=end)
        stamp("response_window_end", record["trial"], record["stimulus"], block=record["block"], timestamp=end)
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
        operator.block, operator.stage, operator.rest_remaining = block, "課題実行中", None
        win.frameIntervals, win.nDroppedFrames = [], 0
        win.recordFrameIntervals = True
        previous_record, previous_state = None, None
        for local_trial, plan in enumerate(design, 1):
            onset = start + (local_trial - 1) * TRIAL_SECONDS
            state = ArithmeticInput(TRIAL_SECONDS, MAX_INPUT_DIGITS)
            record = dict(plan)
            record.update(state.snapshot(record["correct_answer"]))
            record.update(trial=(block - 1) * N_TRIALS + local_trial, block=block,
                          trial_in_block=local_trial, stim_on_us="", stim_off_us="",
                          response_window_end_us="", auto_submit_us="", input_deadline_rt_seconds="",
                          planned_onset_seconds=onset, onset_error_seconds="")
            trials.append(record)
            if previous_record is not None:
                win.callOnFlip(finish_trial, previous_record, previous_state)
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
            operator.trial, operator.current_question, operator.input_text = local_trial, record["stimulus"], ""
            operator.normal_detail = "5秒間、入力・修正できます"
            stimulus.question.text = record["stimulus"]
            stimulus.set_answer("")
            stimulus.draw()
            win.callOnFlip(kb.clock.reset)
            win.callOnFlip(kb.clearEvents)
            win.callOnFlip(stamp, "stim_on", record["trial"], record["stimulus"], record, "stim_on_us")

            def set_input_deadline(record=record, state=state, onset=onset):
                error = clock.getTime() - onset
                state.duration_seconds = max(0, TRIAL_SECONDS - error)
                record.update(onset_error_seconds=error, input_deadline_rt_seconds=state.duration_seconds)

            win.callOnFlip(set_input_deadline)
            win.flip()
            sync.update()
            if local_trial == 1 and block > 1:
                checkpoint()
            wait_until(onset + TRIAL_SECONDS, record, state)
            previous_record = record
            previous_state = state
        operator.stage, operator.normal_detail = "休憩", "休憩してください"
        operator.rest_remaining = int(math.ceil(REST_SECONDS))
        cross.draw()
        win.callOnFlip(finish_trial, previous_record, previous_state)
        win.callOnFlip(lambda summary=summary, block=block: summary.update(
            task_end_us=stamp("task_end", 0, "", block=block),
            rest_start_us=stamp("rest_start", 0, "", block=block)))
        win.flip()
        win.recordFrameIntervals = False
        summary["frame_timing"] = frame_timing_summary(win)
        sync.update()
        checkpoint()
        wait_until(start + block_duration)
        previous_summary = summary
    cross.draw()
    win.callOnFlip(finish_rest, previous_summary)
    win.callOnFlip(lambda: protocol.update(set_end_us=stamp("set_end", 0, "")))
    win.callOnFlip(operator.finish_task)
    win.flip()
    operator.stage, operator.rest_remaining = "記録終了", 0
    operator.normal_detail = "Raw TLXを準備しています"
    sync.update()
    operator.render(force=True)
    checkpoint()


def main(participant_id=None, session_number=None):
    if participant_id is None and session_number is None:
        assignment = prompt_arithmetic_assignment(COUNTERBALANCE_CSV, blocks=N_BLOCKS)
        if assignment is None:
            return
    elif participant_id is None or session_number is None:
        raise ValueError("Specify both participant_id and session_number")
    else:
        assignment = load_arithmetic_assignment(COUNTERBALANCE_CSV, participant_id, session_number)
    subject, difficulty = assignment.participant_id, assignment.difficulty_level
    if difficulty not in OPERAND_RANGES or type(N_TRIALS) is not int or N_TRIALS < 2 or N_TRIALS % 2:
        raise ValueError("Use difficulty 1..3 and an even positive question count")
    if difficulty == 3 and N_TRIALS % 4:
        raise ValueError("Difficulty 3 requires a question count that is a multiple of 4")
    max_answer = sum(upper for _, upper in OPERAND_RANGES[difficulty])
    if type(MAX_INPUT_DIGITS) is not int or MAX_INPUT_DIGITS < len(str(max_answer)):
        raise ValueError("MAX_INPUT_DIGITS is too small for this difficulty")
    if QUEUE_CAPACITY < 2:
        raise ValueError("QUEUE_CAPACITY must be >= 2")
    if type(STARTUP_DIAGNOSTICS) is not bool:
        raise ValueError("STARTUP_DIAGNOSTICS must be a bool")
    if not math.isfinite(STARTUP_TRACEBACK_SECONDS) or STARTUP_TRACEBACK_SECONDS <= 0:
        raise ValueError("STARTUP_TRACEBACK_SECONDS must be finite and positive")
    if type(SYNC_COUNTDOWN_SECONDS) is not int or SYNC_COUNTDOWN_SECONDS < 1:
        raise ValueError("SYNC_COUNTDOWN_SECONDS must be a positive integer")
    if not all(math.isfinite(value) and value > 0
               for value in (SYNC_CUE_SECONDS, OPERATOR_REFRESH_HZ, REST_SECONDS,
                             TRIAL_SECONDS)):
        raise ValueError("Timing and refresh settings must be finite and positive")
    if type(N_BLOCKS) is not int or N_BLOCKS < 1 or type(N_TRIALS) is not int:
        raise ValueError("N_BLOCKS and N_TRIALS must be positive integers")
    active_seconds = N_TRIALS * TRIAL_SECONDS
    if min(active_seconds, REST_SECONDS) < SYNC_COUNTDOWN_SECONDS + SYNC_CUE_SECONDS:
        raise ValueError("Task and rest must accommodate the countdown and marker cue")
    validate_screen_configuration()

    rng_seed = SEED if SEED is not None else random.SystemRandom().randrange(2 ** 32)
    seed_rng = random.Random(rng_seed)
    block_seeds = [seed_rng.randrange(2 ** 32) for _ in range(N_BLOCKS)]
    designs = [make_arithmetic_design(difficulty, N_TRIALS, seed) for seed in block_seeds]
    base = create_output_prefix(OUTPUT_DIR, "arithmetic", subject,
                                assignment.session_number, assignment.difficulty_level)
    recorder = StreamRecorder(base)
    events, trials, inputs = [], [], []
    tracker, win, operator_win, operator = None, None, None, None
    status, err = "failed", None
    cal_result, device_info, size, operator_size = None, {}, None, None
    participant_frame_timing = None
    cleanup_errors = []
    protocol = {"blocks": [], "completed_blocks": 0, "set_start_us": None, "set_end_us": None}
    tlx = RawTLXSession(Path(f"{base}_tlx.csv"), subject, None, N_BLOCKS,
                        session_number=assignment.session_number,
                        difficulty_level=assignment.difficulty_level, task="arithmetic")
    recorder_closed, unsubscribed, gaze_started = False, False, False

    def checkpoint():
        save_csv(Path(f"{base}_events.csv"), EVENT_COLUMNS, events)
        save_csv(Path(f"{base}_trials.csv"), TRIAL_COLUMNS,
                 [[row.get(key, "") for key in TRIAL_COLUMNS] for row in trials])
        save_csv(Path(f"{base}_input.csv"), INPUT_COLUMNS, inputs)

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
        operator.detail = "準備ができたらスペースキー（SPACE）を押してください"
        if stage == "AFTER_TLX":
            operator.stage = "完了"
            operator.detail = "Raw TLX {:.1f} 保存済み。スペースキー（SPACE）で終了".format(tlx.score)
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

    def stamp(event_name, trial_no, stimulus, record=None, field=None, block=None, timestamp=None):
        us = tr.get_system_time_stamp() if timestamp is None else timestamp
        if block is None:
            block = record.get("block", 0) if record is not None else 0
        events.append([event_name, trial_no, stimulus, us, block])
        if record is not None and field is not None:
            record[field] = us
        return us

    def calibration_progress(message):
        progress(message)
        operator.stage = "キャリブレーション"
        operator.detail = runtime.calibration_message_ja(message)
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

    startup_traceback_path = Path(f"{base}_startup_traceback.log")
    startup = StartupDiagnostics(progress, startup_traceback_path,
                                 timeout_seconds=STARTUP_TRACEBACK_SECONDS,
                                 enabled=STARTUP_DIAGNOSTICS)
    try:
        progress("STARTUP ENVIRONMENT " + json.dumps({
            "script": str(Path(__file__).resolve()), "python_executable": sys.executable,
            "python_version": sys.version, "psychopy_version": getattr(psychopy, "__version__", None),
            "psychopy_file": getattr(psychopy, "__file__", None),
            "participant_screen": SCREEN_INDEX, "operator_screen": OPERATOR_SCREEN_INDEX,
            "operator_window_size": OPERATOR_WINDOW_SIZE,
            "participant_wait_blanking": True, "operator_wait_blanking": False,
            "startup_diagnostics": STARTUP_DIAGNOSTICS,
            "startup_traceback_seconds": STARTUP_TRACEBACK_SECONDS,
        }, ensure_ascii=False))
        with startup.step("OPERATOR_WINDOW"):
            operator_win = visual.Window(
                size=OPERATOR_WINDOW_SIZE, fullscr=False, screen=OPERATOR_SCREEN_INDEX,
                units="pix", color="black", waitBlanking=False, checkTiming=False,
                allowGUI=True, winType="pyglet", autoLog=False)
            operator_size = [int(x) for x in operator_win.size]
        with startup.step("OPERATOR_STIMULI"):
            operator = OperatorDisplay(operator_win, assignment)
            operator.detail = "被験者画面を準備しています"
        with startup.step("OPERATOR_FIRST_FLIP"):
            operator.render(force=True)
        # Draw the operator first; create the participant window last for focus.
        with startup.step("PARTICIPANT_WINDOW"):
            win = visual.Window(fullscr=True, screen=SCREEN_INDEX, units="pix",
                                color="black", waitBlanking=True, allowGUI=False,
                                winType="pyglet")
            size = [int(x) for x in win.size]
        with startup.step("KEYBOARD"):
            kb = keyboard.Keyboard()
        with startup.step("PARTICIPANT_STIMULI"):
            message = visual.TextStim(win, font="Yu Gothic", color="white", height=30, wrapWidth=900)
            stimulus = ArithmeticStimulus(win)
            cross = visual.TextStim(win, text="+", color="white", height=48)
        with startup.step("PARTICIPANT_FIRST_FLIP"):
            cross.draw()
            win.flip()
        with startup.step("KEYBOARD_INITIAL_POLL"):
            check_escape(kb)
        with startup.step("OPERATOR_READY_FLIP"):
            operator.detail = "Tobii接続を確認しています"
            operator.render(force=True)
        # End the watchdog before SDK calls, calibration or participant waits.
        startup.close()
        progress("STARTUP COMPLETE")
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
            message.text = runtime.CALIBRATION_INSTRUCTIONS_JA
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

        run_arithmetic_blocks(win, cross, stimulus, kb, operator, designs, trials, inputs,
                              protocol, stamp, checkpoint)
        unsubscribe()
        close_recorder()
        checkpoint()
        if recorder.writer_error:
            raise RuntimeError("Recording failed: " + recorder.writer_error)
        kb.clearEvents()
        run_raw_tlx(win, operator, tlx,
                    lambda name, detail: stamp(name, 0, detail), lambda: check_escape(kb))
        message.font = "Yu Gothic"
        message.text = "回答を保存しました。\nご協力ありがとうございました。\n\nスペースキー（SPACE）で終了します。"
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
        cleanup_step("startup diagnostics close", startup.close)
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
            "task": "arithmetic", "session_number": assignment.session_number,
            "difficulty_level": assignment.difficulty_level,
            "counterbalance_csv": str(COUNTERBALANCE_CSV),
            "output_directory": str(base.parent), "output_prefix": str(base),
            "counterbalance_column": assignment.column,
            "operand_digit_pair": assignment.operand_digit_pair,
            "operand_ranges": OPERAND_RANGES[difficulty],
            "carry_questions_per_block": N_TRIALS // 2,
            "no_carry_questions_per_block": N_TRIALS // 2,
            "max_input_digits": MAX_INPUT_DIGITS,
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
            "trial_seconds": TRIAL_SECONDS, "stimulus_seconds": TRIAL_SECONDS, "isi_seconds": 0,
            "seed": rng_seed, "answer_mode": "auto_submit_at_deadline",
            "input_keys": INPUT_KEYS, "input_event_count": len(inputs),
            "question_pos": [0, 0],
            "answer_pos": [0, -size[1] * .16] if size else None,
            "screen_index": SCREEN_INDEX, "screen_pixels": size,
            "participant_wait_blanking": True,
            "operator_screen_index": OPERATOR_SCREEN_INDEX,
            "operator_screen_pixels": operator_size,
            "operator_fullscreen": False,
            "operator_wait_blanking": False,
            "operator_check_timing": False,
            "startup_diagnostics_enabled": STARTUP_DIAGNOSTICS,
            "startup_traceback_seconds": STARTUP_TRACEBACK_SECONDS,
            "startup_traceback_path": str(startup_traceback_path) if STARTUP_DIAGNOSTICS else None,
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
