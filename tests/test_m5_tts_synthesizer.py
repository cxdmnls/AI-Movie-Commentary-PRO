from __future__ import annotations


def test_estimate_speech_duration() -> None:
    from modules.tts_synthesizer.utils import estimate_speech_duration

    assert estimate_speech_duration("你好世界", words_per_minute=120) > 0


def test_extract_segment_text_supports_narration_text() -> None:
    from modules.tts_synthesizer.synthesizer import TTSSynthesizer

    text = TTSSynthesizer._extract_segment_text({"narration_text": "解说内容"})
    assert text == "解说内容"


def test_extract_segment_duration_from_video_clip() -> None:
    from modules.tts_synthesizer.synthesizer import TTSSynthesizer

    duration = TTSSynthesizer._extract_segment_duration({"video_clip": {"start": 2.0, "end": 8.5}})
    assert duration == 6.5


def test_synthesize_raises_when_model_missing(tmp_path) -> None:
    from modules.tts_synthesizer.synthesizer import TTSSynthesizer

    synth = TTSSynthesizer()
    synth.model = None
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"x")

    try:
        synth.synthesize("文本", str(ref), str(tmp_path / "o.wav"))
        assert False
    except RuntimeError:
        assert True
