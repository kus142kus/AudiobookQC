"""Chunked speech recognition, checkpointing, and partial/final report creation."""
from __future__ import annotations
from pathlib import Path
import json
import hashlib
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import traceback

from .analysis import compare, tokenize, detect_unscripted_profanity
from .acoustics import inspect_wav
from .report import generate_report

CHUNK_SECONDS = 120
OVERLAP_SECONDS = 4.0
MODEL_CACHE: dict[str, object] = {}


def app_data_root() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "AudiobookQC"
    if os.sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "AudiobookQC"
    return Path.home() / ".local" / "share" / "AudiobookQC"

ROOT = Path(os.environ.get("AUDIOBOOK_QC_DATA_DIR", str(app_data_root())))
ROOT.mkdir(parents=True, exist_ok=True)
STATE_LOCK = threading.RLock()
STOP_FLAGS: dict[str, threading.Event] = {}
ACTIVE: set[str] = set()


def job_dir(pid: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", pid):
        raise ValueError("Некорректный идентификатор проекта")
    return ROOT / "projects" / pid


def load_job(pid: str) -> dict:
    with STATE_LOCK:
        with (job_dir(pid) / "job.json").open(encoding="utf-8") as f:
            return json.load(f)


def save_job(job: dict) -> None:
    with STATE_LOCK:
        path = job_dir(job["id"]) / "job.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def quiet_subprocess_kwargs() -> dict:
    """Prevent FFmpeg from flashing a console window on Windows."""
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)} if os.name == "nt" else {}


def get_ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def audio_duration(filepath: Path) -> float:
    proc = subprocess.run([get_ffmpeg(), "-hide_banner", "-i", str(filepath)],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace",
                          **quiet_subprocess_kwargs())
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", proc.stderr)
    if not m:
        raise ValueError("Не удалось определить продолжительность аудио. Проверьте файл и формат.")
    return int(m.group(1))*3600 + int(m.group(2))*60 + float(m.group(3))


def source_text(path: Path) -> str:
    if path.suffix.lower() == ".txt":
        content = path.read_bytes()
        for enc in ("utf-8-sig", "utf-16", "cp1251"):
            try:
                return content.decode(enc)
            except UnicodeDecodeError:
                pass
        raise ValueError("Не удалось прочитать кодировку текстового файла")
    if path.suffix.lower() == ".docx":
        from docx import Document
        doc = Document(str(path))
        # Preserve interleaved paragraphs and tables in the order they occur.
        # Appending all tables at the end would scramble some manuscripts.
        paragraphs = []
        for element in doc.iter_inner_content():
            if hasattr(element, "rows"):
                for row in element.rows:
                    for cell in row.cells:
                        if cell.text.strip():
                            paragraphs.append(cell.text)
            elif element.text.strip():
                paragraphs.append(element.text)
        return "\n".join(paragraphs)
    raise ValueError("Текст должен быть DOCX или TXT")


def cut_audio(audio: Path, start: float, seconds: float, out: Path):
    cmd = [get_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-ss", str(start),
           "-i", str(audio), "-t", str(seconds), "-vn", "-ar", "16000", "-ac", "1",
           "-c:a", "pcm_s16le", str(out)]
    process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180,
                             **quiet_subprocess_kwargs())
    if process.returncode:
        raise RuntimeError("FFmpeg: " + process.stderr.decode("utf-8", "replace")[-700:])


def load_model(name: str):
    if name in MODEL_CACHE:
        return MODEL_CACHE[name]
    from faster_whisper import WhisperModel
    from .offline import resolve_model, offline_environment
    offline_environment()
    model_path = resolve_model(name)  # Local files only: never contacts Hugging Face.
    MODEL_CACHE.clear()
    import gc
    gc.collect()
    model = WhisperModel(str(model_path), device="cpu", compute_type="int8",
                         local_files_only=True)
    MODEL_CACHE[name] = model
    return model


def transcribe_chunk(model, wav: Path, offset: float, start: float, end: float) -> list[dict]:
    segments, info = model.transcribe(str(wav), language="ru", beam_size=5,
                                       word_timestamps=True, vad_filter=True,
                                       condition_on_previous_text=False)
    result = []
    for seg in segments:
        for w in (seg.words or []):
            raw = w.word.strip()
            if not raw:
                continue
            # Tokenise Whisper's punctuation-attached words consistently with source text.
            tokens = tokenize(raw)
            if not tokens:
                continue
            s = float(w.start if w.start is not None else seg.start) + offset
            e = float(w.end if w.end is not None else seg.end) + offset
            if not start <= (s+e)/2 < end:
                continue
            for token in tokens:
                result.append({"word": token, "start":round(s, 2), "end":round(e, 2)})
    return result


