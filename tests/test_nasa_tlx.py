# -*- coding: utf-8 -*-
"""Raw TLX storage and mouse-UI tests without PsychoPy or hardware."""

import csv
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from src.nasa_tlx import RawTLXSession, SCALE_KEYS, SCALES, run_raw_tlx
from tests.test_nback_dual_screen import Rig, Text


class MouseUI:
    def __init__(self, plans):
        self.plans = plans
        self.page = -1
        self.draws = 0
        self.visible = False
        self.page_draws = []

    @property
    def plan(self):
        return self.plans[self.page]

    def mouse(self, win):
        return types.SimpleNamespace(setVisible=self.set_visible, setPos=lambda pos: None,
                                     getPressed=self.pressed)

    def set_visible(self, visible):
        self.visible = visible

    def pressed(self):
        # Every page starts with a held previous click. Only the release and
        # subsequent press may navigate. An unanswered page also gets an early click.
        clicks = (1, 3, 6) if self.plan.get('ready', 1) > 3 else (1, 3)
        return (self.draws in clicks, False, False)

    def slider(self, win, **settings):
        ui = self

        class Slider:
            def reset(self):
                if ui.page >= 0:
                    ui.page_draws.append(ui.draws)
                ui.page += 1
                ui.draws = 0
                self.restored = None
            def draw(self):
                ui.draws += 1
            def getRating(self):
                if ui.draws < ui.plan.get('ready', 1):
                    return None
                return ui.plan['rating']
            def recordRating(self, value, rt=0, log=False):
                self.restored = value

        return Slider()

    def rect(self, win, **settings):
        rectangle = Text(win, **settings)
        rectangle.pos = settings['pos']
        rectangle.contains = lambda mouse: (
            (rectangle.pos[0] > 0 and self.plan.get('action', 'next') == 'next')
            or (rectangle.pos[0] < 0 and self.plan.get('action') == 'back'))
        return rectangle

    def run(self, session, abort_after=None):
        rig = Rig()
        win = rig.window(screen=1, waitBlanking=True)
        operator = types.SimpleNamespace(stage='', detail='', rest_remaining=None, render=lambda: None)
        psychopy = types.ModuleType('psychopy')
        psychopy.visual = types.SimpleNamespace(TextStim=Text, Rect=self.rect, Slider=self.slider)
        psychopy.event = types.SimpleNamespace(Mouse=self.mouse)
        events = []
        def stamp(name, detail):
            events.append((name, detail))
            return int(rig.now * 1e6)
        def check_abort():
            if abort_after is not None and self.page >= abort_after and self.draws >= 2:
                raise RuntimeError('simulated abort')
        with patch.dict(sys.modules, {'psychopy': psychopy}):
            score = run_raw_tlx(win, operator, session, stamp, check_abort)
        return score, events


class RawTLXTests(unittest.TestCase):
    def test_extremes_and_raw_mean(self):
        for values, expected in [([0] * 6, 0), ([100] * 6, 100), ([0, 20, 40, 60, 80, 100], 50)]:
            with self.subTest(values=values), tempfile.TemporaryDirectory() as root:
                session = RawTLXSession(Path(root) / 'tlx.csv', 'AFX', 2, 6)
                ui = MouseUI([{'rating': value} for value in values])
                score, events = ui.run(session)
                self.assertEqual(score, expected)
                self.assertEqual(session.status, 'completed')
                self.assertFalse(ui.visible)
                self.assertEqual(sum(name == 'tlx_response' for name, _ in events), 6)
                self.assertEqual(events[0][0], 'tlx_start')
                self.assertEqual(events[-1][0], 'tlx_end')
                with session.path.open(encoding='utf-8-sig') as stream:
                    row = next(csv.DictReader(stream))
                self.assertEqual(float(row['raw_tlx']), expected)
                self.assertEqual(row['participant_id'], 'AFX')
                self.assertEqual(row['n_back'], '2')
                self.assertFalse(session.path.with_name('tlx.csv.tmp').exists())
        self.assertIn('完全に達成', SCALES[3][3])
        self.assertIn('達成できなかった', SCALES[3][4])

    def test_missing_rating_and_held_click_do_not_advance(self):
        with tempfile.TemporaryDirectory() as root:
            session = RawTLXSession(Path(root) / 'tlx.csv', 'BKR', 1, 6)
            ui = MouseUI([{'rating': 0, 'ready': 5}] + [{'rating': 50}] * 5)
            score, events = ui.run(session)
        self.assertGreaterEqual(ui.page_draws[0], 6)
        self.assertEqual(score, 250 / 6)

    def test_back_and_revision(self):
        plans = [{'rating': 20}, {'rating': 40}, {'rating': 60, 'action': 'back'},
                 {'rating': 80}, {'rating': 100}, {'rating': 0}, {'rating': 5}, {'rating': 100}]
        with tempfile.TemporaryDirectory() as root:
            session = RawTLXSession(Path(root) / 'tlx.csv', 'CMT', 3, 6)
            ui = MouseUI(plans)
            score, events = ui.run(session)
        self.assertEqual(session.ratings['physical_demand'], 80)
        self.assertEqual(score, (20 + 80 + 100 + 0 + 5 + 100) / 6)
        self.assertEqual(sum(name == 'tlx_response' for name, _ in events), 7)

    def test_abort_saves_partial_answers_without_score(self):
        with tempfile.TemporaryDirectory() as root:
            session = RawTLXSession(Path(root) / 'tlx.csv', 'DNV', 4, 6)
            ui = MouseUI([{'rating': 35}] * 6)
            with self.assertRaisesRegex(RuntimeError, 'abort'):
                ui.run(session, abort_after=2)
            self.assertFalse(ui.visible)
            self.assertEqual(session.status, 'aborted')
            self.assertIsNone(session.score)
            with session.path.open(encoding='utf-8-sig') as stream:
                row = next(csv.DictReader(stream))
            self.assertEqual(row['physical_demand'], '35')
            self.assertEqual(row['temporal_demand'], '')
            self.assertEqual(row['raw_tlx'], '')
            self.assertEqual(row['completed_us'], '')

    def test_validation_and_persistence_before_completion(self):
        with tempfile.TemporaryDirectory() as root:
            session = RawTLXSession(Path(root) / 'tlx.csv', 'EPQ', 1, 6)
            session.start(100)
            for value in (-5, 105, 3, 12.5, True, float('nan')):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    session.set_rating(SCALE_KEYS[0], value)
            with self.assertRaises(ValueError):
                session.finish(200)
            session.set_rating('mental_demand', 0)
            with session.path.open(encoding='utf-8-sig') as stream:
                row = next(csv.DictReader(stream))
            self.assertEqual(row['mental_demand'], '0')
            self.assertEqual(row['physical_demand'], '')
            self.assertEqual(row['raw_tlx'], '')


if __name__ == '__main__':
    unittest.main()
