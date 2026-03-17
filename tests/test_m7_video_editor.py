from __future__ import annotations

from pathlib import Path


def test_cut_segments_use_video_clip(monkeypatch, tmp_path: Path) -> None:
    import modules.video_editor.editor as editor_module

    editor = editor_module.VideoEditor()
    video = tmp_path / "movie.mp4"
    video.write_bytes(b"x")

    monkeypatch.setattr(editor, "cut_segment", lambda **kwargs: str(tmp_path / "clip.mp4"))
    monkeypatch.setattr(editor_module, "get_video_duration", lambda *_: 5.0)

    segments = [{"video_clip": {"start": 1.0, "end": 6.0}}]
    out = editor.cut_segments(str(video), segments, str(tmp_path / "clips"))
    assert out[0]["video_clip_duration"] == 5.0
    assert out[0]["video_clip_path"].endswith("clip.mp4")


def test_add_transition_missing_file(tmp_path: Path) -> None:
    from modules.video_editor.editor import VideoEditor

    editor = VideoEditor()
    try:
        editor.add_transition(str(tmp_path / "a.mp4"), str(tmp_path / "b.mp4"), str(tmp_path / "o.mp4"))
        assert False
    except FileNotFoundError:
        assert True
