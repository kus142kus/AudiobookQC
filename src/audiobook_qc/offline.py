"""Strict offline model resolution. No model download or network fallback."""
from __future__ import annotations
import os
import sys
from pathlib import Path

MODEL_REPOS = {
    'medium': 'Systran/faster-whisper-medium',
    # Optional manually installed alternative. Not bundled by default.
    'large-v3-turbo': 'mobiuslabsgmbh/faster-whisper-large-v3-turbo',
}
DEFAULT_MODEL = 'medium'


def bundled_root() -> Path:
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2]))


def model_directories() -> list[Path]:
    from .worker import ROOT
    paths: list[Path] = []
    override = os.environ.get('AUDIOBOOK_QC_MODELS_DIR')
    if override:
        paths.append(Path(override).expanduser())
    paths.extend((bundled_root() / 'models', ROOT / 'models'))
    return paths


def resolve_model(name: str) -> Path:
    if name not in MODEL_REPOS:
        raise ValueError(f'Неподдерживаемая модель: {name}')
    for root in model_directories():
        p = root / name
        if all((p / f).is_file() for f in ('model.bin', 'config.json', 'tokenizer.json')):
            return p
    raise FileNotFoundError(
        f'Локальная модель {name} не найдена. Для работы без интернета '
        'модель должна входить в установочный пакет или быть скопирована '
        'в папку models. Программа ничего не скачивает самостоятельно.'
    )


def offline_environment() -> None:
    # Safety net: even third-party model resolvers should stay offline.
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
