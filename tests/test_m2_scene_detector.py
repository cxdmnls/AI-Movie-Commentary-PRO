from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace


def test_seconds_to_timecode() -> None:
    from modules.scene_detector.utils import seconds_to_timecode

    assert seconds_to_timecode(0) == "00:00:00.000"
    assert seconds_to_timecode(3661.5) == "01:01:01.500"


def test_extract_thumbnail_success(monkeypatch, tmp_path: Path) -> None:
    from modules.scene_detector import utils

    video = tmp_path / "in.mp4"
    video.write_bytes(b"v")
    out = tmp_path / "thumb.jpg"

    monkeypatch.setattr(
        utils.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=["ok"], returncode=0, stdout="", stderr=""),
    )

    assert utils.extract_thumbnail(str(video), 1.0, str(out), 320) == str(out)


def test_scene_detector_detect(monkeypatch, workspace_dir: Path, tmp_path: Path) -> None:
    import modules.scene_detector.detector as detector_module

    video = tmp_path / "movie.mp4"
    video.write_bytes(b"video")

    class FakeContentDetector:
        def __init__(self, *args, **kwargs):
            pass

    def fake_detect(*args, **kwargs):
        return [(0.0, 10.0), (10.0, 25.0)]

    fake_module = SimpleNamespace(ContentDetector=FakeContentDetector, detect=fake_detect)

    monkeypatch.setattr(detector_module.importlib, "import_module", lambda name: fake_module)
    monkeypatch.setattr(detector_module, "extract_thumbnail", lambda *args, **kwargs: "ok.jpg")

    detector = detector_module.SceneDetector(str(workspace_dir))
    scenes = detector.detect(str(video))
    assert len(scenes) == 2
    assert scenes[0]["scene_id"] == 1
    assert scenes[0]["thumbnail"].endswith("001.jpg")
