# -*- coding: utf-8 -*-
"""N-back assignments from the fixed counterbalance CSV (standard library)."""

import csv
from pathlib import Path
import re
from typing import NamedTuple


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


def normalize_participant_id(value):
    if not isinstance(value, str):
        raise ValueError("被験者IDは英字3文字で入力してください。")
    participant_id = value.strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", participant_id):
        raise ValueError("被験者IDは英字3文字で入力してください。")
    return participant_id


def read_nback_table(csv_path):
    table = {}
    required = ("participant_id",) + NBACK_COLUMNS
    try:
        with Path(csv_path).open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            columns = reader.fieldnames or []
            if len(set(columns)) != len(columns) or not set(required).issubset(columns):
                raise AssignmentTableError("CSVにparticipant_idとnback_1〜nback_4の一意な列が必要です。")
            for row in reader:
                if None in row:
                    raise AssignmentTableError("CSVの{}行目の列数が不正です。".format(reader.line_num))
                try:
                    participant_id = normalize_participant_id(row["participant_id"])
                    levels = tuple(int(row[column]) for column in NBACK_COLUMNS)
                except (ValueError, TypeError) as exc:
                    raise AssignmentTableError("CSVの{}行目のIDまたは難易度が不正です。".format(reader.line_num)) from exc
                if participant_id in table:
                    raise AssignmentTableError("CSVの被験者IDが重複しています: " + participant_id)
                if sorted(levels) != [1, 2, 3, 4]:
                    raise AssignmentTableError("{}のN-backの難易度には1〜4を1回ずつ割り当ててください。".format(participant_id))
                table[participant_id] = levels
    except (OSError, UnicodeError, csv.Error) as exc:
        raise AssignmentTableError("割り当てCSVを読み込めません: {} ({})".format(csv_path, exc)) from exc
    if not table:
        raise AssignmentTableError("割り当てCSVに被験者が登録されていません。")
    return table


def select_nback_assignment(table, participant_id, session_number):
    participant_id = normalize_participant_id(participant_id)
    if isinstance(session_number, str):
        session_number = session_number.strip()
        if session_number not in ("1", "2", "3", "4"):
            raise ValueError("N-backの実験回数は1〜4を選択してください。")
        session_number = int(session_number)
    if type(session_number) is not int or not 1 <= session_number <= 4:
        raise ValueError("N-backの実験回数は1〜4を選択してください。")
    if participant_id not in table:
        raise ValueError("登録されていない被験者IDです: " + participant_id)
    level = table[participant_id][session_number - 1]
    return NbackAssignment(participant_id, session_number, level, NBACK_BY_DIFFICULTY[level])


def load_nback_assignment(csv_path, participant_id, session_number):
    return select_nback_assignment(read_nback_table(csv_path), participant_id, session_number)


def prompt_nback_assignment(csv_path):
    """Return an assignment or None; called before windows/recording/files."""
    from psychopy import gui

    try:
        table = read_nback_table(csv_path)
    except AssignmentTableError as exc:
        dialog = gui.Dlg(title="N-back 割り当てファイルのエラー")
        dialog.addText(str(exc))
        dialog.show()
        return None
    participant_id, session_number, error = "", "1", ""
    while True:
        dialog = gui.Dlg(title="N-back 実験条件")
        dialog.addText("実験回数1回は、6ブロックとRaw TLXを含む1セットです。")
        if error:
            dialog.addText(error)
        dialog.addField("被験者ID（英字3文字）", initial=participant_id)
        dialog.addField("N-backの実験回数", initial=session_number, choices=["1", "2", "3", "4"])
        values = dialog.show()
        if not dialog.OK:
            return None
        participant_id, session_number = values
        try:
            return select_nback_assignment(table, participant_id, session_number)
        except ValueError as exc:
            error = str(exc)
