# -*- coding: utf-8 -*-
"""One-digit addition teaching demo. Run this file in PsychoPy Coder."""

SCREEN_INDEX = 1  # A single connected monitor automatically uses screen 0.
FULLSCREEN = True
WINDOW_SIZE = (1280, 720)

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.demo_runtime import run_demo


def main():
    return run_demo('arithmetic', screen_index=SCREEN_INDEX, full_screen=FULLSCREEN, window_size=WINDOW_SIZE)


if __name__ == '__main__':
    main()
