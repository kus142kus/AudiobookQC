"""Audiobook QC: launcher for source and native Python-bundled Windows/Mac releases."""
import os
from pathlib import Path
import sys

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).parent / "src"))

from audiobook_qc.server import launch


def check_bundled_runtime():
    """Test packaged runtime without downloading large AI model or running audio analysis."""
    from audiobook_qc import server, worker
    import docx
    import imageio_ffmpeg
    import faster_whisper
    import ctranslate2
    import av
    import tokenizers
    assert (server.WEB / "index.html").is_file(), f"Missing bundled web UI: {server.WEB}"
    assert Path(imageio_ffmpeg.get_ffmpeg_exe()).is_file(), "FFmpeg executable is missing"
    assert callable(worker.audio_duration)
    print("AudiobookQC packaged runtime OK", flush=True)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--self-test", action="store_true",
                        help="Validate bundled dependencies without downloading an AI model")
    args = parser.parse_args()
    if args.self_test:
        check_bundled_runtime()
    else:
        launch(args.port, args.no_browser)
