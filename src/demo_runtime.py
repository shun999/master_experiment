# -*- coding: utf-8 -*-
"""Single-screen teaching demos. PsychoPy is loaded only when run_demo starts."""

import random

from .demo_tasks import DemoAnswer, demo_spec, expected_label, guide_rows, practice_rows


INPUT_KEYS = (['left', 'right', 'space', 'escape', 'r', 'g', 'backspace', 'delete', 'return', 'num_enter']
              + list('0123456789') + ['num_' + digit for digit in '0123456789'])


class DemoAborted(Exception):
    pass


def select_demo_screen(screen_index):
    import pyglet
    if type(screen_index) is not int or screen_index < 0:
        raise ValueError('画面番号には0以上の整数を指定してください。')
    count = len(pyglet.canvas.get_display().get_screens())
    if count == 1 and screen_index == 1:
        return 0
    if screen_index >= count:
        raise ValueError('指定した画面番号{}は使えません。接続画面数：{}'.format(screen_index, count))
    return screen_index


class DemoView:
    def __init__(self, win, spec, visual):
        self.win, self.spec = win, spec
        width, height = win.size
        self.height = height
        def text(y, size, font='Yu Gothic'):
            return visual.TextStim(win, text='', font=font, color='white', height=height * size,
                                   pos=(0, height * y), wrapWidth=width * .92, autoLog=False)
        self.header = text(.43, .045)
        self.rule = text(.31, .029)
        self.stimulus = text(.12, .12, 'Arial')
        self.answer = text(-.03, .043)
        self.detail = text(-.18, .033)
        self.footer = text(-.41, .027)

    def paint(self, title, rule='', stimulus='', answer='', detail='', footer='', centered=False, color='white'):
        for obj, value in ((self.header, title), (self.rule, rule), (self.stimulus, stimulus),
                           (self.answer, answer), (self.detail, detail), (self.footer, footer)):
            if obj.text != value:
                obj.text = value
        self.detail.pos = (0, 0 if centered else -self.height * .18)
        self.detail.color = color
        for obj in (self.header, self.rule, self.stimulus, self.answer, self.detail, self.footer):
            if obj.text:
                obj.draw()

    def page(self, title, body, footer):
        self.paint(title, detail=body, footer=footer, centered=True)

    def guide(self, row, index, total, answer, feedback):
        comparison = self.spec.rule
        if self.spec.task != 'arithmetic':
            comparison = '直前の数字：{}    今の数字：{}'.format(
                'なし（最初）' if row['previous'] is None else row['previous'], row['stimulus'])
        self.paint('{}　ガイド（時間制限なし）{}/{}'.format(self.spec.condition, index, total),
                   rule=comparison, stimulus=row['stimulus'],
                   answer='入力：' + (answer.text or '_') if self.spec.task == 'arithmetic' else '',
                   detail='正解：{}\n{}\n{}'.format(expected_label(row), row['reason'], feedback),
                   footer=('覚えたらスペースキー（SPACE）で次へ' if not row['response_required'] else
                           '正解したらスペースキー（SPACE）で次へ') + '／エスケープキー（ESC）で終了',
                   color='lightgreen' if answer.correct else 'white')

    def practice(self, row, index, answer, visible=True):
        memory = not row['response_required']
        self.paint('{}　練習（正解の表示なし）{}/{}'.format(self.spec.condition, index, self.spec.count),
                   rule=self.spec.rule, stimulus=row['stimulus'] if visible else '+',
                   answer='入力：' + (answer.text or '_') if self.spec.task == 'arithmetic' else '',
                   footer=('最初の数字は記憶のみです。回答しません。' if memory else
                           ('テンキーで入力・修正できます。確定操作は不要です。' if self.spec.task == 'arithmetic'
                            else '数字と、その後の「＋」表示中に1回回答してください。'))
                          + '\nエスケープキー（ESC）で終了')

    def review(self, row, index, total):
        self.page('答え合わせ　{}/{}'.format(index, total),
                  '問題：{}\n自分の回答：{}\n正解：{}\n判定：{}\n\n{}'.format(
                      row['stimulus'], row['response'], expected_label(row),
                      '採点対象外（記憶のみ）' if row['correct'] is None else ('正解' if row['correct'] else '不正解'),
                      row['reason']),
                  'スペースキー（SPACE）で次へ／エスケープキー（ESC）で終了')


