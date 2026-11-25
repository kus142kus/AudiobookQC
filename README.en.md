# Audiobook QC — offline audiobook proofreading

[Русский](README.md) · [English](README.en.md) · [Downloads](https://github.com/kus142kus/AudiobookQC/releases) · [Report a bug](https://github.com/kus142kus/AudiobookQC/issues)

**Audiobook QC** is a local desktop tool for audiobook narrators, editors, and publishers. It compares a spoken recording against the manuscript and produces **time-coded Word reports** containing possible misread words, omissions, substitutions, repeats, and selected audio-quality warnings.

> **Status: 1.0 Release Candidate (RC2), not a stable 1.0 release.** Windows x64 and Apple Silicon macOS CI builds passed on Nov 25, 2026 (run #38). Windows launch and a first real audio segment have been tested. A real Mac M4 installation and measured detection accuracy are **not yet verified**. No automatic speech-checking system can guarantee finding every mistake.

## Features

- Offline Russian-language ASR using Whisper Medium, with selected suspicious passages independently rechecked using GigaAM.
- DOCX/TXT manuscripts and MP3/WAV/M4A/FLAC recordings; the application can select one matching audio and document from the same folder.
- Incremental processing for hours-long recordings and time-coded `.docx` reports for completed parts.
- Embedded clip playback with **Fixed / Not an error** review choices.
- Optional display of uncertain results and separate, unverified sound-event warnings.
- No cloud transcription, subscription, or API key. The **Windows installer** downloads the bundled speech-model archive on first installation; after installation, the application processes data locally. The **Apple Silicon DMG** already contains its models.

## Platform support

| Platform | Status | Public release file |
| --- | --- | --- |
| Windows 10/11 x64 (Intel/AMD) | CI passed; user testing underway | `AQC-Setup.exe` |
| macOS Apple Silicon (M1–M5, incl. M4) | DMG built; real M4 test pending | `AudiobookQC-Mac.dmg` |
| Linux x64 | Manual experimental workflow only | Not a supported public package |
| Linux ARM64 | Manual experimental workflow only | Not a supported public package |
| macOS Intel | No validated build | Unavailable |
| Windows ARM64 | No validated native build | Unavailable |

**Download from [GitHub Releases](https://github.com/kus142kus/AudiobookQC/releases), not from the source-code ZIP.** If RC2 is not yet published there, it is not publicly downloadable as a release. For Windows, download the **EXE only**; the installer manages `AQC-Model.zip` automatically. For Apple Silicon, download the **DMG only**. The app may trigger OS security warnings because a public code-signing/notarization process is not yet in place.

[Installation (RU)](docs/Установка-в-общем-доступе.md) · [User guide (RU)](docs/Руководство-пользователя.md) · [FAQ (RU)](docs/Частые-вопросы.md) · [Additional platform trials (RU)](docs/Дополнительные-платформы.md) · [Platform roadmap](docs/Платформы-и-план.md)

## Quick start

1. Select **New check** (`Новая проверка`), then **Choose folder with two files** (`Выбрать папку с двумя файлами`), or choose the audio and DOCX/TXT individually.
2. Pick an intermediate report interval (10–20 minutes is a practical starting point), then click **Start checking** (`Начать проверку`).
3. Wait for a completed part; open an issue, play the audio and mark it **Fixed** (`Исправлено`) or **Not an error** (`Не ошибка`). Turn on **Show uncertain** (`Показывать сомнительные`) to inspect less certain candidates.
4. Export each completed part to Word; when the entire job finishes, use **Save final Word** (`Сохранить итоговый Word`).

**All findings need human listening confirmation.** Stress and intonation analysis is experimental. Some real errors may be missed. Start with a short 5–10 minute test before using a multi-hour recording.

## Privacy, licensing, and support

Processing is designed to stay on the local computer. Please **do not upload private manuscripts or audio recordings** to public issues. Open an [issue](https://github.com/kus142kus/AudiobookQC/issues) with OS version, reproduction steps and a redacted screenshot or log.

Source code: [MIT license](LICENSE). **Bundled ASR weights, native libraries, FFmpeg, and Qt have separate terms**; see [third-party notices](THIRD_PARTY_NOTICES.md). A full license review is required before a stable public release.

Developer commands: `python -m unittest discover -s tests -v`. Production CI targets Windows x64 and Apple Silicon macOS; Linux preview workflows are manually triggered and experimental.

## Other platforms: work in progress

A separate **manually triggered** GitHub Actions preview can now try Linux x64, Linux ARM64, macOS Intel and Windows 11 ARM64. Linux and macOS Intel builds are published as **experimental artifacts only if actual CI verification succeeds**. On Windows ARM64, the test checks launching the existing **x64** setup via Windows 11 emulation; this is **not** an ARM64-native app and does not test the transcription models. None of these experimental platforms is advertised as production-ready.

See [details](docs/Дополнительные-платформы.md). Stable Windows x64 and Apple Silicon workflows remain unchanged.

**Experimental platform notes:** Linux audio playback requires the system PortAudio library (`libportaudio2` on Ubuntu). Intel macOS preview receives GigaAM weights converted to `safetensors` on a modern Linux PyTorch runner; compatibility remains unproven until actual CI inference passes. Windows ARM64 preview probes the Inno installer bootstrapper, which can be an x86 executable even when the packaged application is x64; the application itself has not been tested on ARM64.
