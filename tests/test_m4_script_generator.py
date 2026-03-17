from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace


def load_generator_module_with_fake_dashscope(monkeypatch):
    fake_generation = SimpleNamespace(call=lambda **kwargs: {"status_code": 200, "output": {"text": "{}"}})
    fake_dashscope = SimpleNamespace(Generation=fake_generation)
    monkeypatch.setitem(sys.modules, "dashscope", fake_dashscope)
    module = importlib.import_module("modules.script_generator.generator")
    return importlib.reload(module)


def test_parse_json_response(monkeypatch):
    module = load_generator_module_with_fake_dashscope(monkeypatch)
    generator = module.ScriptGenerator()

    parsed = generator._parse_json_response("```json\n{\"a\": 1}\n```")
    assert parsed == {"a": 1}


def test_select_scenes_parse_ids(monkeypatch):
    module = load_generator_module_with_fake_dashscope(monkeypatch)
    generator = module.ScriptGenerator()

    monkeypatch.setattr(generator, "_call_llm", lambda *_: '{"selected_scene_ids": ["1", 2]}')
    scenes = [{"scene_id": 1}, {"scene_id": 2}, {"scene_id": 3}]
    result = generator._select_scenes(scenes, {"title": "A"})
    assert result == [1, 2]


def test_generate_three_round_flow(monkeypatch):
    module = load_generator_module_with_fake_dashscope(monkeypatch)
    generator = module.ScriptGenerator()

    monkeypatch.setattr(generator, "_select_scenes", lambda *_: [1])
    monkeypatch.setattr(
        generator,
        "_generate_narration",
        lambda *_: [{"segment_id": 1, "scene_ids": [1], "video_clip": {"start": 0.0, "end": 1.0}, "narration_text": "x", "emotion": "平静", "is_climax": False, "bgm_required": False}],
    )
    monkeypatch.setattr(generator, "_tag_emotions", lambda segments, *_: segments)

    out = generator.generate([], [{"scene_id": 1}], {"title": "A"})
    assert "segments" in out
    assert out["segments"][0]["narration_text"] == "x"
