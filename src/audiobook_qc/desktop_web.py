"""Offline HTML/CSS desktop window backed by the existing audiobook worker.

No user browser, HTTP server or remote assets. The JS<->Python bridge is
restricted to explicitly declared methods and project IDs are validated by
worker.job_dir. See web/index.html, web/style.css and web/app.js.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from . import worker
from .analysis import fmt_time, effective_severity
from .desktop import create_project, list_projects, delete_saved_project, ClipPlayer
from .offline import DEFAULT_MODEL, offline_environment


def web_assets() -> Path:
    """Bundled web assets from PyInstaller, or repository during development."""
    if getattr(sys, "frozen", False):
        root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        location = root / "web"
    else:
        location = Path(__file__).resolve().parents[2] / "web"
    if not all((location / name).is_file() for name in ("index.html", "style.css", "app.js")):
        raise FileNotFoundError(f"Отсутствуют файлы интерфейса: {location}")
    return location


def bundled_html() -> str:
    """An inline page so pywebview needs no HTTP server, even on Windows."""
    root = web_assets()
    html = (root / "index.html").read_text(encoding="utf-8")
    style = (root / "style.css").read_text(encoding="utf-8")
    script = (root / "app.js").read_text(encoding="utf-8")
    return html.replace("<!-- AQC_STYLES -->", "<style>"+style+"</style>").replace(
        "<!-- AQC_SCRIPT -->", "<script>"+script+"</script>")


AUDIO_SUFFIXES = frozenset(('.wav', '.mp3', '.m4a', '.flac', '.aac', '.ogg', '.mp4'))
TEXT_SUFFIXES = frozenset(('.txt', '.docx'))


def discover_input_pair(folder: Path) -> dict[str, str]:
    """Choose an unambiguous one-audio/one-manuscript pair in a folder.

    Never guess which recording matches a manuscript, and never recurse into
    private nested folders. The user may still choose files separately.
    """
    folder = Path(folder).expanduser().resolve(strict=True)
    if not folder.is_dir():
        raise ValueError('Выбранный путь не является папкой')
    files = [p for p in folder.iterdir() if p.is_file() and not p.is_symlink()]
    audio = [p for p in files if p.suffix.lower() in AUDIO_SUFFIXES]
    text = [p for p in files if p.suffix.lower() in TEXT_SUFFIXES]
    if len(audio) != 1 or len(text) != 1:
        raise ValueError(
            f'В папке найдено аудиофайлов: {len(audio)}, текстовых документов: {len(text)}. '
            'Для автоматического выбора нужны ровно один аудиофайл и один DOCX/TXT. '
            'Если файлов больше, выбери нужные по отдельности.'
        )
    return {'audio': str(audio[0]), 'text': str(text[0])}


class Bridge:
    def __init__(self):
        self.window = None
        self.player = ClipPlayer()
        self.clip_pid = None
        self.clip_issue = None
        self.clip_start = 0.0
        self.clip_path: Path | None = None
        self._audio_lock = threading.RLock()

    def get_projects(self):
        result = []
        for job in list_projects():
            ready = sum(p.get("status") == "done" for p in job.get("parts", []))
            result.append({"id": job["id"], "name": job.get("audio_name", "Аудиокнига"),
                           "duration": fmt_time(job.get("duration", 0)), "status": job.get("status", "ready"),
                           "ready": ready, "total": len(job.get("parts", []))})
        return result

    def get_project(self, pid: str, uncertain: bool = False):
        job = worker.load_job(pid)
        issues = worker.all_issues(pid)
        reviews = worker.read_json(worker.job_dir(pid) / "reviews.json", {})
        filtered = []
        for issue in issues:
            if not uncertain:
                if effective_severity(issue) != "high" and issue.get("kind") not in ("sound_event","hum","hiss","click","level_jump","stress","intonation"):
                    continue
                if (job.get("report_policy") == "dual-gigaam"
                        and issue.get("kind") in ("replacement", "omission", "extra", "repeat")
                        and not issue.get("dual_confirmed")):
                    continue
                if reviews.get(issue.get("id")) == "false":
                    continue
            filtered.append({"id": issue["id"], "start": issue.get("start", 0.0),
                             "end": issue.get("end", 0.0), "time": fmt_time(issue.get("start", 0)),
                             "kind": issue.get("kind", "uncertain"),
                             "spoken": issue.get("spoken", ""), "expected": issue.get("expected", ""),
                             "reason": issue.get("reason", ""),
                             "review": reviews.get(issue["id"], "todo"),
                             "confirmed": bool(issue.get("dual_confirmed"))})
        parts = [{"index": p["index"], "start": fmt_time(p["start"]),
                  "end": fmt_time(p["end"]), "status": p["status"],
                  "count": p.get("issue_count", 0)} for p in job.get("parts", [])]
        def selected_high(issue):
            return (effective_severity(issue) == 'high'
                    and reviews.get(issue.get('id')) != 'false'
                    and (job.get('report_policy') != 'dual-gigaam'
                         or issue.get('kind') not in ('replacement', 'omission', 'extra', 'repeat')
                         or issue.get('dual_confirmed')))

        return {"id": pid, "name": job.get("audio_name", ""),
                "text_name": job.get("text_name", ""), "status": job.get("status", "ready"),
                "message": job.get("message", ""), "duration": job.get("duration", 0),
                "duration_label": fmt_time(job.get("duration", 0)),
                "processed": job.get("processed_seconds", 0),
                "processed_label": fmt_time(job.get("processed_seconds", 0)),
                "model": job.get("model", ""), "part_minutes": job.get("part_minutes", 20),
                "report_policy": job.get("report_policy", ""),
                "parts": parts, "issues": filtered, "all_count": len(issues),
                "high_count": sum(selected_high(x) for x in issues),
                "repeat_count": sum(x.get("kind") == 'repeat' and selected_high(x) for x in issues)}

    def pick_folder(self):
        """Native folder dialog for the usual two-file audiobook workflow."""
        import webview
        chosen = self.window.create_file_dialog(webview.FOLDER_DIALOG, allow_multiple=False)
        return discover_input_pair(Path(chosen[0])) if chosen else None

    def pick_file(self, category: str):
        if category not in ("audio", "text"):
            raise ValueError("Неизвестный тип файла")
        import webview
        filt = ("Audio (*.mp3;*.wav;*.m4a;*.flac;*.aac;*.ogg;*.mp4)",) if category == "audio" else ("Документ (*.docx;*.txt)",)
        chosen = self.window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False, file_types=filt)
        return str(chosen[0]) if chosen else ""

    def new_project(self, audio: str, manuscript: str, minutes: int, glossary: str = ""):
        pid = create_project(Path(audio), Path(manuscript), part_minutes=int(minutes), glossary_text=glossary)
        # Start in background without blocking the UI.
        worker.start(pid)
        return pid

    def resume(self, pid: str):
        job = worker.load_job(pid)
        if job.get("status") == "completed":
            return False
        if job.get("model") != DEFAULT_MODEL or job.get("report_policy") != "dual-gigaam":
            raise ValueError("Этот проект создан другой версией анализатора. Его готовые отчёты доступны, но продолжение не поддерживается.")
        worker.start(pid)
        return True

    def pause(self, pid: str):
        worker.pause(pid)
        return True

    def delete(self, pid: str):
        # Confirmation is requested in JS, refusal during active analysis is enforced here.
        with self._audio_lock:
            if self.clip_pid == pid:
                self._stop_clip()
        delete_saved_project(pid)
        return True

    def review(self, pid: str, issue_id: str, status: str):
        if status not in ("fixed", "false", "todo"):
            raise ValueError("Недопустимый статус")
        if issue_id not in {issue["id"] for issue in worker.all_issues(pid)}:
            raise ValueError("Замечание больше недоступно")
        path = worker.job_dir(pid) / "reviews.json"
        reviews = worker.read_json(path, {})
        reviews[issue_id] = status
        worker.write_json(path, reviews)
        worker.refresh_reports(pid)
        return True

    def open_reports(self, pid: str):
        worker.load_job(pid)
        folder = worker.job_dir(pid) / "reports"
        folder.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(folder))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)], **worker.quiet_subprocess_kwargs())
        else:
            subprocess.Popen(["xdg-open", str(folder)], **worker.quiet_subprocess_kwargs())
        return True

    def save_report(self, pid: str, part_index: int):
        job = worker.load_job(pid)
        ready = {int(p["index"]) for p in job["parts"] if p.get("status")=="done"}
        index = int(part_index)
        if index == 0:
            if job.get('status') != 'completed':
                raise ValueError('Итоговый отчёт доступен после завершения проверки')
            source = worker.job_dir(pid) / 'reports' / 'Полный_отчёт.docx'
        else:
            if index not in ready:
                raise ValueError('Эта часть ещё не готова')
            source = worker.job_dir(pid) / 'reports' / f'Часть_{index:03d}.docx'
        if not source.is_file():
            raise FileNotFoundError("Word-отчёт не найден")
        import webview
        target = self.window.create_file_dialog(webview.SAVE_DIALOG, save_filename=source.name)
        if not target:
            return False
        if isinstance(target, (tuple, list)):
            target = target[0]
        target = Path(target)
        if target.suffix.lower() != '.docx':
            target = target.with_suffix('.docx')
        shutil.copy2(source, target)
        return True

    def load_clip(self, pid: str, issue_id: str):
        job = worker.load_job(pid)
        issue = next((x for x in worker.all_issues(pid) if x["id"] == issue_id), None)
        if issue is None:
            raise ValueError("Замечание не найдено")
        start = max(0.0, float(issue.get("start", 0)) - 18)
        seconds = min(180.0, max(0.1, job["duration"] - start))
        with self._audio_lock:
            self._stop_clip()
            wav = worker.job_dir(pid) / ("preview-" + uuid.uuid4().hex + ".wav")
            try:
                worker.cut_audio(worker.job_dir(pid) / "inputs" / job["audio_stored"], start, seconds, wav)
                self.player.load(wav)
            except Exception:
                wav.unlink(missing_ok=True)
                raise
            self.clip_path = wav
            self.clip_pid = pid
            self.clip_issue = issue_id
            self.clip_start = start
            return self.player_state()

    def _stop_clip(self):
        self.player.stop()
        if self.clip_path:
            self.clip_path.unlink(missing_ok=True)
            self.clip_path = None
        self.clip_pid = None
        self.clip_issue = None

    def toggle_play(self):
        with self._audio_lock:
            if self.player.is_playing:
                self.player.stop()
            else:
                self.player.play()
            return self.player_state()

    def seek(self, seconds: float):
        with self._audio_lock:
            self.player.seek(float(seconds))
            return self.player_state()

    def set_speed(self, speed: float):
        value = float(speed)
        if value not in (0.75, 1.0, 1.25, 1.5):
            raise ValueError('Недопустимая скорость воспроизведения')
        with self._audio_lock:
            was_playing = self.player.is_playing
            self.player.stop()
            self.player.speed = value
            if was_playing:
                self.player.play()
            return self.player_state()

    def player_state(self):
        return {"loaded": self.player.samples is not None and self.clip_pid is not None,
                "playing": bool(self.player.is_playing), "position": self.player.current(),
                "duration": self.player.length, "absolute_start": self.clip_start}

    def shutdown(self):
        with self._audio_lock:
            self._stop_clip()
        for pid in list(worker.ACTIVE):
            worker.pause(pid)
        return True


def preferred_gui_backend(platform: str | None = None) -> str | None:
    """Use bundled Qt WebEngine on Windows/Linux, avoiding pythonnet/WinForms entirely.

    The former Windows WinForms renderer required Python.Runtime.dll and could
    pass CI while failing on a user's PC. macOS keeps its built-in WKWebView.
    """
    platform = sys.platform if platform is None else platform
    return "qt" if platform in ("win32", "linux") else None


def verify_gui_backend() -> str:
    """Fail with an actionable error before starting the GUI or loading models."""
    backend = preferred_gui_backend()
    if backend == "qt":
        # QtPy must not select an unrelated installed Qt binding.
        os.environ["QT_API"] = "pyside6"
        try:
            from PySide6.QtCore import QLibraryInfo  # noqa: F401
            from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
            from PySide6.QtWebChannel import QWebChannel  # noqa: F401
        except (ImportError, OSError) as exc:
            raise RuntimeError(
                "Не найден встроенный Qt WebEngine. Требуется исправленная "
                "сборка Audiobook QC; устанавливать Python или .NET не нужно."
            ) from exc
        return "qt"
    return "system-webkit"


def app_window(smoke: bool = False):
    """Show the bundled HTML in a native window, never in an external browser."""
    offline_environment()
    renderer = preferred_gui_backend()
    verify_gui_backend()
    import webview
    api = Bridge()
    window = webview.create_window("Audiobook QC — Проверка озвучки", html=bundled_html(),
                                   js_api=api, width=1280, height=850,
                                   min_size=(900, 600), background_color="#f5f8fb")
    api.window = window
    window.events.closed += api.shutdown
    kwargs = {"debug": False, "http_server": False}
    if renderer:
        kwargs["gui"] = renderer
    if smoke:
        # THIS IS A REAL UI test, not merely an import or a 1-second timer.
        # Failure must reach the caller (and fail GitHub Actions).
        import time
        problems: list[str] = []

        def check_then_close():
            try:
                if not window.events.loaded.wait(30):
                    raise RuntimeError("Desktop window never reached DOM-loaded state")
                # Injected JS bridge may become ready just after DOM-loaded.
                for _ in range(60):
                    result = window.evaluate_js('''
                        Boolean(document.getElementById("projectView") &&
                                document.getElementById("partsGrid") &&
                                window.pywebview && window.pywebview.api &&
                                window.pywebview.api.get_projects)
                    ''')
                    if result:
                        break
                    time.sleep(0.2)
                else:
                    raise RuntimeError("UI loaded but project elements or JS-Python bridge missing")
            except Exception as exc:
                problems.append(f"{type(exc).__name__}: {exc}")
            finally:
                window.destroy()

        webview.start(func=check_then_close, **kwargs)
        if problems:
            raise RuntimeError("Native GUI smoke test failed: " + "; ".join(problems))
    else:
        webview.start(**kwargs)
