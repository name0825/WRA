from pathlib import Path

import pytest

from wra.browser import http_cache
from wra.browser.http_cache import fetch_cached, is_allowed_url


class _FakeResponse:
    def __init__(self, body: bytes, content_type: str = "text/css") -> None:
        self._body = body
        self.headers = {"Content-Type": content_type}

    def read(self, n: int = -1) -> bytes:
        return self._body[:n] if n is not None and n >= 0 else self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _stub_opener(monkeypatch: pytest.MonkeyPatch, response=None, raises=None):
    calls = []

    class _FakeOpener:
        def open(self, request, timeout=None):
            calls.append(request.full_url)
            if raises is not None:
                raise raises
            return response

    monkeypatch.setattr(http_cache.urllib_request, "build_opener", lambda *a, **k: _FakeOpener())
    return calls


def test_disallowed_host_is_never_fetched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_opener(monkeypatch, response=_FakeResponse(b"body"))
    result = fetch_cached("https://evil.example.com/x.css", tmp_path)
    assert result is None
    assert calls == []


def test_http_scheme_is_rejected(tmp_path: Path) -> None:
    assert not is_allowed_url("http://fonts.googleapis.com/css")


def test_cache_hit_reuses_the_file_with_no_fetch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_opener(monkeypatch, response=_FakeResponse(b"h1{color:red}"))
    url = "https://fonts.googleapis.com/css2?family=Roboto"

    first = fetch_cached(url, tmp_path)
    assert first is not None
    assert first.read_bytes() == b"h1{color:red}"
    assert len(calls) == 1

    second = fetch_cached(url, tmp_path)
    assert second == first
    assert len(calls) == 1  # no second network call


def test_oversized_response_falls_back_to_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    huge = b"a" * (http_cache.MAX_RESPONSE_BYTES + 1)
    _stub_opener(monkeypatch, response=_FakeResponse(huge))
    result = fetch_cached("https://fonts.gstatic.com/s/x.woff2", tmp_path)
    assert result is None
    assert list(tmp_path.glob("*")) == []


def test_a_failed_fetch_falls_back_to_none_without_raising(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_opener(monkeypatch, raises=OSError("connection reset"))
    result = fetch_cached("https://fonts.googleapis.com/css2?family=Roboto", tmp_path)
    assert result is None


def test_a_redirect_off_the_allowlist_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    handler = http_cache._AllowlistRedirectHandler(http_cache.ALLOWED_HOSTS)
    with pytest.raises(Exception):
        handler.redirect_request(None, None, 302, "Found", {}, "https://evil.example.com/payload")


def test_a_leftover_tmp_file_is_never_treated_as_a_cache_hit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://fonts.googleapis.com/css2?family=Roboto"
    digest = http_cache._digest_for_url(url)
    (tmp_path / f"{digest}.css.tmp").write_bytes(b"partial-write-from-a-killed-process")

    calls = _stub_opener(monkeypatch, response=_FakeResponse(b"h1{color:red}"))
    result = fetch_cached(url, tmp_path)

    assert result is not None
    assert result.suffix != ".tmp"
    assert result.read_bytes() == b"h1{color:red}"
    assert len(calls) == 1


def test_non_default_port_is_rejected(tmp_path: Path) -> None:
    assert not is_allowed_url("https://fonts.gstatic.com:8443/s/x.woff2")


def test_path_traversal_via_url_cannot_escape_the_cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_opener(monkeypatch, response=_FakeResponse(b"body"))
    # Even a maliciously crafted host-allowlisted URL only ever produces a
    # hash-based filename; nothing derived from the URL path is used as a
    # path segment, so this must land inside tmp_path or be rejected.
    url = "https://fonts.gstatic.com/../../../etc/passwd"
    result = fetch_cached(url, tmp_path)
    if result is not None:
        assert tmp_path.resolve() in result.resolve().parents
        assert result.name.split(".")[0] == http_cache._digest_for_url(url)


def test_the_user_agent_is_a_current_desktop_chrome(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A default urllib UA makes Google serve legacy whole-file .ttf CSS,
    which blows the per-file size cap for CJK families; a Chrome UA gets
    woff2 sliced by unicode-range instead."""
    captured = {}

    class _FakeOpener:
        def open(self, request, timeout=None):
            captured["ua"] = request.get_header("User-agent")
            return _FakeResponse(b"body")

    monkeypatch.setattr(http_cache.urllib_request, "build_opener", lambda *a, **k: _FakeOpener())
    fetch_cached("https://fonts.googleapis.com/css2?family=Roboto", tmp_path)
    assert captured["ua"] == http_cache.CHROME_USER_AGENT
    assert "Chrome" in captured["ua"]


def test_a_legacy_pre_ua_change_cache_entry_is_not_reused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A .css file cached under the pre-fix unversioned digest (fetched with
    the old UA, pointing at whole .ttf files) must never be served as a hit
    for the new, UA-versioned cache key."""
    import hashlib

    url = "https://fonts.googleapis.com/css2?family=Roboto"
    legacy_digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    (tmp_path / f"{legacy_digest}.css").write_bytes(b"body{font-family:legacy-ttf-css}")

    calls = _stub_opener(monkeypatch, response=_FakeResponse(b"new-woff2-css"))
    result = fetch_cached(url, tmp_path)

    assert len(calls) == 1  # the stale legacy-UA entry was not treated as a hit
    assert result is not None
    assert result.read_bytes() == b"new-woff2-css"
