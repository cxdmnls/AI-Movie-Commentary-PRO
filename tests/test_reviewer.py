from __future__ import annotations


def test_segment_text_reads_narration_text() -> None:
    from review.reviewer import Reviewer

    text = Reviewer._segment_text({"narration_text": "这是解说"})
    assert text == "这是解说"


def test_review_script_confirm(monkeypatch, tmp_path) -> None:
    from review.reviewer import Reviewer

    reviewer = Reviewer(str(tmp_path))
    monkeypatch.setattr(reviewer, "_display_segment_table", lambda *_: None)
    monkeypatch.setattr(reviewer, "_prompt_user", lambda *_: "确认")

    script = {"segments": [{"narration_text": "a", "video_clip": {"start": 0.0, "end": 1.0}}]}
    out = reviewer.review_script(script)
    assert out["segments"][0]["narration_text"] == "a"


def test_review_materials_remove_bgm(monkeypatch, tmp_path) -> None:
    from review.reviewer import Reviewer

    reviewer = Reviewer(str(tmp_path))
    monkeypatch.setattr(reviewer, "_display_segment_table", lambda *_: None)

    actions = iter(["移除某个 segment 的 BGM", "确认全部"])
    monkeypatch.setattr(reviewer, "_prompt_user", lambda *_: next(actions))
    monkeypatch.setattr(reviewer, "_prompt_segment_index", lambda *_: 0)

    segments = [{"bgm_clip_path": "a.wav", "bgm_required": True, "video_clip": {"start": 0.0, "end": 2.0}}]
    out = reviewer.review_materials(segments)
    assert out[0]["bgm_clip_path"] is None
    assert out[0]["bgm_required"] is False
