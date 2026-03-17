from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import requests


def test_clean_html_text() -> None:
    from modules.info_collector.utils import clean_html_text

    text = clean_html_text("<html><body><h1>标题</h1><script>x=1</script><p>内容</p></body></html>")
    assert "标题" in text
    assert "内容" in text
    assert "script" not in text


def test_safe_request_failure(monkeypatch) -> None:
    from modules.info_collector.utils import safe_request

    def raise_error(*args, **kwargs):
        raise requests.RequestException("boom")

    monkeypatch.setattr(requests, "get", raise_error)
    assert safe_request("https://x") is None


def test_collect_merge_and_save(monkeypatch, tmp_path: Path) -> None:
    from modules.info_collector.collector import InfoCollector

    collector = InfoCollector()
    collector.sources = ["douban", "baidu_baike", "tmdb"]
    monkeypatch.setattr(collector, "_enrich_with_llm", lambda _name, merged: merged)

    monkeypatch.setattr(collector, "_search_douban", lambda *_: {"title": "片名", "genre": ["剧情"], "year": 2024})
    monkeypatch.setattr(collector, "_search_baidu_baike", lambda *_: {"synopsis": "剧情梗概"})
    monkeypatch.setattr(collector, "_search_tmdb", lambda *_args, **_kwargs: {"genre": ["剧情", "悬疑"]})

    info = collector.collect("片名")
    assert info["title"] == "片名"
    assert "剧情" in info["genre"]
    assert info["synopsis"] == "剧情梗概"

    output = tmp_path / "movie_info.json"
    collector.save(info, str(output))
    assert output.exists()