class DemoSession:
    def __init__(self, spec, win, kb, view, core, rng=None):
        self.spec, self.win, self.kb, self.view, self.core = spec, win, kb, view, core
        self.rng = rng if rng is not None else random.SystemRandom()
        self.phase, self.current_row = 'intro', None
        self.last_results, self.practice_seeds = [], []
        self.last_duration = None

    def keys(self):
        keys = self.kb.getKeys(keyList=INPUT_KEYS, waitRelease=False, clear=True)
        if any(key.name == 'escape' for key in keys):
            raise DemoAborted()
        return sorted(keys, key=lambda key: float(key.rt))

    def first_flip(self):
        self.win.callOnFlip(self.kb.clock.reset)
        self.win.callOnFlip(self.kb.clearEvents)
        self.win.flip()

    def page(self, phase, title, body, choices=('space',)):
        self.phase, self.current_row = phase, None
        footer = ('Rキー：もう一度練習／Gキー：ガイドからやり直し／ESCキー：終了' if phase == 'menu' else
                  'スペースキー（SPACE）で進む／エスケープキー（ESC）で終了')
        self.view.page(title, body, footer)
        self.first_flip()
        while True:
            for key in self.keys():
                if key.name in choices:
                    return key.name
            self.view.page(title, body, footer)
            self.win.flip()

    def guided(self):
        rows = guide_rows(self.spec.task)
        self.phase = 'guide'
        for index, row in enumerate(rows, 1):
            self.current_row = row
            answer = DemoAnswer(self.spec.task, row)
            feedback = '正解を見ながら回答してください。' if row['response_required'] else '数字を覚えたらスペースキーで次へ進みます。'
            self.view.guide(row, index, len(rows), answer, feedback)
            self.first_flip()
            while True:
                advance = False
                for key in self.keys():
                    if key.name == 'space':
                        if answer.correct or not row['response_required']:
                            advance = True
                            break
                        feedback = '正解を確認し、回答し直してください。'
                    elif answer.press(key.name, float(key.rt)):
                        if answer.correct:
                            feedback = '正解です。スペースキーで次へ進めます。'
                        elif self.spec.task == 'arithmetic':
                            feedback = '入力中です。正解の数字を確認してください。'
                        else:
                            feedback = '違います。正解の矢印キーを押してみましょう。'
                if advance:
                    break
                self.view.guide(row, index, len(rows), answer, feedback)
                self.win.flip()

    def practice(self):
        self.phase = 'practice'
        seed = self.rng.randrange(2 ** 32)
        self.practice_seeds.append(seed)
        rows = practice_rows(self.spec.task, seed)
        self.last_results = []
        clock = self.core.Clock()
        tolerance = (self.win.monitorFramePeriod or 1 / 60) * .5

        def poll(answer):
            for key in self.keys():
                answer.press(key.name, float(key.rt))

        def finish(record, answer):
            # Drain timestamped events while the old keyboard clock still applies.
            poll(answer)
            record.update(response=answer.response_label, correct=answer.correct,
                          response_window_end_seconds=clock.getTime(), completed=True)

        def wait_until(deadline, record, answer, visible):
            while clock.getTime() < deadline - tolerance:
                poll(answer)
                self.view.practice(record, record['trial'], answer, visible)
                self.win.flip()
            poll(answer)

        previous = None
        for index, row in enumerate(rows):
            onset = index * self.spec.interval
            record = dict(row, onset_seconds=None, stimulus_end_seconds=None,
                          response_window_end_seconds=None, completed=False)
            answer = DemoAnswer(self.spec.task, row, deadline=self.spec.interval)
            self.current_row = record
            self.last_results.append(record)
            if previous is not None:
                self.win.callOnFlip(finish, *previous)
            if index == 0:
                self.win.callOnFlip(clock.reset)
            self.view.practice(record, index + 1, answer)
            self.win.callOnFlip(self.kb.clock.reset)
            self.win.callOnFlip(self.kb.clearEvents)
            def start(record=record, answer=answer, onset=onset):
                now = clock.getTime()
                record['onset_seconds'] = now
                answer.deadline = max(0, onset + self.spec.interval - now)
                record['input_deadline_rt_seconds'] = answer.deadline
            self.win.callOnFlip(start)
            self.win.flip()
            wait_until(onset + self.spec.stimulus_seconds, record, answer, True)
            if self.spec.stimulus_seconds < self.spec.interval:
                self.view.practice(record, index + 1, answer, False)
                self.win.callOnFlip(lambda record=record: record.update(stimulus_end_seconds=clock.getTime()))
                self.win.flip()
                wait_until(onset + self.spec.interval, record, answer, False)
            previous = record, answer
        # End the final response window on the flip at 20 seconds.
        self.view.practice(record, len(rows), answer, False)
        if self.spec.task == 'arithmetic':
            self.view.paint('練習終了', stimulus='+')
        self.win.callOnFlip(finish, *previous)
        self.win.flip()
        self.last_duration = clock.getTime()
        if self.spec.task == 'arithmetic':
            for record in self.last_results:
                record['stimulus_end_seconds'] = record['response_window_end_seconds']
        return self.last_results

    def run(self):
        controls = ('\n\nナムロック（NumLock）を有効にしてください。\n'
                    'バックスペースキー（Backspace）：1文字削除\nデリートキー（Delete）：全削除\n'
                    'エンターキー（Enter）での確定は不要です。' if self.spec.task == 'arithmetic' else '')
        self.page('intro', '{}のデモ'.format(self.spec.title),
                  '{}\n\n{}\n\nまず、正解と理由を見ながら回答します。\n'
                  '次に、正解を隠して20秒間練習します。\n最後に答え合わせをします。{}'.format(
                      self.spec.condition, self.spec.rule, controls))
        action = 'g'
        while True:
            if action == 'g':
                self.guided()
            extra = ('ナムロック（NumLock）を有効にしてください。\n'
                     '1問5秒で自動採点します。切り替わるまで修正できます。\n'
                     'エンターキー（Enter）による確定は不要です。' if self.spec.task == 'arithmetic' else
                     '数字0.5秒＋注視点{}秒、{}秒間隔で進みます。\n'.format(
                         self.spec.interval - self.spec.stimulus_seconds, self.spec.interval)
                     + ('最初の数字には左矢印で回答してください。' if self.spec.task == 'nback' else
                        '最初の数字は記憶のみで、回答しません。'))
            self.page('practice_ready', '本番速度の練習を始めます',
                      '{}\n\n正解と比較用の数字は表示しません。\n{}\n\n{}問（個）・20秒です。'.format(
                          self.spec.rule, extra, self.spec.count))
            results = self.practice()
            scored = [row for row in results if row['response_required']]
            self.page('summary', '練習の結果', '正答数：{}／{}\n\nこれから1問ずつ答えを確認します。'.format(
                sum(row['correct'] for row in scored), len(scored)))
            for index, row in enumerate(results, 1):
                self.phase, self.current_row = 'review', row
                self.view.review(row, index, len(results))
                self.first_flip()
                while not any(key.name == 'space' for key in self.keys()):
                    self.view.review(row, index, len(results))
                    self.win.flip()
            action = self.page('menu', 'デモを終了・再実行できます',
                               'Rキー：もう一度20秒間練習する\nGキー：解説付きガイドからやり直す\n'
                               'エスケープキー（ESC）：終了する', choices=('r', 'g'))


def run_demo(task, screen_index=1, full_screen=True, window_size=(1280, 720)):
    spec = demo_spec(task)
    screen = select_demo_screen(screen_index)
    from psychopy import core, visual
    from psychopy.hardware import keyboard
    win, session = None, None
    try:
        print('DEMO: {} / {} / screen={}'.format(spec.title, spec.condition, screen), flush=True)
        settings = dict(fullscr=full_screen, screen=screen, units='pix', color='black',
                        waitBlanking=True, allowGUI=not full_screen, winType='pyglet', autoLog=False)
        if not full_screen:
            settings['size'] = window_size
        win = visual.Window(**settings)
        view = DemoView(win, spec, visual)
        view.page('{}のデモ'.format(spec.title), 'デモを準備しています。', '')
        win.flip()  # Establish/draw the visual context before keyboard initialisation.
        print('DEMO: keyboard initialisation', flush=True)
        kb = keyboard.Keyboard()
        session = DemoSession(spec, win, kb, view, core)
        session.run()
    except DemoAborted:
        return session.last_results if session is not None else []
    finally:
        if win is not None:
            win.close()
