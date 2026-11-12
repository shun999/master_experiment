# -*- coding: utf-8 -*-
"""Task assignments from the fixed counterbalance CSV (standard library)."""

import csv
from pathlib import Path
import re
from typing import NamedTuple

from .arithmetic_task import OPERAND_DIGIT_PAIRS


NBACK_BY_DIFFICULTY = {1: 0, 2: 1, 3: 2, 4: 3}
NBACK_COLUMNS = tuple("nback_{}".format(i) for i in range(1, 5))


class AssignmentTableError(ValueError):
    """An unusable assignment file; do not fall back to a manual condition."""


class NbackAssignment(NamedTuple):
    participant_id: str
    session_number: int
    difficulty_level: int
    n_back: int

    @property
    def column(self):
        return "nback_{}".format(self.session_number)


class ArithmeticAssignment(NamedTuple):
    participant_id: str
    session_number: int
    difficulty_level: int

    @property
    def column(self):
        return "arithmetic_{}".format(self.session_number)

    @property
    def operand_digit_pair(self):
        return OPERAND_DIGIT_PAIRS[self.difficulty_level]

    @property
    def condition_label(self):
        first, second = self.operand_digit_pair
        if first != second:
            return "{}桁と{}桁・左右ランダム".format(first, second)
        return "{}桁 + {}桁".format(first, second)


class PvsatAssignment(NamedTuple):
    participant_id: str
    session_number: int
    difficulty_level: int

    @property
    def column(self):
        return "pvsat_{}".format(self.session_number)


def normalize_participant_id(value):
    if not isinstance(value, str):
        raise ValueError("被験者IDは英字3文字で入力してください。")
    participant_id = value.strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", participant_id):
        raise ValueError("被験者IDは英字3文字で入力してください。")
    return participant_id


def read_nback_table(csv_path):
    return _read_task_table(csv_path, "nback", 4, "N-back")


def read_arithmetic_table(csv_path):
    return _read_task_table(csv_path, "arithmetic", 3, "暗算")


def read_pvsat_table(csv_path):
    return _read_task_table(csv_path, "pvsat", 3, "PVSAT")


def _read_task_table(csv_path, task, count, label):
    table = {}
    task_columns = tuple("{}_{}".format(task, i) for i in range(1, count + 1))
    required = ("participant_id",) + task_columns
    try:
        with Path(csv_path).open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            columns = reader.fieldnames or []
            if len(set(columns)) != len(columns) or not set(required).issubset(columns):
                raise AssignmentTableError("CSVにparticipant_idと{}_1〜{}_{}の一意な列が必要です。".format(task, task, count))
            for row in reader:
                if None in row:
                    raise AssignmentTableError("CSVの{}行目の列数が不正です。".format(reader.line_num))
                try:
                    participant_id = normalize_participant_id(row["participant_id"])
                    levels = tuple(int(row[column]) for column in task_columns)
                except (ValueError, TypeError) as exc:
                    raise AssignmentTableError("CSVの{}行目のIDまたは難易度が不正です。".format(reader.line_num)) from exc
                if participant_id in table:
                    raise AssignmentTableError("CSVの被験者IDが重複しています: " + participant_id)
                if sorted(levels) != list(range(1, count + 1)):
                    raise AssignmentTableError("{}の{}の難易度には1〜{}を1回ずつ割り当ててください。".format(participant_id, label, count))
                table[participant_id] = levels
    except (OSError, UnicodeError, csv.Error) as exc:
        raise AssignmentTableError("割り当てCSVを読み込めません: {} ({})".format(csv_path, exc)) from exc
    if not table:
        raise AssignmentTableError("割り当てCSVに被験者が登録されていません。")
    return table


def select_nback_assignment(table, participant_id, session_number):
    participant_id, session_number, level = _select_level(table, participant_id, session_number, 4, "N-back")
    return NbackAssignment(participant_id, session_number, level, NBACK_BY_DIFFICULTY[level])


def select_arithmetic_assignment(table, participant_id, session_number):
    return ArithmeticAssignment(*_select_level(table, participant_id, session_number, 3, "暗算"))


def select_pvsat_assignment(table, participant_id, session_number):
    return PvsatAssignment(*_select_level(table, participant_id, session_number, 3, "PVSAT"))


def _select_level(table, participant_id, session_number, count, label):
    participant_id = normalize_participant_id(participant_id)
    if isinstance(session_number, str):
        session_number = session_number.strip()
        if session_number not in tuple(str(i) for i in range(1, count + 1)):
            raise ValueError("{}の実験回数は1〜{}を選択してください。".format(label, count))
        session_number = int(session_number)
    if type(session_number) is not int or not 1 <= session_number <= count:
        raise ValueError("{}の実験回数は1〜{}を選択してください。".format(label, count))
    if participant_id not in table:
        raise ValueError("登録されていない被験者IDです: " + participant_id)
    level = table[participant_id][session_number - 1]
    return participant_id, session_number, level


def load_nback_assignment(csv_path, participant_id, session_number):
    return select_nback_assignment(read_nback_table(csv_path), participant_id, session_number)


def load_arithmetic_assignment(csv_path, participant_id, session_number):
    return select_arithmetic_assignment(read_arithmetic_table(csv_path), participant_id, session_number)


def load_pvsat_assignment(csv_path, participant_id, session_number):
    return select_pvsat_assignment(read_pvsat_table(csv_path), participant_id, session_number)


def prompt_nback_assignment(csv_path):
    """Return an assignment or None; called before windows/recording/files."""
    return _prompt_assignment(csv_path, "N-back", 4, 6, read_nback_table, select_nback_assignment)


def prompt_arithmetic_assignment(csv_path, blocks=3):
    return _prompt_assignment(csv_path, "暗算", 3, blocks, read_arithmetic_table, select_arithmetic_assignment)


def prompt_pvsat_assignment(csv_path, blocks=3):
    return _prompt_assignment(csv_path, "PVSAT", 3, blocks, read_pvsat_table, select_pvsat_assignment)


def _prompt_assignment(csv_path, label, count, blocks, read_table, select_assignment):
    from psychopy import gui

    try:
        table = read_table(csv_path)
    except AssignmentTableError as exc:
        dialog = gui.Dlg(title=label + " 割り当てファイルのエラー")
        dialog.addText(str(exc))
        dialog.show()
        return None
    participant_id, session_number, error = "", "1", ""
    while True:
        dialog = gui.Dlg(title=label + " 実験条件")
        dialog.addText("実験回数1回は、{}ブロックとRaw TLXを含む1セットです。".format(blocks))
        if error:
            dialog.addText(error)
        dialog.addField("被験者ID（英字3文字）", initial=participant_id)
        dialog.addField(label + "の実験回数", initial=session_number, choices=[str(i) for i in range(1, count + 1)])
        values = dialog.show()
        if not dialog.OK:
            return None
        participant_id, session_number = values
        try:
            return select_assignment(table, participant_id, session_number)
        except ValueError as exc:
            error = str(exc)
