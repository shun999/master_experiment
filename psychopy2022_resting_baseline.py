# -*- coding: utf-8 -*-
"""Five-minute resting fixation and operator Lap recorder for PsychoPy 2022.2.4."""

DURATION_SECONDS = 300.0
SCREEN_INDEX = 1
OPERATOR_SCREEN_INDEX = 0
OPERATOR_WINDOW_SIZE = (960, 640)
OPERATOR_REFRESH_HZ = 5.0
STARTUP_DIAGNOSTICS = True
STARTUP_TRACEBACK_SECONDS = 20.0

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.resting_baseline import run_resting_baseline

OUTPUT_DIR = PROJECT_ROOT / "data"


def main(participant_id=None, session_number=None):
    return run_resting_baseline(
        participant_id, session_number, output_root=OUTPUT_DIR,
        duration_seconds=DURATION_SECONDS, screen_index=SCREEN_INDEX,
        operator_screen_index=OPERATOR_SCREEN_INDEX, operator_window_size=OPERATOR_WINDOW_SIZE,
        operator_refresh_hz=OPERATOR_REFRESH_HZ, startup_diagnostics=STARTUP_DIAGNOSTICS,
        startup_traceback_seconds=STARTUP_TRACEBACK_SECONDS)


if __name__ == "__main__":
    main()
