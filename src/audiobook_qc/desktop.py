"""Offline native Tk desktop UI. No webview, browser, local HTTP server or API."""
from __future__ import annotations
import json
import math
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
import wave
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from . import worker
from .analysis import fmt_time, effective_severity
from .offline import DEFAULT_MODEL, resolve_model, offline_environment

BG = '#F5F7FB'
WHITE = '#FFFFFF'
NAVY = '#142F4D'
BLUE = '#246AB0'
MUTED = '#63768D'
BORDER = '#DDE5EE'
GREEN = '#147D5B'


def create_project(audio: Path, manuscript: Path, part_minutes: int = 20,
                   model: str = DEFAULT_MODEL, glossary_text: str = "") -> str:
    """Direct project creation, no HTTP upload; large files copied in bounded buffers."""
    from .offline import MODEL_REPOS
    if model not in MODEL_REPOS:
        raise ValueError('Неизвестная модель')
    if len(glossary_text) > 30000:
        raise ValueError("Словарь слишком длинный")
    audio = audio.resolve(strict=True)
    manuscript = manuscript.resolve(strict=True)
    if audio.suffix.lower() not in ('.wav', '.mp3', '.m4a', '.flac', '.aac', '.ogg', '.mp4'):
        raise ValueError('Неподдерживаемый формат аудиофайла')
    if manuscript.suffix.lower() not in ('.txt', '.docx'):
        raise ValueError('Текст должен быть TXT или DOCX')
    if part_minutes not in (5, 10, 20, 30, 60):
        raise ValueError('Недопустимая длительность части')
    if len(worker.tokenize(worker.source_text(manuscript))) < 20:
        raise ValueError('В тексте слишком мало слов')
    duration = worker.audio_duration(audio)
    if duration <= 0 or duration > 48 * 3600:
        raise ValueError('Записи поддерживаются до 48 часов')
    # Fail before copying gigabytes if the offline model is missing.
    resolve_model(model)
    from .dual_engine import resolve_secondary_dir
    resolve_secondary_dir()
    # New projects require the locally bundled event model as well. Check
    # before copying a multi-hour audio file, never download at runtime.
    from .events import model_dir, _manifest_verified
    _manifest_verified(model_dir())
    # A direct project keeps private copies of the original files. Check
    # capacity before starting multi-gigabyte copies, not halfway through.
    # Leave headroom for incremental JSON and reports during processing.
    needed = audio.stat().st_size + manuscript.stat().st_size + 256 * 1024**2
    root = worker.ROOT
    root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(root).free < needed:
        raise ValueError('Недостаточно свободного места для копии аудио и текста проекта (нужен запас 256 МБ).')
    pid = uuid.uuid4().hex
    base = worker.job_dir(pid)
    inputs = base / 'inputs'
    inputs.mkdir(parents=True, exist_ok=False)
    try:
        target_audio = 'audio' + audio.suffix.lower()
        target_text = 'text' + manuscript.suffix.lower()
        shutil.copy2(audio, inputs / (target_audio + '.pending'))
        (inputs / (target_audio + '.pending')).replace(inputs / target_audio)
        shutil.copy2(manuscript, inputs / target_text)
        (inputs / "glossary.txt").write_text(glossary_text, encoding="utf-8")
        span = part_minutes * 60
        parts = [{'index': i + 1, 'start': i * span,
                  'end': min(duration, (i + 1) * span), 'status': 'pending', 'issue_count': 0}
                 for i in range(math.ceil(duration / span))]
        job = {'id':pid, 'audio_name':audio.name, 'text_name':manuscript.name,
               'audio_stored': target_audio, 'text_stored':target_text,
               'duration': duration, 'part_minutes':part_minutes,
               'model':model, 'report_policy':'dual-gigaam', 'quality_mode':'extended-v1', 'status':'ready', 'processed_seconds':0,
               'cursor':0, 'message':'Готово к анализу', 'parts':parts}
        worker.save_job(job)
        return pid
    except Exception:
        shutil.rmtree(base, ignore_errors=True)
        raise


def list_projects() -> list[dict]:
    root = worker.ROOT / 'projects'
    jobs = []
    for meta in sorted(root.glob('*/job.json') if root.exists() else [], reverse=True):
        try:
            job = worker.read_json(meta)
            if job.get('status') in ('running', 'model_loading', 'pausing') and job['id'] not in worker.ACTIVE:
                job['status'] = 'paused'
                job['message'] = 'Продолжите анализ после перезапуска'
                worker.save_job(job)
            jobs.append(job)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return jobs


