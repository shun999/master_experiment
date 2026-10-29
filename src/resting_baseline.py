# -*- coding: utf-8 -*-
"""Resting fixation and durable, timestamped Laps. No device SDK is required."""

import csv
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sys

from .experiment_assignment import normalize_participant_id
from .experiment_paths import create_rest_output_prefix
from .experiment_runtime import validate_screen_configuration
from .startup_diagnostics import StartupDiagnostics


LAP_COLUMNS = ["lap_number", "elapsed_seconds", "lap_seconds", "timestamp_iso"]
INPUT_KEYS = ["return", "num_enter", "space", "escape"]


def validate_rest_assignment(participant_id, session_number):
    participant_id = normalize_participant_id(participant_id)
    if type(session_number) is not int or session_number < 1:
        raise ValueError("安静時測定の回数は1以上の整数で入力してください。")
    return participant_id, session_number


def prompt_rest_assignment(gui):
    participant_id, session_text, error = "", "1", ""
    while True:
        dialog = gui.Dlg(title="安静時測定")
        dialog.addText("安静時測定の回数は、他の課題とは独立して数えます。")
        if error:
            dialog.addText(error)
        dialog.addField("被験者ID（英字3文字）", initial=participant_id)
        dialog.addField("安静時測定の回数（1以上）", initial=session_text)
        values = dialog.show()
        if not dialog.OK:
            return None
        participant_id, session_text = values
        try:
            text = str(session_text).strip()
            if not text or any(char not in "0123456789" for char in text):
                raise ValueError("安静時測定の回数は1以上の整数で入力してください。")
            return validate_rest_assignment(participant_id, int(session_text))
        except ValueError as exc:
            error = str(exc)


def format_elapsed(seconds):
    ticks = max(0, int(round(seconds * 10)))
    minutes, tenths = divmod(ticks, 600)
    return "{:02d}:{:04.1f}".format(minutes, tenths / 10)


class RestSession:
    def __init__(self, duration_seconds):
        if (isinstance(duration_seconds, bool) or not math.isfinite(duration_seconds)
                or duration_seconds <= 0):
            raise ValueError("測定時間は正の有限値で指定してください。")
        self.duration_seconds = duration_seconds
        self.status = "ready"
        self.started_at = self.ended_at = None
        self.actual_elapsed_seconds = 0.0
        self.laps = []
        self._last_lap_rt = None

    def start(self, wall_time):
        if self.status != "ready":
            raise RuntimeError("Resting measurement has already started")
        self.started_at = wall_time
        self.status = "running"

    def add_lap(self, rt):
        if (self.status != "running" or not math.isfinite(rt) or not 0 <= rt < self.duration_seconds
                or (self._last_lap_rt is not None and rt <= self._last_lap_rt)):
            return None
        row = dict(lap_number=len(self.laps) + 1, elapsed_seconds=round(rt, 6),
                   lap_seconds=round(rt - (self._last_lap_rt or 0), 6),
                   timestamp_iso=(self.started_at + timedelta(seconds=rt)).isoformat(timespec="microseconds"))
        self.laps.append(row)
        self._last_lap_rt = rt
        return row

    def finish(self, status, elapsed_seconds):
        self.status = status
        self.actual_elapsed_seconds = round(max(0, elapsed_seconds), 6) if self.started_at else 0.0
        self.ended_at = (self.started_at + timedelta(seconds=self.actual_elapsed_seconds)
                         if self.started_at else datetime.now(timezone.utc))


class RestAborted(Exception):
    def __init__(self, elapsed_seconds=0.0):
        self.elapsed_seconds = elapsed_seconds


class RestOperator:
    def __init__(self, win, core, visual, participant_id, session_number, session, refresh_hz):
        self.win, self.session, self.refresh_hz = win, session, refresh_hz
        self.participant_id, self.session_number = participant_id, session_number
        self.clock, self.last_update, self.last_text = core.Clock(), float("-inf"), None
        width, height = win.size
        self.banner = visual.TextStim(win, font="Yu Gothic", color="white", height=height * .045,
                                     pos=(0, height * .40), wrapWidth=width * .92, autoLog=False)
        self.body = visual.TextStim(win, font="Yu Gothic", color="white", height=height * .029,
                                   pos=(-width * .45, height * .29), wrapWidth=width * .90,
                                   alignText="left", anchorHoriz="left", anchorVert="top", autoLog=False)

    def render(self, elapsed=0.0, notice=None, force=False):
        now = self.clock.getTime()
        if not force and now - self.last_update < 1 / self.refresh_hz:
            return False
        state = self.session.status
        label = {"ready": "準備完了", "running": "測定中", "completed": "測定終了",
                 "aborted": "中断", "failed": "エラー"}[state]
        elapsed = min(elapsed, self.session.duration_seconds)
        recent = ["Lap {}　経過 {}　区間 {}".format(
            row["lap_number"], format_elapsed(row["elapsed_seconds"]), format_elapsed(row["lap_seconds"]))
            for row in self.session.laps[-5:]]
        if state == "ready":
            controls = "エンターキー（Enter）：開始\nエスケープキー（ESC）：中断・終了"
        elif state == "running":
            controls = "スペースキー（SPACE）：Lapを記録\nエスケープキー（ESC）：中断・保存して終了"
        else:
            controls = "エンターキー（Enter）／エスケープキー（ESC）：画面を閉じる"
        text = (notice or "安静時測定：" + label,
                "被験者ID：{}　安静時測定の回数：{}\n\n経過時間：{}　予定時間：{}\n"
                "Lap総数：{}\n\n直近5件のLap\n{}\n\n{}".format(
                    self.participant_id, self.session_number, format_elapsed(elapsed),
                    format_elapsed(self.session.duration_seconds), len(self.session.laps),
                    "\n".join(recent) or "まだ記録はありません", controls))
        if text != self.last_text:
            self.banner.text, self.body.text = text
        self.banner.draw()
        self.body.draw()
        self.win.flip()
        self.last_update, self.last_text = self.clock.getTime(), text
        return True


