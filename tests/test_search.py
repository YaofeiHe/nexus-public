from __future__ import annotations

from pathlib import Path
import json

from nexus.tools.repo_scan import scan_repo
from nexus.tools.search import local_inventory_search
from nexus.tools.search_adapters import ChineseWebAdapter, resolve_chinese_web_providers


def test_local_content_search_finds_project_files(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text("这是一个 workflow kernel runner，用于中文互联网调研。", encoding="utf-8")
    repo_scan = scan_repo(project)

    candidates, statuses = local_inventory_search(repo_scan, ["workflow kernel 中文互联网"], project_path=project)

    assert any(candidate["source"] == "local_content_keyword" for candidate in candidates)
    assert any(status["source"] == "local_content_keyword" for status in statuses)


def test_chinese_web_defaults_to_non_openai(monkeypatch) -> None:
    monkeypatch.delenv("NEXUS_CHINESE_WEB_PROVIDERS", raising=False)
    monkeypatch.delenv("NEXUS_ENABLE_OPENAI_WEB_SEARCH", raising=False)
    monkeypatch.delenv("NEXUS_ENABLE_BAIDU_SERP", raising=False)

    assert resolve_chinese_web_providers() == ["tavily", "brave"]


def test_chinese_web_without_keys_is_auth_required(monkeypatch, tmp_path: Path) -> None:
    for name in [
        "TAVILY_API_KEY",
        "BRAVE_SEARCH_API_KEY",
        "SERPAPI_API_KEY",
        "SEARCHAPI_API_KEY",
        "OPENAI_API_KEY",
        "NEXUS_ENABLE_OPENAI_WEB_SEARCH",
        "NEXUS_ENABLE_BAIDU_SERP",
        "NEXUS_CHINESE_WEB_PROVIDERS",
    ]:
        monkeypatch.delenv(name, raising=False)

    records, status = ChineseWebAdapter().search(["中文 workflow kernel"], project_path=tmp_path, repo_scan={}, online=True, raw_dir=tmp_path)

    assert records == []
    assert status.status == "auth_required"
    assert status.online_search_blocked is True
    assert {item["provider"] for item in status.provider_statuses} == {"tavily", "brave"}
    assert all(item["attempted"] is False for item in status.provider_statuses)
    assert all(Path(ref).exists() for ref in status.raw_artifact_refs)


def test_tavily_response_maps_to_candidate_and_redacted_raw(monkeypatch, tmp_path: Path) -> None:
    secret = "tvly-secret"
    monkeypatch.setenv("TAVILY_API_KEY", secret)
    monkeypatch.setenv("NEXUS_CHINESE_WEB_PROVIDERS", "tavily")
    monkeypatch.delenv("NEXUS_ENABLE_OPENAI_WEB_SEARCH", raising=False)

    class FakeResponse:
        status = 200
        headers = {"content-type": "application/json", "x-ratelimit-remaining": "9"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(
                {
                    "results": [
                        {
                            "title": "中文 Workflow Kernel",
                            "url": "https://example.com/workflow",
                            "content": "中文互联网检索结果",
                            "score": 0.91,
                        }
                    ],
                    "usage": {"searches": 1},
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout):
        assert timeout == 60
        assert request.headers["Authorization"] == f"Bearer {secret}"
        return FakeResponse()

    monkeypatch.setattr("nexus.tools.search_adapters.urlopen", fake_urlopen)

    records, status = ChineseWebAdapter().search(["中文 workflow kernel"], project_path=tmp_path, repo_scan={}, online=True, raw_dir=tmp_path)

    assert status.status == "ok"
    assert status.provider_statuses[0]["provider"] == "tavily"
    assert records[0].url == "https://example.com/workflow"
    raw_text = Path(records[0].raw_artifact_refs[0]).read_text(encoding="utf-8")
    assert secret not in raw_text
    assert "[REDACTED]" in raw_text


def test_tavily_key_file_is_used_without_printing_secret(monkeypatch, tmp_path: Path) -> None:
    secret = "tvly-file-secret"
    key_file = tmp_path / "tvly-key"
    key_file.write_text(secret + "\n", encoding="utf-8")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setenv("NEXUS_TAVILY_KEY_FILE", str(key_file))
    monkeypatch.setenv("NEXUS_CHINESE_WEB_PROVIDERS", "tavily")

    class FakeResponse:
        status = 200
        headers = {"content-type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"results": [{"title": "Python docs", "url": "https://docs.python.org/zh-cn/3/", "content": "Python 中文文档"}]}).encode("utf-8")

    def fake_urlopen(request, timeout):
        assert request.headers["Authorization"] == f"Bearer {secret}"
        return FakeResponse()

    monkeypatch.setattr("nexus.tools.search_adapters.urlopen", fake_urlopen)

    records, status = ChineseWebAdapter().search(["Python 官方文档"], project_path=tmp_path, repo_scan={}, online=True, raw_dir=tmp_path)

    assert status.status == "ok"
    assert records[0].url == "https://docs.python.org/zh-cn/3/"
    raw_text = Path(records[0].raw_artifact_refs[0]).read_text(encoding="utf-8")
    assert secret not in raw_text