def model_worker(pid: str):
    stop_flag = STOP_FLAGS.setdefault(pid, threading.Event())
    try:
        job = load_job(pid)
        job["status"] = "model_loading"
        job["message"] = "Загрузка встроенной локальной модели (интернет не используется)..."
        save_job(job)
        pending = any(p["status"] != "done" for p in job["parts"])
        model = load_model(job["model"]) if pending else None
        # A genuinely independent decoder is mandatory for a dual-model job.
        # Never fall back silently to two passes of Whisper.
        verifier = None
        if pending and job.get('report_policy') == 'dual-gigaam':
            from .dual_engine import load_secondary_model
            job['message'] = 'Загрузка второй локальной модели GigaAM…'
            save_job(job)
            verifier = load_secondary_model()
        doc = tokenize(source_text(job_dir(pid) / "inputs" / job["text_stored"]))
        if len(doc) < 20:
            raise ValueError("В исходном тексте слишком мало слов для достоверного сравнения")
        audio = job_dir(pid) / "inputs" / job["audio_stored"]
        cursor = int(job.get("cursor", 0))
        for part in job["parts"]:
            if part["status"] == "done":
                # A power loss can happen after the part metadata was committed.
                # Rebuild any missing report from the already saved findings.
                part_file = job_dir(pid) / "parts" / f"part-{part['index']:03d}.json"
                report_file = job_dir(pid) / "reports" / f"Часть_{part['index']:03d}.docx"
                if not part_file.exists():
                    raise RuntimeError(f"Нет контрольных данных части {part['index']}; нужна диагностика проекта")
                if not report_file.exists():
                    issues = read_json(part_file, {}).get("issues", [])
                    generate_report(report_file, job, issues, part["start"], part["end"],
                                    reviews=read_json(job_dir(pid) / "reviews.json", {}))
                continue
            if stop_flag.is_set():
                job["status"] = "paused"
                job["message"] = "Остановлено между фрагментами. Можно продолжить."
                save_job(job)
                return
            part["status"] = "working"
            job["status"] = "running"
            save_job(job)
            part_words = []
            part_acoustic = []
            pos = part["start"]
            idx = 0
            while pos < part["end"] - 0.01:
                finish = min(part["end"], pos + CHUNK_SECONDS)
                chunk_path = job_dir(pid) / "chunks" / f"p{part['index']:03d}_c{idx:03d}.json"
                chunk_data = None
                if chunk_path.exists():
                    try:
                        cached = read_json(chunk_path)
                        if (isinstance(cached, dict) and isinstance(cached.get("words"), list)
                                and abs(float(cached.get("start", -1)) - pos) < .01
                                and abs(float(cached.get("end", -1)) - finish) < .01):
                            chunk_data = cached
                    except (ValueError, TypeError, OSError):
                        pass  # Damaged or partial checkpoint: decode it again.
                if chunk_data is None:
                    if stop_flag.is_set():
                        job["status"] = "paused"
                        part["status"] = "pending"
                        job["message"] = "Остановлено; готовые фрагменты сохранены."
                        save_job(job)
                        return
                    with tempfile.TemporaryDirectory(prefix="aqc-") as temp:
                        temp_file = Path(temp) / "speech.wav"
                        decode_start = max(0, pos - OVERLAP_SECONDS)
                        decode_end = min(job["duration"], finish + OVERLAP_SECONDS)
                        cut_audio(audio, decode_start, decode_end-decode_start, temp_file)
                        words = transcribe_chunk(model, temp_file, decode_start, pos, finish)
                        acoustic = inspect_wav(temp_file, decode_start, pos, finish)
                    chunk_data = {"start":pos,"end":finish,"words":words, "acoustic":acoustic}
                    write_json(chunk_path, chunk_data)
                part_words.extend(chunk_data["words"])
                part_acoustic.extend(chunk_data.get("acoustic", []))
                pos = finish
                idx += 1
                job["processed_seconds"] = max(job.get("processed_seconds", 0), finish)
                job["message"] = f"Анализ: {part['index']}-я часть из {len(job['parts'])} · {int(finish/60)} мин"
                save_job(job)
            # Part completed: emit its report IMMEDIATELY, no waiting for other parts.
            previous = read_json(job_dir(pid) / "parts" / f"part-{part['index']-1:03d}.json", {}) if part["index"] > 1 else {}
            issues, new_cursor, alignment = compare(doc, part_words, cursor, previous.get("tail_words", []))
            if not part_words:
                for issue in issues:
                    if issue["kind"] == "uncertain":
                        issue["start"], issue["end"] = part["start"], part["end"]
                        issue["id"] = hashlib.sha256(
                            f"empty|{part['index']}|{part['start']}".encode()).hexdigest()[:14]
            # Verify important textual disagreements on a different audio crop.
            # This deliberately favours fewer false alarms over a large noisy list.
            if job.get('report_policy') == 'dual-gigaam':
                from .dual_engine import verify_with_gigaam
                issues = verify_with_gigaam(verifier, audio, issues, job['duration'],
                                             cut_audio, should_stop=stop_flag.is_set)
            else:
                from .verification import recheck_issues
                issues = recheck_issues(model, audio, issues, job['duration'], cut_audio,
                                        should_stop=stop_flag.is_set)
            if stop_flag.is_set():
                job["status"] = "paused"
                part["status"] = "pending"
                job["message"] = "Остановлено перед созданием отчёта. Распознанные фрагменты сохранены."
                save_job(job)
                return
            issues.extend(part_acoustic)
            issues.extend(detect_unscripted_profanity(doc, part_words))
            issues.sort(key=lambda i: (i["start"], i["kind"]))
            for issue in issues:
                issue["part"] = part["index"]
            part_file = job_dir(pid) / "parts" / f"part-{part['index']:03d}.json"
            write_json(part_file, {"issues":issues,"alignment":alignment,"tail_words":part_words[-50:],
                                   "cursor_after":new_cursor, "token_count":len(part_words)})
            reviews = read_json(job_dir(pid) / "reviews.json", {})
            report_path = job_dir(pid) / "reports" / f"Часть_{part['index']:03d}.docx"
            generate_report(report_path, job, issues, part["start"], part["end"], reviews=reviews)
            cursor = new_cursor
            job["cursor"] = cursor
            part["status"] = "done"
            part["issue_count"] = len(issues)
            part["high_count"] = sum(
                i["severity"] == "high" and (
                    i.get("kind") not in ("replacement", "omission", "extra", "repeat")
                    or job.get('report_policy') != 'dual-gigaam'
                    or i.get('dual_confirmed')) for i in issues)
            part["alignment"] = alignment
            job["message"] = f"Отчёт для части {part['index']} готов и доступен для скачивания"
            save_job(job)
        all_issues = []
        for part in job["parts"]:
            all_issues.extend(read_json(job_dir(pid) / "parts" / f"part-{part['index']:03d}.json", {}).get("issues",[]))
        generate_report(job_dir(pid) / "reports" / "Полный_отчёт.docx", job,
                        all_issues, 0, job["duration"], final=True,
                        reviews=read_json(job_dir(pid) / "reviews.json", {}))
        job["status"] = "completed"
        job["processed_seconds"] = job["duration"]
        job["message"] = "Все части обработаны. Полный отчёт готов."
        save_job(job)
    except Exception as ex:
        job = load_job(pid)
        job["status"] = "error"
        job["message"] = f"Ошибка: {type(ex).__name__}: {ex}"
        (job_dir(pid) / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
        save_job(job)
    finally:
        with STATE_LOCK:
            ACTIVE.discard(pid)


def start(pid: str):
    with STATE_LOCK:
        if pid in ACTIVE:
            return
        if ACTIVE:
            raise ValueError("Уже проверяется другая книга. Дождитесь окончания или приостановите её.")
        job = load_job(pid)
        if job["status"] == "completed":
            return
        ACTIVE.add(pid)
        STOP_FLAGS[pid] = threading.Event()
    thread = threading.Thread(target=model_worker, args=(pid,), daemon=True, name=f"job-{pid[:8]}")
    thread.start()


def pause(pid: str):
    STOP_FLAGS.setdefault(pid, threading.Event()).set()
    job = load_job(pid)
    if job["status"] in ("running", "model_loading"):
        job["status"] = "pausing"
        job["message"] = "Остановка после текущего фрагмента..."
        save_job(job)


def all_issues(pid: str) -> list[dict]:
    result = []
    job = load_job(pid)
    for part in job["parts"]:
        f = job_dir(pid) / "parts" / f"part-{part['index']:03d}.json"
        result.extend(read_json(f, {}).get("issues", []))
    return result


def refresh_reports(pid: str):
    job = load_job(pid)
    reviews = read_json(job_dir(pid) / "reviews.json", {})
    for part in job["parts"]:
        if part["status"] != "done":
            continue
        issues = read_json(job_dir(pid) / "parts" / f"part-{part['index']:03d}.json", {}).get("issues",[])
        generate_report(job_dir(pid) / "reports" / f"Часть_{part['index']:03d}.docx",
                        job, issues, part["start"], part["end"], reviews=reviews)
    if job["status"] == "completed":
        generate_report(job_dir(pid) / "reports" / "Полный_отчёт.docx",
                        job, all_issues(pid), 0, job["duration"], final=True, reviews=reviews)