def delete_saved_project(pid: str) -> None:
    """Remove only an inactive local project and its copied inputs/reports.

    Never delete a directory while the recognition thread could still use it.
    """
    with worker.STATE_LOCK:
        folder = worker.job_dir(pid)  # validates the UUID, prevents ../ traversal
        if folder.is_symlink() or folder.resolve().parent != (worker.ROOT / 'projects').resolve():
            raise RuntimeError('Недопустимый путь проекта')
        if pid in worker.ACTIVE:
            raise RuntimeError('Сначала приостановите проверку и дождитесь её остановки.')
        job = worker.load_job(pid)
        if job.get('id') != pid:
            raise RuntimeError('Некорректные данные проекта')
        if job.get('status') in ('running', 'model_loading', 'pausing'):
            raise RuntimeError('Дождитесь полной остановки проверки перед удалением.')
        shutil.rmtree(folder)


class ClipPlayer:
    """Bounded-memory 180-second local WAV player. Requires sounddevice in release."""
    def __init__(self):
        self.samples = None
        self.rate = 16000
        self.position = 0.0
        self.started_at = None
        self.started_offset = 0.0
        self.length = 0.0
        self.speed = 1.0
        self.is_playing = False

    def load(self, path: Path):
        import numpy as np
        self.stop()
        with wave.open(str(path), 'rb') as wf:
            if wf.getsampwidth() != 2 or wf.getnchannels() != 1:
                raise ValueError('Нужен моно WAV PCM 16-bit')
            self.rate = wf.getframerate()
            n = wf.getnframes()
            buf = wf.readframes(n)
        self.samples = np.frombuffer(buf, dtype=np.int16).astype(np.float32) / 32768
        self.length = len(self.samples) / self.rate
        self.position = 0.0

    def current(self) -> float:
        if self.is_playing and self.started_at is not None:
            p = self.started_offset + (time.monotonic()-self.started_at)*self.speed
            if p >= self.length:
                self.is_playing = False
                self.position = self.length
                return self.length
            return p
        return self.position

    def play(self):
        if self.samples is None:
            return
        import sounddevice as sd
        self.position = min(max(0.0, self.current()), self.length)
        sd.stop()
        idx = int(self.position * self.rate)
        if idx >= len(self.samples):
            self.position = 0.0
            idx = 0
        # Speed affects pitch; do not advertise pitch-preserving time stretching.
        sd.play(self.samples[idx:], round(self.rate * self.speed), blocking=False)
        self.started_at = time.monotonic()
        self.started_offset = self.position
        self.is_playing = True

    def seek(self, seconds: float):
        resume = self.is_playing
        self.stop()
        self.position = max(0.0, min(self.length, seconds))
        if resume:
            self.play()

    def stop(self):
        self.position = self.current()
        self.is_playing = False
        self.started_at = None
        try:
            import sounddevice as sd
            sd.stop()
        except (ImportError, OSError):
            # Headless Linux CI, or systems without PortAudio, must still be
            # able to load/seek clips and clean up an idle player.  Actual
            # playback will still report a missing audio backend.
            pass


def app_window():
    offline_environment()
    window = tk.Tk()
    App(window)
    window.mainloop()


