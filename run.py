"""Audiobook QC v0.6 — native offline Windows / macOS application."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).parent / "src"))

from audiobook_qc.offline import DEFAULT_MODEL, resolve_model, offline_environment

def self_test():
    offline_environment()
    import imageio_ffmpeg
    import faster_whisper
    import ctranslate2
    import docx
    import sounddevice
    import tkinter
    from audiobook_qc import worker
    assert Path(imageio_ffmpeg.get_ffmpeg_exe()).is_file(), "Bundled FFmpeg missing"
    assert callable(worker.cut_audio)
    model_path=resolve_model(DEFAULT_MODEL)
    assert (model_path / "model.bin").stat().st_size > 100_000_000, "Model weights are missing"
    from audiobook_qc.dual_engine import resolve_secondary_dir, check_model_hash, load_secondary_model
    secondary_dir=resolve_secondary_dir()
    check_model_hash(secondary_dir)
    # Test actual model loading, not just existence of a large file.
    model=worker.load_model(DEFAULT_MODEL)
    assert model is not None
    second_model=load_secondary_model(validate_hash=False)
    assert second_model is not None
    # Probe the *real* GigaAM result shape: v0.5 mistakenly required a string,
    # while upstream returns a TranscriptionResult dataclass with .text.
    import tempfile, wave
    from audiobook_qc.dual_engine import extract_transcription_text
    with tempfile.TemporaryDirectory(prefix='aqc-smoke-') as folder:
        test_wav=Path(folder)/'quiet.wav'
        with wave.open(str(test_wav), 'wb') as f:
            f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000)
            f.writeframes(b'\0\0'*16000*2)
        secondary_response=second_model.transcribe(str(test_wav))
        assert isinstance(extract_transcription_text(secondary_response),str)
    print("AUDIOBOOK_QC_OFFLINE_BUNDLE_OK", flush=True)

if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--smoke-gui', action='store_true')
    args=parser.parse_args()
    if args.self_test:
        self_test()
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
