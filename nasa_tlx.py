# -*- coding: utf-8 -*-
"""Raw TLX: unweighted mean of six ratings; no pairwise comparisons.

Japanese prompts paraphrase the NASA scale definitions:
https://www.nasa.gov/human-systems-integration-division/nasa-task-load-index-tlx/
They are not represented as a validated Japanese translation.
"""

import csv
from pathlib import Path


SCALES = (
    ("mental_demand", "知的・知覚的要求", "考える、判断する、覚えるなど、どの程度の知的・知覚的活動が必要でしたか？", "非常に低い", "非常に高い"),
    ("physical_demand", "身体的要求", "操作などに、どの程度の身体的活動が必要でしたか？", "非常に低い", "非常に高い"),
    ("temporal_demand", "時間的要求", "課題の速さによって、どの程度せかされたり時間的な圧迫を感じたりしましたか？", "非常に低い", "非常に高い"),
    ("performance", "作業成績", "求められた課題を、どの程度うまく達成できましたか？", "完全に達成できた", "全く達成できなかった"),
    ("effort", "努力", "課題の成績を得るために、精神的・身体的にどの程度努力しましたか？", "非常に低い", "非常に高い"),
    ("frustration", "フラストレーション", "課題中に、不安、落胆、いらだち、ストレスなどをどの程度感じましたか？", "非常に低い", "非常に高い"),
)
SCALE_KEYS = tuple(scale[0] for scale in SCALES)


class RawTLXSession:
    def __init__(self, path, participant_id, n_back, blocks, session_number=None, difficulty_level=None):
        self.path = Path(path)
        self.participant_id = participant_id
        self.n_back = n_back
        self.blocks = blocks
        self.session_number = session_number
        self.difficulty_level = difficulty_level
        self.ratings = {key: None for key in SCALE_KEYS}
        self.status = "not_started"
        self.started_us = None
        self.completed_us = None

    @property
    def score(self):
        if self.status != "completed":
            return None
        return sum(self.ratings.values()) / 6.0

    def start(self, timestamp):
        self.started_us = timestamp
        self.status = "in_progress"
        self.save()

    def set_rating(self, key, value):
        if key not in self.ratings:
            raise ValueError("Unknown TLX dimension: " + key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value not in range(0, 101, 5):
            raise ValueError("TLX rating must be 0..100 in steps of 5")
        self.ratings[key] = int(value)
        self.save()

    def finish(self, timestamp):
        if any(value is None for value in self.ratings.values()):
            raise ValueError("All six TLX ratings are required")
        self.completed_us = timestamp
        self.status = "completed"
        self.save()

    def abort(self):
        if self.status == "in_progress":
            self.status = "aborted"
            self.save()

    def save(self):
        row = {"participant_id": self.participant_id, "task": "nback", "n_back": self.n_back,
               "session_number": self.session_number, "difficulty_level": self.difficulty_level,
               "blocks": self.blocks, "status": self.status, "started_us": self.started_us,
               "completed_us": self.completed_us}
        row.update(self.ratings)
        row["raw_tlx"] = self.score
        temporary = self.path.with_name(self.path.name + ".tmp")
        with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(row))
            writer.writeheader()
            writer.writerow(row)
        temporary.replace(self.path)


def run_raw_tlx(win, operator, session, stamp, check_abort):
    """Rate one dimension per page; stamp(name, detail) returns SDK time."""
    from psychopy import event, visual

    width, height = win.size
    text_options = {"font": "Yu Gothic", "color": "white", "autoLog": False}
    header = visual.TextStim(win, height=height * .040, pos=(0, height * .36),
                             wrapWidth=width * .9, **text_options)
    prompt = visual.TextStim(win, height=height * .035, pos=(0, height * .16),
                             wrapWidth=width * .85, **text_options)
    selected = visual.TextStim(win, height=height * .030, pos=(0, -height * .22), **text_options)
    low = visual.TextStim(win, height=height * .025, pos=(-width * .30, -height * .14),
                          wrapWidth=width * .35, **text_options)
    high = visual.TextStim(win, height=height * .025, pos=(width * .30, -height * .14),
                           wrapWidth=width * .35, **text_options)
    next_box = visual.Rect(win, width=width * .30, height=height * .085,
                           pos=(width * .22, -height * .35), lineColor="white", autoLog=False)
    back_box = visual.Rect(win, width=width * .30, height=height * .085,
                           pos=(-width * .22, -height * .35), lineColor="white", autoLog=False)
    next_text = visual.TextStim(win, height=height * .030, pos=next_box.pos, **text_options)
    back_text = visual.TextStim(win, text="戻る", height=height * .030, pos=back_box.pos, **text_options)
    slider = visual.Slider(win, ticks=tuple(range(0, 101, 5)), labels=("0", "50", "100"),
                           granularity=5, style="rating", units="pix", pos=(0, -height * .03),
                           size=(width * .75, height * .08), font="Yu Gothic",
                           labelHeight=height * .025, autoLog=False)
    mouse = event.Mouse(win=win)
    slider.mouse = mouse  # Use participant-window coordinates, not the operator window.
    mouse.setVisible(True)
    mouse.setPos((0, 0))
    operator.stage = "Raw TLX"
    operator.rest_remaining = None
    index = 0
    try:
        # A participant flip marks actual questionnaire presentation, before any response.
        win.callOnFlip(lambda: session.start(stamp("tlx_start", "")))
        while index < len(SCALES):
            key, title, question, low_label, high_label = SCALES[index]
            header.text = "Raw TLX ({}/6)  {}\n{}ブロック全体の課題について回答してください".format(
                index + 1, title, session.blocks)
            prompt.text = question
            low.text, high.text = "0: " + low_label, "100: " + high_label
            next_text.text = "回答を保存して終了" if index == 5 else "次へ"
            slider.reset()
            if session.ratings[key] is not None:
                slider.recordRating(session.ratings[key], rt=0, log=False)
            previous_down = True  # Require release after the previous page's click.
            while True:
                check_abort()
                header.draw()
                prompt.draw()
                slider.draw()
                rating = slider.getRating()
                selected.text = "マウスで0〜100を5点刻みに選んでください" if rating is None else "選択値: {}".format(int(rating))
                selected.draw()
                low.draw()
                high.draw()
                next_box.fillColor = "darkgreen" if rating is not None else "dimgray"
                back_box.fillColor = "dimgray" if index == 0 else "darkblue"
                next_box.draw()
                next_text.draw()
                if index > 0:
                    back_box.draw()
                    back_text.draw()
                win.flip()
                operator.detail = "Raw TLX 回答中: {}/6（確定済み {}）".format(
                    index + 1, sum(value is not None for value in session.ratings.values()))
                operator.render()
                down = bool(mouse.getPressed()[0])
                if down and not previous_down:
                    if next_box.contains(mouse) and rating is not None:
                        session.set_rating(key, int(rating))
                        stamp("tlx_response", "{}={}".format(key, int(rating)))
                        index += 1
                        break
                    if index > 0 and back_box.contains(mouse):
                        index -= 1
                        break
                previous_down = down
        session.finish(stamp("tlx_end", ""))
        return session.score
    finally:
        mouse.setVisible(False)
        session.abort()  # Leaves a completed session intact; preserves partial answers.
