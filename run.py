"""Audiobook QC desktop — native offline Windows / macOS application."""
from __future__ import annotations
import argparse
import sys
import traceback
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
    """A tiny deterministic PCM WAV for checking speech inference."""
    import wave
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 16000)


def _audible_test_wav(path: Path, seconds: int = 3) -> None:
    """A bounded audible synthetic tone that the sound detector cannot skip.

    Do not use silence here: AudioEventDetector.inspect intentionally skips
    very quiet audio. The CI smoke test must run ONNX inference for real.
    """
    import math
    import wave
    from array import array

    sample_rate = 16000
    samples = array("h", (
        int(2200 * math.sin(2 * math.pi * 440 * i / sample_rate))
        for i in range(sample_rate * seconds)
    ))
    # PCM WAV must always be little-endian.
    import sys as _sys
    if _sys.byteorder != "little":
        samples.byteswap()
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(samples.tobytes())


def _verify_word_export() -> None:
    """Test the same Word generator used for real 20-minute report exports.

    Importing python-docx is insufficient: missing default-header.xml and
    default-footer.xml only fail when a report accesses headers/footers.
    Run this in the *frozen binary*, not only in source unit tests.
    """
    from tempfile import TemporaryDirectory
    from docx import Document
    from audiobook_qc.report import generate_report

    _stage("Checking bundled Word templates by exporting a real DOCX")
    with TemporaryDirectory(prefix="aqc-word-smoke-") as directory:
        output = Path(directory) / "report.docx"
        generate_report(
            output,
            {"audio_name": "smoke.wav", "text_name": "smoke.docx",
             "report_policy": "dual-gigaam"},
            [], 0.0, 10.0,
        )
        assert output.is_file() and output.stat().st_size > 1000, "Word report missing or empty"
        document = Document(str(output))
        assert document.paragraphs, "Word report was not readable"
        assert document.sections[0].header.paragraphs, "Word header template failed"
        assert document.sections[0].footer.paragraphs, "Word footer template failed"
        assert any("Проверка озвучки" in p.text for p in document.paragraphs), \
            "Generated Word report has no expected heading"
    _stage("AUDIOBOOK_QC_WORD_REPORT_EXPORT_OK")


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
    import webview
    import onnxruntime
    from audiobook_qc.events import _manifest_verified, model_dir
    _manifest_verified(model_dir())
    from audiobook_qc.desktop_web import bundled_html
    html = bundled_html()
    assert "id=\"projectView\"" in html, "Desktop WebView interface missing"
    _verify_word_export()
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
        elif which == "events":
            _stage("Loading YAMNet event detector")
            from audiobook_qc.events import AudioEventDetector
            detector = AudioEventDetector()
            _stage("Preparing non-silent 3-second YAMNet test waveform")
            _audible_test_wav(wav, seconds=3)
            class _CountingSession:
                def __init__(self, wrapped):
                    self.wrapped = wrapped
                    self.calls = 0
                def run(self, *args, **kwargs):
                    self.calls += 1
                    return self.wrapped.run(*args, **kwargs)
            counter = _CountingSession(detector.session)
            detector.session = counter
            _stage("Running YAMNet 3-second REAL inference")
            issues = detector.inspect(wav, 0, 0, 3)
            if not isinstance(issues, list) or counter.calls < 1:
                raise RuntimeError("YAMNet smoke test did not execute any ONNX inference")
            _stage(f"YAMNet ONNX inference calls: {counter.calls}")
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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--smoke-gui', action='store_true')
    parser.add_argument('--test-whisper', action='store_true')
    parser.add_argument('--test-gigaam', action='store_true')
    parser.add_argument('--test-events', action='store_true')
    args = parser.parse_args(argv)
    # Catch smoke-test exceptions before the windowed PyInstaller bootloader
    # can open an invisible/modal error dialog and make GitHub Actions hang.
    try:
        if args.self_test:
            self_test()
        elif args.test_whisper:
            single_model_test("whisper")
        elif args.test_gigaam:
            single_model_test("gigaam")
        elif args.test_events:
            single_model_test("events")
        elif args.smoke_gui:
            from audiobook_qc.desktop_web import app_window
            app_window(smoke=True)
        else:
            try:
                from audiobook_qc.desktop_web import app_window
                app_window()
            except Exception:
                import datetime
                from audiobook_qc import worker
                path = worker.ROOT / 'crash.log'
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"{datetime.datetime.now().isoformat()}\n{traceback.format_exc()}", encoding='utf-8')
                try:
                    import tkinter as tk
                    from tkinter import messagebox
                    dialog = tk.Tk(); dialog.withdraw()
                    messagebox.showerror('Audiobook QC: ошибка запуска',
                                         f'Не удалось открыть приложение. Диагностика сохранена в {path}')
                    dialog.destroy()
                except Exception:
                    pass
                raise
        return 0
    except Exception:
        # --windowed sets sys.stderr to None on Windows. Always write the
        # details to the smoke log, return code 1, and never open any dialog.
        if args.self_test or args.test_whisper or args.test_gigaam or args.test_events or args.smoke_gui:
            _stage("AUDIOBOOK_QC_SMOKE_TEST_FAILED")
            _stage(traceback.format_exc())
            return 1
        raise


if __name__ == "__main__":
    sys.exit(main())
