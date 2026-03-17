from __future__ import annotations

from pathlib import Path


def test_list_audio_files(tmp_path: Path) -> None:
    from modules.bgm_matcher.utils import list_audio_files

    (tmp_path / "a.mp3").write_bytes(b"x")
    (tmp_path / "b.txt").write_text("x", encoding="utf-8")
    files = list_audio_files(str(tmp_path))
    assert len(files) == 1
    assert files[0].endswith("a.mp3")


def test_extract_duration_from_video_clip() -> None:
    from modules.bgm_matcher.matcher import BGMMatcher

    d = BGMMatcher._extract_duration({"video_clip": {"start": 10.0, "end": 18.0}})
    assert d == 8.0


def test_match_segments_skip_when_library_empty(monkeypatch, tmp_path: Path) -> None:
    import conf
    from modules.bgm_matcher.matcher import BGMMatcher

    monkeypatch.setattr(conf, "BGM_LIBRARY_DIR", str(tmp_path / "bgm"))
    monkeypatch.setattr(conf, "EMOTION_TYPES", ["平静", "紧张"])
    monkeypatch.setattr(conf, "BGM_SKIP_IF_EMPTY", True)

    matcher = BGMMatcher()
    segments = [{"video_clip": {"start": 0.0, "end": 3.0}, "emotion": "平静", "bgm_required": True}]
    out = matcher.match_segments(segments, str(tmp_path / "out"))
    assert len(out) == 1
    assert "bgm_clip_path" not in out[0] or out[0]["bgm_clip_path"] is None
