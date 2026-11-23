"""Audiobook QC v0.6 — native offline Windows / macOS application."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).parent / "src"))

from audiobook_qc.offline import DEFAULT_MODEL, resolve_model, offline_environment

def _stage(message: str) -> None:
    """Write progress even in Windows --windowed executables with no stdout."""
    import os
    log = os.environ.get("AUDIOBOOK_QC_SMOKE_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as stream:
            stream.write(message + "\n")
    if sys.stdout is not None:
        print(message, flush=True)


def _silent_wav(path: Path) -> None:
    """A tiny deterministic PCM WAV for checking inference without user audio."""
    import wave
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 16000)


def self_test() -> None:
    """Fast, bounded bundle integrity check. Does NOT load both ASR models."""
    offline_environment()
    _stage("Checking bundled FFmpeg")
    import imageio_ffmpeg
    assert Path(imageio_ffmpeg.get_ffmpeg_exe()).is_file(), "Bundled FFmpeg missing"
    _stage("Checking primary model files")
    folder = resolve_model(DEFAULT_MODEL)
    assert (folder / "model.bin").stat().st_size > 100_000_000, "Whisper weights missing"
    _stage("Checking secondary model files")
    from audiobook_qc.dual_engine import resolve_secondary_dir, check_model_hash
    giga = resolve_secondary_dir()
    check_model_hash(giga)
    _stage("Checking application libraries")
    import docx
    import sounddevice
    _stage("AUDIOBOOK_QC_OFFLINE_BUNDLE_OK")


def single_model_test(which: str) -> None:
    """Independent processes: never keep Whisper and GigaAM in RAM together."""
    offline_environment()
    import tempfile
    with tempfile.TemporaryDirectory(prefix="aqc-ci-model-") as dirname:
        wav = Path(dirname) / "silence.wav"
        _silent_wav(wav)
        if which == "whisper":
            _stage("Loading Whisper model")
            from audiobook_qc.worker import load_model
            model = load_model(DEFAULT_MODEL)
            _stage("Running Whisper 1-second inference")
            chunks, _ = model.transcribe(
                str(wav), language="ru", vad_filter=False,
                word_timestamps=False, condition_on_previous_text=False,
                beam_size=1, temperature=0)
            list(chunks)
        elif which == "gigaam":
            import faulthandler
            import os
            _stage("Starting GigaAM diagnostics")
            # If a frozen Torch/Transformers import hangs, record live Python
            # stacks every 60 seconds, without needing a console window.
            stack_log_path = os.environ.get("AUDIOBOOK_QC_SMOKE_LOG")
            stack_log = open(stack_log_path, "a", encoding="utf-8") if stack_log_path else None
            if stack_log:
                faulthandler.dump_traceback_later(60, repeat=True, file=stack_log)
            try:
                _stage("Loading GigaAM model")
                from audiobook_qc.dual_engine import load_secondary_model, extract_transcription_text
                model = load_secondary_model(validate_hash=False, on_stage=_stage)
                _stage("Running GigaAM 1-second inference")
                result = model.transcribe(str(wav))
                assert isinstance(extract_transcription_text(result), str)
            finally:
                if stack_log:
                    faulthandler.cancel_dump_traceback_later()
                    stack_log.close()
        else:
            raise ValueError("Unknown model")
    _stage(f"AUDIOBOOK_QC_{which.upper()}_INFERENCE_OK")


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--smoke-gui', action='store_true')
    parser.add_argument('--test-whisper', action='store_true')
    parser.add_argument('--test-gigaam', action='store_true')
    args=parser.parse_args()
    if args.self_test:
        self_test()
    elif args.test_whisper:
        single_model_test("whisper")
    elif args.test_gigaam:
        single_model_test("gigaam")
    elif args.smoke_gui:
        from audiobook_qc.desktop import App
        import tkinter as tk
        app=tk.Tk()
        App(app)
        app.after(800,app.destroy)
        app.mainloop()
    else:
        try:
            from audiobook_qc.desktop import app_window
            app_window()
        except Exception:
            import traceback
            import datetime
            from audiobook_qc import worker
            path=worker.ROOT/'crash.log'
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(f"{datetime.datetime.now().isoformat()}\n{traceback.format_exc()}",encoding='utf-8')
            try:
                import tkinter as tk
                from tkinter import messagebox
                dialog=tk.Tk(); dialog.withdraw()
                messagebox.showerror('Audiobook QC: ошибка запуска',
                                     f'Не удалось открыть приложение. Диагностика сохранена в {path}')
                dialog.destroy()
            except Exception:
                pass
            raise
