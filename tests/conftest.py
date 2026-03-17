from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(autouse=True)
def patch_conf_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import conf

    monkeypatch.setattr(conf, "WORKSPACE_DIR", str(tmp_path / "workspace"))
    monkeypatch.setattr(conf, "BGM_LIBRARY_DIR", str(tmp_path / "bgm_library"))
    monkeypatch.setattr(conf, "PROMPTS_DIR", str(PROJECT_ROOT / "modules" / "script_generator" / "prompts"))
    monkeypatch.setattr(conf, "FFMPEG_BIN", "ffmpeg")
    monkeypatch.setattr(conf, "FFPROBE_BIN", "ffprobe")


@pytest.fixture
def ok_process() -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["ok"], returncode=0, stdout="", stderr="")


@pytest.fixture
def workspace_dir(tmp_path: Path) -> Path:
    path = tmp_path / "workspace" / "movie"
    path.mkdir(parents=True, exist_ok=True)
    return path
