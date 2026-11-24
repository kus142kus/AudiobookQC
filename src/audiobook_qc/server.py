"""Loopback-only local web UI: large-file streaming uploads, status and downloads."""
from __future__ import annotations
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit
import io
import csv
import platform
from datetime import datetime, timezone
import shutil
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import uuid
import webbrowser
import zipfile

from . import worker, __version__
from .analysis import fmt_time

WEB = (Path(getattr(sys, "_MEIPASS")) / "web" if getattr(sys, "frozen", False)
       else Path(__file__).resolve().parents[2] / "web")
AUDIO_TYPES = {".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg", ".mp4"}
TEXT_TYPES = {".docx", ".txt"}
UPLOADS = worker.ROOT / "uploads"
UPLOADS.mkdir(parents=True, exist_ok=True)


class Handler(BaseHTTPRequestHandler):
    server_version = f"AudiobookQC/{__version__}"

    def log_message(self, format, *args):
        if os.environ.get("AUDIOBOOK_QC_DEBUG"):
            super().log_message(format, *args)

    def _json(self, status: int, obj):
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _send(self, status: int, body: bytes, content_type: str, filename: str | None = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if filename:
            from urllib.parse import quote
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(filename))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (ConnectionResetError, BrokenPipeError):
            pass

    def _body_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= 1024*1024:
            raise ValueError("Неверный размер запроса")
        return json.loads(self.rfile.read(length))

    def _origin_ok(self):
        # Refuse DNS-rebinding Host headers and browser cross-origin requests.
        # The server binds only to 127.0.0.1; never expose it on a public interface.
        host = self.headers.get("Host", "").lower()
        allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        if host not in allowed:
            return False
        origin = self.headers.get("Origin")
        if origin and origin.lower() not in {f"http://{h}" for h in allowed}:
            return False
        site = self.headers.get("Sec-Fetch-Site", "same-origin").lower()
        return site in ("same-origin", "none", "")

    def do_GET(self):
        try:
            if not self._origin_ok():
                return self._json(403, {"error":"Доступ разрешён только с локального интерфейса"})
            self._get()
        except (ValueError, FileNotFoundError) as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:
            self._json(500, {"error": str(exc)})

    def _get(self):
        route = urlsplit(self.path)
        path = route.path
        if path == "/api/projects":
            result = []
            folder = worker.ROOT / "projects"
            for p in sorted(folder.glob("*/job.json") if folder.exists() else [], reverse=True):
                job = worker.read_json(p, {})
                if job.get("status") in ("running", "model_loading", "pausing") and job.get("id") not in worker.ACTIVE:
                    job["status"] = "paused"
                    job["message"] = "После перезапуска можно продолжить анализ"
                    worker.save_job(job)
                result.append({k:job.get(k) for k in ("id", "audio_name", "status", "processed_seconds", "duration", "message")})
            return self._json(200, result)
        m = re.fullmatch(r"/api/projects/([0-9a-f]{32})", path)
        if m:
            pid = m.group(1)
            job = worker.load_job(pid)
            if job["status"] in ("running", "model_loading", "pausing") and pid not in worker.ACTIVE:
                job["status"] = "paused"
                worker.save_job(job)
            issues = worker.all_issues(pid)
            reviews = worker.read_json(worker.job_dir(pid)/"reviews.json", {})
            for issue in issues:
                issue["review"] = reviews.get(issue["id"], "todo")
            job["issues"] = issues
            return self._json(200, job)
        m = re.fullmatch(r"/api/projects/([0-9a-f]{32})/part/(\d+)", path)
        if m:
            pid, number = m.group(1), int(m.group(2))
            job = worker.load_job(pid)
            if number < 1 or number > len(job["parts"]):
                raise ValueError("Часть не найдена")
            if job["parts"][number-1]["status"] != "done":
                raise ValueError("Часть ещё не готова")
            return self._download(worker.job_dir(pid)/"reports"/f"Часть_{number:03d}.docx")
        m = re.fullmatch(r"/api/projects/([0-9a-f]{32})/final", path)
        if m:
            pid = m.group(1)
            if worker.load_job(pid)["status"] != "completed":
                raise ValueError("Полный отчёт ещё не готов")
            return self._download(worker.job_dir(pid)/"reports"/"Полный_отчёт.docx")
        m = re.fullmatch(r"/api/projects/([0-9a-f]{32})/zip", path)
        if m:
            pid = m.group(1)
            job = worker.load_job(pid)
            ready = [worker.job_dir(pid)/"reports"/f"Часть_{p['index']:03d}.docx"
                     for p in job["parts"] if p["status"] == "done"]
            if not ready:
                raise ValueError("Нет готовых отчётов")
            out = io.BytesIO()
            with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as z:
                for p in ready:
                    z.write(p, p.name)
            return self._send(200, out.getvalue(), "application/zip", "Готовые_части.zip")
        m = re.fullmatch(r"/api/projects/([0-9a-f]{32})/diagnostic\.json", path)
        if m:
            pid = m.group(1)
            job = worker.load_job(pid)
            # No audio, manuscript, recognized phrases, or user file paths.
            info = {"app_version": __version__, "platform": platform.system(),
                    "python_version": platform.python_version(), "status": job["status"],
                    "duration_seconds": job["duration"], "part_minutes": job["part_minutes"],
                    "model": job["model"], "completed_parts": sum(p["status"] == "done" for p in job["parts"]),
                    "total_parts": len(job["parts"]), "processed_seconds": job["processed_seconds"],
                    "error_type": job.get("message", "").split(":", 2)[1].strip()
                    if job.get("status") == "error" and ":" in job.get("message", "") else ""}
            return self._send(200, json.dumps(info, ensure_ascii=False, indent=2).encode("utf-8"),
                              "application/json; charset=utf-8", "Диагностика.json")
        m = re.fullmatch(r"/api/projects/([0-9a-f]{32})/issues\.csv", path)
        if m:
            pid = m.group(1)
            worker.load_job(pid)
            reviews = worker.read_json(worker.job_dir(pid) / "reviews.json", {})
            out = io.StringIO()
            writer = csv.writer(out, delimiter=";")
            writer.writerow(["Часть", "Начало", "Конец", "Категория", "Приоритет", "Уверенность",
                             "Произнесено", "По тексту", "Причина", "Статус"])
            for x in worker.all_issues(pid):
                writer.writerow([x.get("part", ""), fmt_time(x["start"]), fmt_time(x["end"]),
                                 x["kind"], x["severity"], x["confidence"], x["spoken"],
                                 x["expected"], x["reason"], reviews.get(x["id"], "todo")])
            return self._send(200, b"\xef\xbb\xbf" + out.getvalue().encode("utf-8"),
                              "text/csv; charset=utf-8", "Все_замечания.csv")
        m = re.fullmatch(r"/api/projects/([0-9a-f]{32})/audio", path)
        if m:
            pid = m.group(1)
            job = worker.load_job(pid)
            q = parse_qs(route.query)
            start = max(0, float(q.get("start", [0])[0]))
            start = min(start, job["duration"])
            seconds = min(20.0, max(1.0, float(q.get("duration", [10])[0])))
            if not (0 <= start <= job["duration"] and 1 <= seconds <= 20):
                raise ValueError("Некорректный диапазон аудио")
            audio = worker.job_dir(pid)/"inputs"/job["audio_stored"]
            cp = subprocess.run([worker.get_ffmpeg(), "-hide_banner", "-loglevel", "error",
                                 "-ss", str(start), "-i", str(audio), "-t", str(seconds),
                                 "-ar", "16000", "-ac", "1", "-f", "wav", "pipe:1"],
                                capture_output=True, timeout=50)
            if cp.returncode:
                raise ValueError("Не удалось получить аудиофрагмент")
            return self._send(200, cp.stdout, "audio/wav")
        # static files must not escape packaged 'web' directory
        relative = "index.html" if path in ("", "/") else path.lstrip("/")
        file = (WEB / relative).resolve()
        if WEB.resolve() not in file.parents or not file.is_file():
            raise FileNotFoundError("Страница не найдена")
        mime = mimetypes.guess_type(str(file))[0] or "application/octet-stream"
        return self._send(200, file.read_bytes(), mime + ("; charset=utf-8" if mime.startswith("text/") or mime == "application/javascript" else ""))

    def _download(self, path: Path):
        return self._send(200, path.read_bytes(),
                          "application/vnd.openxmlformats-officedocument.wordprocessingml.document", path.name)

    def do_POST(self):
        try:
            if not self._origin_ok():
                return self._json(403, {"error":"Запрос не с локального интерфейса"})
            self._post()
        except (ValueError, FileNotFoundError, KeyError) as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:
            self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

    def _post(self):
        route = urlsplit(self.path)
        path = route.path
        if path == "/api/upload":
            kind = parse_qs(route.query).get("kind", [""])[0]
            if kind not in ("audio", "text"):
                raise ValueError("Ожидался аудиофайл или текст")
            filename = Path(unquote(self.headers.get("X-File-Name", "file"))).name
            ext = Path(filename).suffix.lower()
            if ext not in (AUDIO_TYPES if kind == "audio" else TEXT_TYPES):
                raise ValueError("Неподдерживаемый формат: " + ext)
            n = int(self.headers.get("Content-Length", 0))
            if n <= 0 or n > 12*1024**3:
                raise ValueError("Неверный размер файла")
            if kind == "text" and n > 50 * 1024**2:
                raise ValueError("Текстовый файл слишком большой (максимум 50 МБ)")
            # Browsers send the full file to loopback: a second local copy is needed.
            # Fail early rather than filling the disk during a six-hour upload.
            if shutil.disk_usage(UPLOADS).free < n + 256 * 1024**2:
                raise ValueError("Недостаточно места на диске для загрузки")
            uid = uuid.uuid4().hex
            path_out = UPLOADS / (uid + ext)
            remaining = n
            try:
                with path_out.open("wb") as out:
                    while remaining:
                        block = self.rfile.read(min(1024*1024, remaining))
                        if not block:
                            raise ValueError("Загрузка файла прервана")
                        out.write(block)
                        remaining -= len(block)
            except Exception:
                path_out.unlink(missing_ok=True)
                raise
            return self._json(200, {"id":uid, "name":filename, "ext":ext, "kind":kind})
        if path == "/api/projects":
            data = self._body_json()
            audio, text = data["audio"], data["text"]
            if audio["kind"] != "audio" or text["kind"] != "text":
                raise ValueError("Необходимо выбрать аудио и исходный текст")
            if audio.get("ext") not in AUDIO_TYPES or text.get("ext") not in TEXT_TYPES:
                raise ValueError("Некорректный формат загруженного файла")
            if not re.fullmatch(r"[0-9a-f]{32}", audio["id"]) or not re.fullmatch(r"[0-9a-f]{32}", text["id"]):
                raise ValueError("Некорректные идентификаторы файлов")
            part_minutes = int(data.get("part_minutes", 20))
            model = data.get("model", "small")
            if part_minutes not in (5,10,20,30,60) or model not in ("base", "small", "medium", "large-v3"):
                raise ValueError("Недопустимые настройки")
            audio_src = UPLOADS / (audio["id"] + audio["ext"])
            text_src = UPLOADS / (text["id"] + text["ext"])
            if not audio_src.is_file() or not text_src.is_file():
                raise ValueError("Загруженный файл не найден")
            document_tokens = worker.tokenize(worker.source_text(text_src))
            if len(document_tokens) < 20:
                raise ValueError("В тексте слишком мало слов для надёжной проверки")
            duration = worker.audio_duration(audio_src)
            if duration <= 0 or duration > 48*3600:
                raise ValueError("Продолжительность должна быть от 1 секунды до 48 часов")
            pid = uuid.uuid4().hex
            path_base = worker.job_dir(pid)
            (path_base/"inputs").mkdir(parents=True, exist_ok=True)
            a_file = "audio" + audio["ext"]
            t_file = "text" + text["ext"]
            shutil.move(str(audio_src), str(path_base/"inputs"/a_file))
            shutil.move(str(text_src), str(path_base/"inputs"/t_file))
            part_span = part_minutes * 60
            parts = [{"index":i+1, "start":i*part_span,
                      "end":min(duration,(i+1)*part_span), "status":"pending", "issue_count":0}
                     for i in range(int((duration + part_span - .0001) // part_span))]
            job = {"id":pid,"audio_name":audio["name"],"text_name":text["name"],
                   "audio_stored":a_file,"text_stored":t_file,"duration":duration,
                   "part_minutes":part_minutes,"model":model,"status":"ready",
                   "processed_seconds":0,"cursor":0,"message":"Проект создан. Можно запускать проверку.",
                   "parts":parts}
            worker.save_job(job)
            return self._json(201, {"id":pid})
        m = re.fullmatch(r"/api/projects/([0-9a-f]{32})/(start|pause|review|delete)",path)
        if m:
            pid, action = m.groups()
            worker.load_job(pid)
            if action == "delete":
                with worker.STATE_LOCK:
                    if pid in worker.ACTIVE:
                        raise ValueError("Сначала приостановите анализ и дождитесь остановки")
                    shutil.rmtree(worker.job_dir(pid))
            elif action == "start":
                worker.start(pid)
            elif action == "pause":
                worker.pause(pid)
            else:
                data = self._body_json()
                issue_id, status = data.get("id"), data.get("status")
                if status not in ("todo", "fixed", "false"):
                    raise ValueError("Неверный статус проверки")
                if issue_id not in {x["id"] for x in worker.all_issues(pid)}:
                    raise ValueError("Ошибка не найдена")
                path_reviews = worker.job_dir(pid)/"reviews.json"
                reviews = worker.read_json(path_reviews, {})
                reviews[issue_id] = status
                worker.write_json(path_reviews, reviews)
                worker.refresh_reports(pid)
            return self._json(200, {"ok":True})
        if path == "/api/shutdown":
            self._json(200,{"ok":True})
            for pid in list(worker.ACTIVE):
                worker.pause(pid)
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        raise ValueError("Неизвестная команда")


def launch(port: int = 0, no_browser: bool=False):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{server.server_port}/"
    print("Audiobook QC запущен:", url, flush=True)
    if not no_browser:
        threading.Timer(0.7, lambda:webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
