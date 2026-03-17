from __future__ import annotations

import subprocess
from pathlib import Path


def test_create_concat_file(tmp_path: Path) -> None:
    from modules.final_composer.utils import create_concat_file

    a = tmp_path / "a.mp4"
    b = tmp_path / "b.mp4"
    a.write_bytes(b"x")
    b.write_bytes(b"y")
    out = tmp_path / "list.txt"

    path = create_concat_file([str(a), str(b)], str(out))
    assert path == str(out)
    assert "file '" in out.read_text(encoding="utf-8")


def test_compose_segment_missing_paths(tmp_path: Path) -> None:
    from modules.final_composer.composer import FinalComposer

    composer = FinalComposer()
    try:
        composer.compose_segment({}, str(tmp_path / "o.mp4"))
        assert False
    except ValueError:
        assert True


def test_compose_all_with_skip(monkeypatch, tmp_path: Path) -> None:
    import modules.final_composer.composer as composer_module

    composer = composer_module.FinalComposer()
    out = tmp_path / "final.mp4"

    seg_out = tmp_path / "seg0.mp4"
    seg_out.write_bytes(b"x")

    monkeypatch.setattr(composer, "compose_segment", lambda *_args, **_kwargs: str(seg_out))
    monkeypatch.setattr(composer_module, "create_concat_file", lambda *_args, **_kwargs: str(tmp_path / "concat.txt"))
    monkeypatch.setattr(
        composer_module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=["ok"], returncode=0, stdout="", stderr=""),
    )

    segments = [{"skip": False}, {"skip": True}]
    result = composer.compose_all(segments, str(out))
    assert result == str(out)
