import httpx
import pytest

from wra.core.models import WRAConfig
from wra.llm import preflight
from wra.llm.preflight import configured_model_names, ensure_browser_available, ensure_models_available
from support import sample_config_data


def _tags(monkeypatch, names=None, *, status=200, error=None, key="name", calls=None):
    def fake_get(url, timeout=None):
        if calls is not None:
            calls.append(url)
        if error is not None:
            raise error
        request = httpx.Request("GET", url)
        return httpx.Response(status, json={"models": [{key: name} for name in names or []]}, request=request)

    monkeypatch.setattr(preflight.httpx, "get", fake_get)


def test_models_all_served_pass(monkeypatch) -> None:
    _tags(monkeypatch, ["a:7b", "b"])
    ensure_models_available("http://host:1", ["a:7b", "b"])


def test_a_missing_model_is_named_and_the_url_redacted(monkeypatch) -> None:
    _tags(monkeypatch, ["a:7b"])
    with pytest.raises(RuntimeError, match=r"not found on http://host:1: c") as excinfo:
        ensure_models_available("http://user:secret@host:1", ["a:7b", "c", "c"])
    assert "secret" not in str(excinfo.value)


def test_latest_suffix_is_tolerated_both_ways(monkeypatch) -> None:
    _tags(monkeypatch, ["a:latest", "b"])
    ensure_models_available("http://host:1", ["a", "b:latest"])


def test_model_key_is_read_when_name_is_absent(monkeypatch) -> None:
    _tags(monkeypatch, ["a"], key="model")
    ensure_models_available("http://host:1", ["a"])


def test_the_path_prefix_of_the_endpoint_is_kept(monkeypatch) -> None:
    calls: list[str] = []
    _tags(monkeypatch, ["a"], calls=calls)
    ensure_models_available("http://host:52415/ollama/", ["a"])
    assert calls[0] == "http://host:52415/ollama/api/tags"


def test_an_unreachable_host_is_skipped(monkeypatch) -> None:
    _tags(monkeypatch, error=httpx.ConnectError("down"))
    ensure_models_available("http://host:1", ["missing"])


def test_a_server_without_tags_is_skipped(monkeypatch) -> None:
    _tags(monkeypatch, status=404)
    ensure_models_available("http://host:1", ["missing"])


def test_an_unparseable_listing_is_skipped(monkeypatch) -> None:
    monkeypatch.setattr(preflight.httpx, "get", lambda url, timeout=None: httpx.Response(200, text="<html>", request=httpx.Request("GET", url)))
    ensure_models_available("http://host:1", ["missing"])


def _config(**overrides) -> WRAConfig:
    data = sample_config_data()
    data["models"] = {role: [role + "-model"] for role in ("analyzer", "designer", "generator", "evaluator", "vision", "creator", "refiner")}
    data["input"]["vision"]["enabled"] = overrides.get("vision", False)
    data["pipeline"]["refine"]["enabled"] = overrides.get("refine", True)
    return WRAConfig.model_validate(data)


def test_roles_are_collected_by_use() -> None:
    names = configured_model_names(_config(), creates=False)
    assert names == ["analyzer-model", "designer-model", "generator-model", "evaluator-model", "refiner-model"]
    assert "creator-model" in configured_model_names(_config(), creates=True)
    assert "vision-model" in configured_model_names(_config(vision=True), creates=False)
    assert "refiner-model" not in configured_model_names(_config(refine=False), creates=False)


def test_a_missing_browser_is_reported(monkeypatch, tmp_path) -> None:
    import playwright.sync_api as sync_api

    class Chromium:
        executable_path = str(tmp_path / "absent")

    class Manager:
        def __enter__(self):
            return type("P", (), {"chromium": Chromium})()

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(sync_api, "sync_playwright", lambda: Manager())
    with pytest.raises(RuntimeError, match="playwright install chromium"):
        ensure_browser_available()
    present = tmp_path / "chrome"
    present.write_text("")
    Chromium.executable_path = str(present)
    ensure_browser_available()


EXO = "http://host:52415/ollama"


def _exo(monkeypatch, tags, state=None, *, state_status=200, state_error=None, calls=None):
    def fake_get(url, timeout=None):
        if calls is not None:
            calls.append(url)
        request = httpx.Request("GET", url)
        if url.endswith("/api/tags"):
            return httpx.Response(200, json={"models": [{"name": name} for name in tags]}, request=request)
        if state_error is not None:
            raise state_error
        return httpx.Response(state_status, json=state if state is not None else {}, request=request)

    monkeypatch.setattr(preflight.httpx, "get", fake_get)


def _instances(*model_ids):
    return {"instances": {f"i{index}": {"MlxRingInstance": {"shardAssignments": {"modelId": model_id, "runnerToShard": {"r": {"PipelineShardMetadata": {"modelCard": {"modelId": model_id}}}}}}} for index, model_id in enumerate(model_ids)}}


def test_an_exo_model_that_is_placed_passes(monkeypatch) -> None:
    calls: list[str] = []
    _exo(monkeypatch, ["m/a", "m/b"], _instances("m/a", "m/b"), calls=calls)
    ensure_models_available(EXO + "/", ["m/a", "m/b"])
    assert calls == [f"{EXO}/api/tags", "http://host:52415/state"]


def test_an_exo_model_that_is_downloaded_but_not_placed_fails(monkeypatch) -> None:
    _exo(monkeypatch, ["m/a", "m/b"], _instances("m/a"))
    with pytest.raises(RuntimeError, match=r"not placed on http://host:52415/ollama: m/b"):
        ensure_models_available(EXO, ["m/a", "m/b"])


def test_nothing_placed_at_all_fails(monkeypatch) -> None:
    _exo(monkeypatch, ["m/a"], {"instances": {}})
    with pytest.raises(RuntimeError, match="not placed"):
        ensure_models_available(EXO, ["m/a"])


def test_a_missing_download_is_still_reported_first(monkeypatch) -> None:
    _exo(monkeypatch, ["m/a"], _instances("m/a"))
    with pytest.raises(RuntimeError, match=r"not found on"):
        ensure_models_available(EXO, ["m/z"])


def test_an_exo_state_that_cannot_be_read_is_skipped(monkeypatch, caplog) -> None:
    for kwargs in ({"state_status": 404}, {"state_error": httpx.ConnectError("down")}, {"state": {"unexpected": 1}}, {"state": {"instances": {"i": {"x": 1}}}}):
        caplog.clear()
        _exo(monkeypatch, ["m/a"], **kwargs)
        ensure_models_available(EXO, ["m/a"])
        assert "skipping the placement check" in caplog.text


def test_a_non_exo_endpoint_never_requests_state(monkeypatch) -> None:
    calls: list[str] = []
    _exo(monkeypatch, ["m/a"], calls=calls)
    ensure_models_available("http://host:11434", ["m/a"])
    assert calls == ["http://host:11434/api/tags"]