def run_resting_baseline(participant_id=None, session_number=None, *, output_root,
                         duration_seconds=300.0, screen_index=1, operator_screen_index=0,
                         operator_window_size=(960, 640), operator_refresh_hz=5.0,
                         startup_diagnostics=True, startup_traceback_seconds=20.0):
    session = RestSession(duration_seconds)
    if (not math.isfinite(operator_refresh_hz) or operator_refresh_hz <= 0
            or not math.isfinite(startup_traceback_seconds) or startup_traceback_seconds <= 0
            or type(startup_diagnostics) is not bool):
        raise ValueError("更新頻度・起動診断の設定を確認してください。")
    if (len(operator_window_size) != 2
            or any(type(value) is not int or value < 1 for value in operator_window_size)):
        raise ValueError("実施者ウィンドウのサイズは正の整数2個で指定してください。")
    from psychopy import core, visual
    from psychopy.hardware import keyboard
    import psychopy

    if participant_id is None and session_number is None:
        from psychopy import gui
        assignment = prompt_rest_assignment(gui)
        if assignment is None:
            return None
        participant_id, session_number = assignment
    else:
        participant_id, session_number = validate_rest_assignment(participant_id, session_number)
    validate_screen_configuration(screen_index, operator_screen_index)
    base = create_rest_output_prefix(output_root, participant_id, session_number)
    operator_win = win = kb = startup = lap_stream = log_stream = None
    cleanup_errors, error = [], None
    screen_pixels = operator_screen_pixels = None

    def progress(message):
        line = "{} | {}".format(datetime.now(timezone.utc).isoformat(timespec="seconds"), message)
        print(line, flush=True)
        if log_stream is not None:
            log_stream.write(line + "\n")
            log_stream.flush()

    def elapsed():
        return kb.clock.getTime() if kb is not None and session.started_at is not None else 0.0

    def checkpoint():
        metadata = dict(task="rest", participant=participant_id, session_number=session_number,
                        status=session.status, error=error, cleanup_errors=cleanup_errors,
                        planned_duration_seconds=duration_seconds,
                        actual_elapsed_seconds=session.actual_elapsed_seconds if session.status != "running" else elapsed(),
                        started_at_iso=session.started_at.isoformat(timespec="microseconds") if session.started_at else None,
                        ended_at_iso=session.ended_at.isoformat(timespec="microseconds") if session.ended_at else None,
                        lap_count=len(session.laps), output_directory=str(base.parent), output_prefix=str(base),
                        screen_index=screen_index, screen_pixels=screen_pixels, participant_wait_blanking=True,
                        operator_screen_index=operator_screen_index, operator_screen_pixels=operator_screen_pixels,
                        operator_fullscreen=False, operator_wait_blanking=False, operator_refresh_hz=operator_refresh_hz,
                        fixation=dict(text="+", color="white", height=48, pos=[0, 0], units="pix", background="black"),
                        psychopy_version=getattr(psychopy, "__version__", None), python_version=sys.version,
                        startup_diagnostics=startup_diagnostics, startup_traceback_seconds=startup_traceback_seconds,
                        timestamp_note="Lap RT uses the keyboard clock reset on the measurement-start flip. "
                                       "ISO timestamps are start UTC plus elapsed time; no external device timestamps.")
        path = Path(str(base) + "_metadata.json")
        temporary = Path(str(path) + ".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(metadata, stream, ensure_ascii=False, indent=2)
        temporary.replace(path)
        return metadata

    def keys():
        return sorted(kb.getKeys(keyList=INPUT_KEYS, waitRelease=False, clear=True), key=lambda key: float(key.rt))

    try:
        log_stream = Path(str(base) + "_progress.log").open("w", encoding="utf-8")
        lap_stream = Path(str(base) + "_laps.csv").open("w", encoding="utf-8-sig", newline="")
        writer = csv.DictWriter(lap_stream, fieldnames=LAP_COLUMNS)
        writer.writeheader()
        lap_stream.flush()
        checkpoint()
        progress("OUTPUT PREFIX: {}".format(base))
        progress("STARTUP ENVIRONMENT " + json.dumps(dict(module=str(Path(__file__).resolve()),
                 python_executable=sys.executable, python_version=sys.version,
                 psychopy_version=getattr(psychopy, "__version__", None),
                 psychopy_file=getattr(psychopy, "__file__", None),
                 participant_screen=screen_index, operator_screen=operator_screen_index), ensure_ascii=False))
        startup = StartupDiagnostics(progress, Path(str(base) + "_startup_traceback.log"),
                                     timeout_seconds=startup_traceback_seconds, enabled=startup_diagnostics)
        with startup.step("OPERATOR_WINDOW"):
            operator_win = visual.Window(size=operator_window_size, fullscr=False, screen=operator_screen_index,
                                         units="pix", color="black", waitBlanking=False, checkTiming=False,
                                         allowGUI=True, winType="pyglet", autoLog=False)
            operator_screen_pixels = [int(value) for value in operator_win.size]
        with startup.step("OPERATOR_FIRST_FLIP"):
            operator = RestOperator(operator_win, core, visual, participant_id, session_number, session, operator_refresh_hz)
            operator.render(notice="被験者画面を準備しています", force=True)
        with startup.step("PARTICIPANT_WINDOW"):
            win = visual.Window(fullscr=True, screen=screen_index, units="pix", color="black",
                                waitBlanking=True, allowGUI=False, winType="pyglet", autoLog=False)
            screen_pixels = [int(value) for value in win.size]
        with startup.step("PARTICIPANT_FIRST_FLIP"):
            cross = visual.TextStim(win, text="+", color="white", height=48, pos=(0, 0), autoLog=False)
            cross.draw()
            win.flip()
        with startup.step("KEYBOARD"):
            kb = keyboard.Keyboard()
        with startup.step("KEYBOARD_INITIAL_POLL"):
            if any(key.name == "escape" for key in keys()):
                raise RestAborted()
        startup.close()
        progress("STARTUP COMPLETE")
        operator.render(force=True)

        ready = False
        while not ready:
            for key in keys():
                if key.name == "escape":
                    raise RestAborted()
                if key.name in ("return", "num_enter"):
                    ready = True
            if not ready:
                cross.draw()
                win.flip()
                operator.render()

        def start():
            kb.clock.reset()
            kb.clearEvents()
            session.start(datetime.now(timezone.utc))

        cross.draw()
        win.callOnFlip(start)
        win.flip()
        checkpoint()
        progress("MEASUREMENT START")
        operator.render(force=True)
        while True:
            # Poll before checking the cutoff to retain pre-deadline presses
            # delivered during the final flip. The key RT determines eligibility.
            for key in keys():
                rt = float(key.rt)
                if key.name == "escape" and math.isfinite(rt) and 0 <= rt < duration_seconds:
                    raise RestAborted(rt)
                if key.name == "space":
                    row = session.add_lap(rt)
                    if row is not None:
                        writer.writerow(row)
                        lap_stream.flush()
                        progress("LAP {} elapsed={:.6f}s interval={:.6f}s".format(
                            row["lap_number"], row["elapsed_seconds"], row["lap_seconds"]))
                        checkpoint()
            if elapsed() >= duration_seconds:
                break
            cross.draw()
            win.flip()
            operator.render(elapsed())
        session.finish("completed", elapsed())
        checkpoint()
        progress("MEASUREMENT COMPLETE")
        operator.render(duration_seconds, force=True)
        kb.clearEvents()
        while not any(key.name in ("return", "num_enter", "escape") for key in keys()):
            cross.draw()
            win.flip()
            operator.render(duration_seconds)
    except RestAborted as exc:
        session.finish("aborted", exc.elapsed_seconds)
        progress("MEASUREMENT ABORTED")
    except BaseException as exc:
        error = "{}: {}".format(type(exc).__name__, exc)
        session.finish("failed", elapsed())
        progress("MEASUREMENT ERROR: " + error)
        raise
    finally:
        for label, resource in (("startup", startup), ("participant_window", win),
                                ("operator_window", operator_win), ("laps", lap_stream)):
            if resource is not None:
                try:
                    resource.close()
                except Exception as exc:
                    cleanup_errors.append("{}: {}: {}".format(label, type(exc).__name__, exc))
        try:
            if cleanup_errors and error is None:
                error = "; ".join(cleanup_errors)
                session.status = "failed"
            metadata = checkpoint()
            progress("FINISHED: status={}".format(session.status))
        finally:
            if log_stream is not None:
                log_stream.close()
    if cleanup_errors:
        raise RuntimeError("; ".join(cleanup_errors))
    return metadata
