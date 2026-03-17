from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace


def test_format_timestamp() -> None:
    from modules.subtitle_extractor.utils import format_timestamp

    assert format_timestamp(0) == "00:00:00.000"
    assert format_timestamp(65.432) == "00:01:05.432"


def test_extract_audio_success(monkeypatch, tmp_path: Path) -> None:
    from modules.subtitle_extractor import utils

    video = tmp_path / "input.mp4"
    video.write_bytes(b"v")
    output = tmp_path / "audio" / "x.wav"

    monkeypatch.setattr(
        utils.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=["ok"], returncode=0, stdout="", stderr=""),
    )

    result = utils.extract_audio(str(video), str(output))
    assert result == str(output)


def test_subtitle_extractor_extract_and_save(monkeypatch, workspace_dir: Path, tmp_path: Path) -> None:
    import modules.subtitle_extractor.extractor as extractor_module

    video = tmp_path / "movie.mp4"
    video.write_bytes(b"video")

    class FakeWhisperModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, *args, **kwargs):
            return [
                SimpleNamespace(start=0.0, end=1.2, text="你好"),
                SimpleNamespace(start=1.2, end=2.4, text="世界"),
            ], {}

    def fake_import_module(name: str):
        if name == "faster_whisper":
            return SimpleNamespace(WhisperModel=FakeWhisperModel)
        raise AssertionError(f"unexpected import: {name}")

    monkeypatch.setattr(extractor_module.importlib, "import_module", fake_import_module)
    monkeypatch.setattr(extractor_module, "extract_audio", lambda *_: str(workspace_dir / "audio" / "movie.wav"))

    extractor = extractor_module.SubtitleExtractor(str(workspace_dir))
    subtitles = extractor.extract(str(video))
    assert subtitles[0]["text"] == "你好"
    assert len(subtitles) == 2

    out_file = tmp_path / "subtitles.json"
    extractor.save(subtitles, str(out_file))
    assert out_file.exists()