class App:
    def __init__(self, window: tk.Tk):
        self.root = window
        window.title('Audiobook QC — Проверка озвучки')
        window.configure(bg=BG)
        window.geometry('1240x820')
        window.minsize(820, 560)
        self.current_id = None
        self.audio_input = tk.StringVar()
        self.text_input = tk.StringVar()
        self.parts_minutes = tk.StringVar(value='20')
        self.show_uncertain = tk.BooleanVar(value=False)
        self.player = ClipPlayer()
        self.clip_start = 0.0
        self._audio_loading = False
        self._issue_rows = []
        self._last_refresh = 0
        self._issue_render_signature = None
        self._clip_seq = 0
        self._preview_path = None
        self._setup_styles()
        self._build()
        self.update_history()
        if self.history_ids:
            self.current_id = self.history_ids[0]
            self._select_history_row(0)
            self._show_project_view()
            self.render_job()
        else:
            self._choose_new()
        window.protocol('WM_DELETE_WINDOW', self.close)
        self.poll()

    def _setup_styles(self):
        sty = ttk.Style(self.root)
        if 'clam' in sty.theme_names():
            sty.theme_use('clam')
        sty.configure('Main.Treeview', rowheight=30, font=('Segoe UI', 10), background=WHITE,
                      foreground=NAVY, fieldbackground=WHITE)
        sty.configure('Main.Treeview.Heading', font=('Segoe UI', 10, 'bold'), background='#E8EFF7', foreground=NAVY)
        sty.map('Main.Treeview', background=[('selected','#D9E9FA')], foreground=[('selected',NAVY)])
        sty.configure('AQC.Horizontal.TProgressbar', troughcolor='#E7EDF6', background=BLUE, borderwidth=0)
        sty.configure('TCombobox', padding=6)

    def label(self, parent, text, font=10, bold=False, fg=NAVY, bg=WHITE, **kw):
        return tk.Label(parent,text=text,font=('Segoe UI',font,'bold' if bold else 'normal'),
                        bg=bg,fg=fg,**kw)

    def button(self, parent, text, callback, primary=False, padx=13, pady=9):
        w = tk.Button(parent,text=text,command=callback,relief='flat',cursor='hand2',
                  font=('Segoe UI',10,'bold' if primary else 'normal'),padx=padx,pady=pady,
                  bg=BLUE if primary else '#EAF1F9',fg=WHITE if primary else NAVY,
                  activebackground='#18538D' if primary else '#D6E5F6',
                  activeforeground=WHITE if primary else NAVY)
        return w

    def _card(self, parent, padx=20, pady=14):
        f=tk.Frame(parent,bg=WHITE,highlightbackground=BORDER,highlightthickness=1)
        f.pack(fill='x',padx=18,pady=8)
        inner=tk.Frame(f,bg=WHITE)
        inner.pack(fill='both',expand=True,padx=padx,pady=pady)
        return inner

    def _build(self):
        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill='both', expand=True)

        # Persistent project navigation, including deletion without creating a new job.
        side = tk.Frame(outer, width=224, bg=NAVY)
        side.pack(side='left', fill='y')
        side.pack_propagate(False)
        self.label(side, 'AUDIOBOOK QC', 15, True, WHITE, NAVY).pack(anchor='w', padx=18, pady=(23,5))
        self.label(side, 'Корректор аудиокниг', 9, False, '#B2C8DC', NAVY).pack(anchor='w', padx=18)
        tk.Frame(side, height=1, bg='#35516D').pack(fill='x', padx=16, pady=17)
        self.button(side, '+ Новая проверка', self._choose_new, True).pack(fill='x', padx=12, pady=(0,13))
        self.label(side, 'ПРОЕКТЫ', 9, True, '#BED0E1', NAVY).pack(anchor='w', padx=16, pady=(4,7))
        history_box = tk.Frame(side, bg=NAVY)
        history_box.pack(fill='both', expand=True, padx=(9,7))
        self.history = tk.Listbox(history_box, relief='flat', activestyle='none', borderwidth=0,
                                  highlightthickness=0, bg=NAVY, fg=WHITE,
                                  selectbackground='#305878', selectforeground=WHITE,
                                  exportselection=False, font=('Segoe UI',10), height=10)
        history_scroll = ttk.Scrollbar(history_box, orient='vertical', command=self.history.yview)
        self.history.configure(yscrollcommand=history_scroll.set)
        history_scroll.pack(side='right', fill='y')
        self.history.pack(side='left', fill='both', expand=True)
        self.history.bind('<<ListboxSelect>>', self._history_click)
        self.history_ids = []
        self.delete_btn = tk.Button(side, text='Удалить выбранный проект',
                                    command=self.delete_project, relief='flat', cursor='hand2',
                                    font=('Segoe UI', 9), bg=NAVY, fg='#F6C6C7',
                                    activebackground='#294563', activeforeground=WHITE,
                                    anchor='w', padx=12, pady=9)
        self.delete_btn.pack(fill='x', padx=10, pady=(8,4))
        self.label(side, 'Обработка на этом компьютере',9,False,'#A6C3DB',NAVY).pack(
            anchor='w',padx=17,pady=(4,15))

        main = tk.Frame(outer, bg=BG)
        main.pack(side='left', fill='both', expand=True)
        # Canvas is essential: previously the lower controls were cut off on laptop screens.
        self.page_canvas = tk.Canvas(main, bg=BG, highlightthickness=0, borderwidth=0)
        page_scroll = ttk.Scrollbar(main, orient='vertical', command=self.page_canvas.yview)
        self.page_canvas.configure(yscrollcommand=page_scroll.set)
        page_scroll.pack(side='right', fill='y')
        self.page_canvas.pack(side='left', fill='both', expand=True)
        page = tk.Frame(self.page_canvas, bg=BG)
        self.page = page
        self.page_window = self.page_canvas.create_window((0,0), window=page, anchor='nw')
        page.bind('<Configure>', self._resize_scrollregion)
        self.page_canvas.bind('<Configure>', self._resize_page_width)
        self.root.bind_all('<MouseWheel>', self._mousewheel, add='+')
        self.root.bind_all('<Button-4>', self._mousewheel, add='+')
        self.root.bind_all('<Button-5>', self._mousewheel, add='+')

        header = tk.Frame(page, bg=BG)
        header.pack(fill='x', padx=20, pady=(19, 5))
        self.heading = self.label(header,'Проверка озвучки',19,True,NAVY,BG)
        self.heading.pack(side='left')
        self.offline = self.label(header, '●  Офлайн', 10, True, GREEN, BG)
        self.offline.pack(side='right')
        try:
            resolve_model(DEFAULT_MODEL)
        except FileNotFoundError:
            self.offline.configure(text='●  Модель не найдена', fg='#A56C14')
        self.new_panel = self._card(page, pady=13)
        self.new_panel_host = self.new_panel.master
        self._build_new()
        self.progress_panel = self._card(page, pady=13)
        self.progress_panel_host = self.progress_panel.master
        self._build_status()
        issues_shell = tk.Frame(page,bg=WHITE,highlightbackground=BORDER,highlightthickness=1)
        issues_shell.pack(fill='x',padx=18,pady=(8,18))
        self.issues_shell = issues_shell
        self._build_issues(issues_shell)

    def _resize_scrollregion(self, _event=None):
        self.page_canvas.configure(scrollregion=self.page_canvas.bbox('all'))

    def _resize_page_width(self, event):
        self.page_canvas.itemconfigure(self.page_window, width=event.width)
        self._resize_scrollregion()

    def _mousewheel(self, event):
        # Do not steal scrolling from the issue list, project list or combobox.
        try:
            target = self.root.winfo_containing(event.x_root, event.y_root)
        except (tk.TclError, AttributeError):
            return
        if target is None:
            return
        w = target
        while w is not None:
            if w in (self.history, self.tree):
                return
            if w == self.page_canvas:
                break
            w = getattr(w, 'master', None)
        if w is None:
            return
        if event.num == 4:
            direction = -3
        elif event.num == 5:
            direction = 3
        else:
            delta = event.delta or 0
            direction = -3 if delta > 0 else 3 if delta < 0 else 0
        if direction:
            self.page_canvas.yview_scroll(direction, 'units')
            return 'break'

    def _show_project_view(self):
        self.new_panel_host.pack_forget()
        if not self.progress_panel_host.winfo_manager():
            if self.issues_shell.winfo_manager():
                self.progress_panel_host.pack(fill='x', padx=18, pady=8, before=self.issues_shell)
            else:
                self.progress_panel_host.pack(fill='x', padx=18, pady=8)
        if not self.issues_shell.winfo_manager():
            self.issues_shell.pack(fill='x', padx=18, pady=(8,18))
        self.heading.configure(text='Проверка озвучки')
        self._resize_scrollregion()

    def _show_new_view(self):
        self.progress_panel_host.pack_forget()
        self.issues_shell.pack_forget()
        if not self.new_panel_host.winfo_manager():
            self.new_panel_host.pack(fill='x', padx=18, pady=8)
        self.heading.configure(text='Новая проверка')
        self.page_canvas.yview_moveto(0)
        self._resize_scrollregion()

    def _file_row(self,parent,tag,var,cmd):
        row=tk.Frame(parent,bg=WHITE)
        row.pack(fill='x',pady=4)
        self.label(row,tag,10,True).pack(side='left',padx=(0,8))
        self.label(row,'',10,False,fg=MUTED).pack(side='left')
        self.button(row,'Выбрать…',cmd,padx=12,pady=5).pack(side='right')
        entry=tk.Entry(row,textvariable=var,relief='flat',bg='#F0F4FA',fg=NAVY,
                       font=('Segoe UI',10),highlightthickness=1,highlightbackground=BORDER)
        entry.pack(side='right',fill='x',expand=True,padx=(0,9),ipady=6)

    def _build_new(self):
        p=self.new_panel
        self.label(p,'Выбери запись и текст книги',13,True).pack(anchor='w',pady=(0,8))
        self._file_row(p,'Аудио',self.audio_input, self.pick_audio)
        self._file_row(p,'Рукопись',self.text_input, self.pick_text)
        footer=tk.Frame(p,bg=WHITE)
        footer.pack(fill='x',pady=(8,0))
        self.label(footer,'Отчёты по',10,False).pack(side='left')
        m=ttk.Combobox(footer,textvariable=self.parts_minutes,values=['5','10','20','30','60'],state='readonly',width=4)
        m.pack(side='left',padx=7)
        self.label(footer,'минут',10,False).pack(side='left')
        # The models run offline and do not need another crowded line of text here.
        self.create_btn=self.button(footer,'Начать проверку',self.create_and_run,True)
        self.create_btn.pack(side='right')

    def _build_status(self):
        p=self.progress_panel
        head=tk.Frame(p,bg=WHITE)
        head.pack(fill='x')
        self.job_title=self.label(head,'Проект не выбран',13,True)
        self.job_title.pack(side='left')
        self.job_state=self.label(head,'',10,True,fg=BLUE)
        self.job_state.pack(side='right')
        self.progress=ttk.Progressbar(p,style='AQC.Horizontal.TProgressbar',mode='determinate')
        self.progress.pack(fill='x',pady=(12,6))
        self.job_summary=self.label(p,'Выбери аудиофайл и текст для начала проверки.',9,False,fg=MUTED)
        self.job_summary.pack(anchor='w',pady=(0,7))
        controls=tk.Frame(p,bg=WHITE)
        controls.pack(fill='x')
        self.resume_btn=self.button(controls,'Продолжить',self.resume_job,True)
        self.resume_btn.pack(side='left',padx=(0,7))
        self.pause_btn=self.button(controls,'Пауза',self.pause_job)
        self.pause_btn.pack(side='left',padx=5)
        self.button(controls,'Сохранить Word',self.save_report).pack(side='left',padx=5)
        self.button(controls,'Папка с отчётами',self.open_reports_folder).pack(side='right')

    def _build_issues(self, parent):
        heading=tk.Frame(parent,bg=WHITE)
        heading.pack(fill='x',padx=18,pady=(14,6))
        self.label(heading,'Замечания по озвучке',13,True).pack(side='left')
        cb=tk.Checkbutton(heading,text='Показать сомнительные',variable=self.show_uncertain,
                          command=self.render_issues,relief='flat',bg=WHITE,fg=MUTED,
                          font=('Segoe UI',10),activebackground=WHITE)
        cb.pack(side='right')
        tree_box=tk.Frame(parent,bg=WHITE)
        tree_box.pack(fill='x',padx=17,pady=6)
        tree_box.configure(height=310)
        tree_box.pack_propagate(False)
        self.tree=ttk.Treeview(tree_box,columns=('time','type','spoken','expected'),show='headings',style='Main.Treeview')
        for k,title,width in [('time','Время',105),('type','Тип',132),('spoken','Распознано',230),('expected','По книге',230)]:
            self.tree.heading(k,text=title)
            self.tree.column(k,width=width,anchor='w',minwidth=65,stretch=(k in ('spoken','expected')))
        scroll=ttk.Scrollbar(tree_box,command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y')
        self.tree.pack(side='left',fill='both',expand=True)
        self.tree.bind('<<TreeviewSelect>>',self.selected_issue)
        self.empty_issues = self.label(tree_box,
            'Пока нет подтверждённых замечаний.\nМожно включить «Показать сомнительные».',
            11, False, fg=MUTED, bg=WHITE, justify='center')
        self.empty_issues.place(relx=.5, rely=.5, anchor='center')
        actions=tk.Frame(parent,bg=WHITE)
        actions.pack(fill='x',padx=17,pady=(1,6))
        self.reason=self.label(actions,'Выбери ошибку в списке, чтобы прослушать запись.',9,False,fg=MUTED,
                               wraplength=600,justify='left')
        self.reason.pack(side='left',fill='x',expand=True)
        self.button(actions,'Не ошибка',lambda:self.review('false'),padx=9,pady=5).pack(side='right',padx=4)
        self.button(actions,'Исправлено',lambda:self.review('fixed'),padx=9,pady=5).pack(side='right',padx=4)
        player=tk.Frame(parent,bg='#EFF4FA')
        player.pack(fill='x',padx=16,pady=(4,15))
        self.button(player,'◀ −10 с',lambda:self.seek_relative(-10),padx=8,pady=5).pack(side='left',padx=(7,3),pady=6)
        self.play_btn=self.button(player,'▶ Слушать',self.toggle_player,True,padx=10,pady=5)
        self.play_btn.pack(side='left',padx=3)
        self.button(player,'+10 с ▶',lambda:self.seek_relative(10),padx=8,pady=5).pack(side='left',padx=3)
        self.seek_value=tk.DoubleVar(value=0)
        self.timeline=ttk.Scale(player,variable=self.seek_value,from_=0,to=90,command=self._seek_preview)
        self.timeline.pack(side='left',fill='x',expand=True,padx=(8,6))
        self.timeline.bind('<ButtonRelease-1>',self._commit_seek)
        self.time_label=self.label(player,'0:00 / 0:00',9,False,fg=MUTED,bg='#EFF4FA')
        self.time_label.pack(side='right',padx=(0,10))
        self._sliding=False
        self.timeline.bind('<ButtonPress-1>',lambda _:setattr(self,'_sliding',True))

    def pick_audio(self):
        p=filedialog.askopenfilename(title='Выбери аудиокнигу',filetypes=[('Аудио','*.mp3 *.wav *.m4a *.flac *.aac *.ogg *.mp4'),('Все файлы','*.*')])
        if p:self.audio_input.set(p)

    def pick_text(self):
        p=filedialog.askopenfilename(title='Выбери рукопись',filetypes=[('Текст','*.docx *.txt'),('Все файлы','*.*')])
        if p:self.text_input.set(p)

    def _choose_new(self):
        self.current_id = None
        self.history.selection_clear(0, 'end')
        self.audio_input.set('')
        self.text_input.set('')
        self.create_btn.configure(state='normal')
        self._clip_seq += 1
        self.player.stop()
        self._show_new_view()

    def create_and_run(self):
        if not self.audio_input.get() or not self.text_input.get():
            messagebox.showwarning('Нет файлов','Выбери аудио и текст рукописи.')
            return
        if worker.ACTIVE:
            messagebox.showwarning('Идёт проверка',
                                   'Сначала приостановите текущую проверку и дождитесь остановки.')
            return
        self.create_btn.configure(state='disabled')
        audio, text = Path(self.audio_input.get()), Path(self.text_input.get())
        duration = int(self.parts_minutes.get())
        self.create_btn.configure(text='Подготовка…')
        def task():
            try:
                pid=create_project(audio,text,duration)
                self.root.after(0,lambda:self._created(pid))
            except Exception as ex:
                self.root.after(0,lambda e=str(ex):self._create_failed(e))
        threading.Thread(target=task,daemon=True).start()

    def _created(self,pid):
        self.create_btn.configure(state='normal', text='Начать проверку')
        self.current_id = pid
        self.update_history()
        self._issue_render_signature = None
        self._show_project_view()
        self.render_job()
        self.resume_job()

    def _create_failed(self,error):
        self.create_btn.configure(state='normal', text='Начать проверку')
        messagebox.showerror('Не удалось создать проект',error)

    def update_history(self):
        jobs=list_projects()
        self.history_ids=[job['id'] for job in jobs]
        old=self.current_id
        self.history.delete(0,'end')
        for j in jobs:
            completed=sum(p['status']=='done' for p in j['parts'])
            self.history.insert('end',f'  {j["audio_name"][:21]}  ({completed}/{len(j["parts"])})')
        if old in self.history_ids:
            self._select_history_row(self.history_ids.index(old))

    def _select_history_row(self, index: int):
        self.history.selection_clear(0, 'end')
        self.history.selection_set(index)
        self.history.see(index)

    def delete_project(self):
        # Prefer the highlighted history entry, not a previously visible project.
        picked = self.history.curselection()
        pid = self.history_ids[picked[0]] if picked else self.current_id
        if not pid:
            messagebox.showinfo('Удаление проекта', 'Сначала выбери проект слева.')
            return
        try:
            job = worker.load_job(pid)
        except (OSError, ValueError) as ex:
            messagebox.showerror('Не удалось найти проект', str(ex))
            return
        if pid in worker.ACTIVE or job.get('status') in ('running','model_loading','pausing'):
            messagebox.showwarning('Идёт проверка',
                                   'Сначала нажми «Пауза» и дождись остановки текущего фрагмента.')
            return
        ready = sum(p.get('status') == 'done' for p in job.get('parts', []))
        if not messagebox.askyesno('Удалить проект?',
            f'Удалить «{job.get("audio_name", "Проект") }»?\n\n'
            f'Готовых частей: {ready}. Будут навсегда удалены копия аудио, рукопись, '
            'промежуточные результаты и все отчёты этого проекта.\n\n'
            'Сохранённые отдельно Word-файлы останутся на диске.',
            icon='warning', parent=self.root):
            return
        try:
            delete_saved_project(pid)
        except (OSError, ValueError, RuntimeError) as ex:
            messagebox.showerror('Не удалось удалить проект', str(ex))
            return
        self._clip_seq += 1
        self.player.stop()
        if self._preview_path:
            self._preview_path = None
        self._issue_render_signature = None
        self.current_id = None
        self.update_history()
        if self.history_ids:
            self.current_id = self.history_ids[0]
            self._select_history_row(0)
            self._show_project_view()
            self.render_job()
        else:
            self._choose_new()

    def _history_click(self,event):
        pick=self.history.curselection()
        if pick:
            self.current_id=self.history_ids[pick[0]]
            self._issue_render_signature=None
            self._clip_seq+=1
            self.player.stop()
            self._show_project_view()
            self.page_canvas.yview_moveto(0)
            self.render_job()

    def job(self):
        if not self.current_id:
            return None
        try:return worker.load_job(self.current_id)
        except (OSError,ValueError):return None

    def render_job(self):
        job=self.job()
        if not job:return
        self.job_title.configure(text=job['audio_name'])
        states={'ready':'Готов к проверке','running':'Проверка','model_loading':'Загрузка модели',
                'paused':'Пауза','pausing':'Приостанавливается','completed':'Готово','error':'Ошибка'}
        self.job_state.configure(text=states.get(job['status'],job['status']))
        self.resume_btn.configure(state='disabled' if job['status'] in
                                  ('running','model_loading','pausing','completed') else 'normal')
        self.pause_btn.configure(state='normal' if job['status'] in
                                 ('running','model_loading') else 'disabled')
        self.progress['value']=100*job.get('processed_seconds',0)/max(1,job['duration'])
        ready=sum(p['status']=='done' for p in job['parts'])
        self.job_summary.configure(text=f'{ready}/{len(job["parts"])} частей  •  {fmt_time(job.get("processed_seconds",0))} / {fmt_time(job["duration"])}  •  {job.get("message","")}')
        self.render_issues()

    def render_issues(self):
        job=self.job()
        if not job:return
        selected_id = None
        pick = self.tree.selection()
        if pick and int(pick[0]) < len(self._issue_rows):
            selected_id=self._issue_rows[int(pick[0])]['id']
        issues=worker.all_issues(job['id'])
        reviews=worker.read_json(worker.job_dir(job['id'])/'reviews.json', {})
        signature=(job['id'], bool(self.show_uncertain.get()), tuple(
            (p['status'],p.get('issue_count',0)) for p in job['parts']),
            tuple(sorted(reviews.items())))
        if signature == self._issue_render_signature:
            return
        self._issue_render_signature = signature
        self._issue_rows=[]
        self.tree.delete(*self.tree.get_children())
        for issue in issues:
            if not self.show_uncertain.get() and effective_severity(issue) != 'high':
                continue
            if (not self.show_uncertain.get() and job.get('report_policy') == 'dual-gigaam'
                    and issue.get('kind') in ('replacement', 'omission', 'extra', 'repeat')
                    and not issue.get('dual_confirmed')):
                continue
            if reviews.get(issue['id'])=='false' and not self.show_uncertain.get():
                continue
            i=len(self._issue_rows)
            self._issue_rows.append(issue)
            labels={'repeat':'Повтор','omission':'Пропуск','replacement':'Замена',
                    'extra':'Вне текста','uncertain':'Сомнительно','clipping':'Звук','profanity':'Реплика'}
            self.tree.insert('','end',iid=str(i),values=(fmt_time(issue['start']),
                labels.get(issue['kind'],issue['kind']),issue.get('spoken','')[:100],issue.get('expected','')[:100]))
            if issue['id']==selected_id:
                self.tree.selection_set(str(i))
        if self._issue_rows:
            self.empty_issues.place_forget()
        else:
            self.empty_issues.place(relx=.5, rely=.5, anchor='center')

    def resume_job(self):
        if not self.current_id:return
        job=self.job()
        if job and job.get('status') != 'completed' and (job.get('model') != DEFAULT_MODEL
                or job.get('report_policy') != 'dual-gigaam'):
            messagebox.showwarning('Старая модель проекта',
                'Этот проект создан другой версией движка. Готовые Word-отчёты останутся доступны, '
                'но нельзя продолжать старую проверку с новыми настройками распознавания. '
                'Для нового анализа выбери исходные файлы заново.')
            return
        try:
            worker.start(self.current_id)
            self.render_job()
        except Exception as ex:
            messagebox.showerror('Не удалось запустить',str(ex))

    def pause_job(self):
        if self.current_id:
            worker.pause(self.current_id)
            self.render_job()

    def save_report(self):
        job=self.job()
        if not job:return
        ready=[p for p in job['parts'] if p['status']=='done']
        if not ready:
            messagebox.showinfo('Отчёт ещё не готов','После завершения первой части появится Word-отчёт.')
            return
        import tkinter.simpledialog as dialog
        idx=dialog.askinteger('Выбрать часть',f'Готовы части: {", ".join(str(x["index"]) for x in ready)}.\nНомер части для экспорта:',initialvalue=ready[-1]['index'],minvalue=1,maxvalue=len(job['parts']),parent=self.root)
        if idx is None:return
        if idx not in {x['index'] for x in ready}:
            messagebox.showwarning('Не готово','Эта часть ещё обрабатывается.')
            return
        src=worker.job_dir(job['id'])/'reports'/f'Часть_{idx:03d}.docx'
        target=filedialog.asksaveasfilename(defaultextension='.docx',initialfile=src.name,filetypes=[('Word','*.docx')])
        if target:shutil.copy2(src,target)

    def open_reports_folder(self):
        job=self.job()
        if not job:return
        p=worker.job_dir(job['id'])/'reports'
        p.mkdir(parents=True,exist_ok=True)
        if sys.platform=='win32':os.startfile(str(p))
        elif sys.platform=='darwin':subprocess.Popen(['open',str(p)],**worker.quiet_subprocess_kwargs())
        else:subprocess.Popen(['xdg-open',str(p)],**worker.quiet_subprocess_kwargs())

    def selected_issue(self,event=None):
        pick=self.tree.selection()
        if not pick or not self.current_id:return
        idx=int(pick[0]);
        if idx >= len(self._issue_rows):return
        issue=self._issue_rows[idx]
        self.reason.configure(text=issue.get('reason',''))
        self._load_clip(issue)

    def review(self,status):
        pick=self.tree.selection()
        if not pick or not self.current_id:return
        issue=self._issue_rows[int(pick[0])]
        path=worker.job_dir(self.current_id)/'reviews.json'
        data=worker.read_json(path,{})
        data[issue['id']]=status
        worker.write_json(path,data)
        worker.refresh_reports(self.current_id)
        self.render_issues()

    def _load_clip(self, issue):
        job=self.job()
        if not job:return
        self.player.stop()
        self.play_btn.configure(text='Подготовка…')
        self._clip_seq += 1
        seq=self._clip_seq
        start=max(0.0,float(issue['start'])-18)
        length=min(180.0, job['duration']-start)
        self.clip_start=start
        pid=job['id']
        self._audio_loading=True
        def extract():
            try:
                out=worker.job_dir(pid)/f'preview-{seq}.wav'
                # No network and no external player: all playback stays in this window.
                worker.cut_audio(worker.job_dir(pid)/'inputs'/job['audio_stored'], start,length,out)
                self.root.after(0,lambda:self._clip_loaded(pid,out,seq))
            except Exception as ex:
                self.root.after(0,lambda e=str(ex):self._clip_failed(e))
        threading.Thread(target=extract,daemon=True).start()

    def _clip_loaded(self,pid,path,seq):
        if self.current_id != pid or self._clip_seq != seq:
            path.unlink(missing_ok=True)
            return
        try:
            self.player.load(path)
            if self._preview_path and self._preview_path != path:
                self._preview_path.unlink(missing_ok=True)
            self._preview_path=path
            self.timeline.configure(to=max(1,self.player.length))
            self.seek_value.set(0)
            self.play_btn.configure(text='▶ Слушать')
        except Exception as ex:
            self._clip_failed(str(ex))
        finally:
            self._audio_loading=False

    def _clip_failed(self,error):
        self._audio_loading=False
        self.play_btn.configure(text='▶ Слушать')
        messagebox.showerror('Не удалось подготовить аудио',error)

    def toggle_player(self):
        if self._audio_loading:return
        try:
            if self.player.is_playing:
                self.player.stop()
                self.play_btn.configure(text='▶ Слушать')
            elif self.player.samples is not None:
                self.player.play()
                self.play_btn.configure(text='❚❚ Пауза')
        except Exception as ex:
            messagebox.showerror('Звук недоступен',str(ex))

    def seek_relative(self,delta):
        try:self.player.seek(self.player.current()+delta)
        except Exception as ex:messagebox.showerror('Перемотка',str(ex))

    def _seek_preview(self,value):
        pass

    def _commit_seek(self,event):
        self._sliding=False
        self.player.seek(float(self.seek_value.get()))

    def poll(self):
        if self.current_id:
            self.render_job()
        p=self.player.current()
        if not self._sliding:
            self.seek_value.set(p)
        self.time_label.configure(text=f'{int(p//60)}:{int(p%60):02} / {int(self.player.length//60)}:{int(self.player.length%60):02}')
        if self.player.is_playing is False and self.play_btn.cget('text')=='❚❚ Пауза':
            self.play_btn.configure(text='▶ Слушать')
        self.root.after(1200,self.poll)

    def close(self):
        self.player.stop()
        if self._preview_path:
            self._preview_path.unlink(missing_ok=True)
        for pid in list(worker.ACTIVE):
            worker.pause(pid)
        self.root.destroy()
