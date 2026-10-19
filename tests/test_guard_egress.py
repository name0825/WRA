import os
import socket
import threading
import time
from pathlib import Path

import pytest

from wra.browser.guard import CHROMIUM_LAUNCH_ARGS, guard_context, launch_guarded

pytest.importorskip("playwright.sync_api")


def _rtc(url: str) -> str:
    return (
        "<script>const pc=new RTCPeerConnection({{iceServers:[{{urls:'" + url + "',"
        "username:'u',credential:'c'}}]}});pc.createDataChannel('x');"
        "pc.createOffer().then(o=>pc.setLocalDescription(o));</script>"
    )


RTC_VECTORS = {
    "webrtc-turn-tcp": _rtc("turn:{host}:{port}?transport=tcp"),
    "webrtc-turns-tcp": _rtc("turns:{host}:{port}?transport=tcp"),
    "webrtc-stun-udp": _rtc("stun:{host}:{port}"),
    "webrtc-turn-udp": _rtc("turn:{host}:{port}?transport=udp"),
    "webtransport": "<script>try{{new WebTransport('https://{host}:{port}/')}}catch(e){{}}</script>",
}

VECTORS = {
    "preconnect": '<link rel="preconnect" href="http://{host}:{port}">',
    "preconnect-crossorigin": '<link rel="preconnect" href="http://{host}:{port}" crossorigin>',
    "dns-prefetch": '<link rel="dns-prefetch" href="http://{host}:{port}">',
    "prefetch": '<link rel="prefetch" href="http://{host}:{port}/x">',
    **RTC_VECTORS,
    "send-beacon": "<script>navigator.sendBeacon('http://{host}:{port}/b','x')</script>",
    "anchor-ping": (
        '<a id="a" href="about:blank" ping="http://{host}:{port}/p">x</a>'
        "<script>document.getElementById('a').click()</script>"
    ),
    "event-source": "<script>new EventSource('http://{host}:{port}/e')</script>",
    "fetch-keepalive": "<script>fetch('http://{host}:{port}/f',{{keepalive:true,mode:'no-cors'}}).catch(()=>{{}})</script>",
}

LOOKUP_PROBE = (
    "<script>const pc=new RTCPeerConnection({iceServers:["
    "{urls:'turn:leak-probe.invalid:3478?transport=tcp',username:'u',credential:'c'},"
    "{urls:'turns:leak-probe.invalid:5349?transport=tcp',username:'u',credential:'c'},"
    "{urls:'turn:leak-probe.invalid:3478?transport=udp',username:'u',credential:'c'},"
    "{urls:'stun:leak-probe.invalid:3478'}]});pc.createDataChannel('x');"
    "pc.createOffer().then(o=>pc.setLocalDescription(o));</script>"
)


class _Listener:
    def __init__(self, host: str = "127.0.0.1") -> None:
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        self.hits = 0
        self.tcp = socket.socket(family, socket.SOCK_STREAM)
        self.udp = socket.socket(family, socket.SOCK_DGRAM)
        try:
            self.tcp.bind((host, 0))
            self.tcp.listen(16)
            self.port = self.tcp.getsockname()[1]
            self.udp.bind((host, self.port))
        except OSError:
            self.tcp.close()
            self.udp.close()
            raise
        self.tcp.settimeout(0.1)
        self.udp.settimeout(0.1)
        self._stop = threading.Event()
        self._threads = [
            threading.Thread(target=self._serve_tcp, daemon=True),
            threading.Thread(target=self._serve_udp, daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def _serve_tcp(self) -> None:
        while not self._stop.is_set():
            try:
                connection, _ = self.tcp.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            self.hits += 1
            connection.close()

    def _serve_udp(self) -> None:
        while not self._stop.is_set():
            try:
                self.udp.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                return
            self.hits += 1

    def close(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=2)
        self.tcp.close()
        self.udp.close()


def _open_listener(host: str = "127.0.0.1") -> _Listener | None:
    for _ in range(10):
        try:
            return _Listener(host)
        except OSError:
            continue
    return None


@pytest.fixture
def listener():
    instance = _open_listener()
    if instance is None:
        pytest.skip("no free loopback port for both TCP and UDP")
    try:
        yield instance
    finally:
        instance.close()


def _chromium_unavailable(exc: Exception) -> None:
    message = f"chromium unavailable: {exc}"
    if os.environ.get("CI"):
        pytest.fail(message)
    pytest.skip(message)


def _launch_plain(playwright):
    return playwright.chromium.launch()


def _render(tmp_path: Path, body: str, launch=launch_guarded, settle=None) -> None:
    from playwright.sync_api import Error, sync_playwright

    page_path = tmp_path / "page.html"
    page_path.write_text(f"<!DOCTYPE html><html><head><meta charset=utf-8></head><body>{body}</body></html>", encoding="utf-8")

    with sync_playwright() as playwright:
        try:
            browser = launch(playwright)
        except Error as exc:
            _chromium_unavailable(exc)
        try:
            context = browser.new_context()
            guard_context(context, tmp_path.resolve())
            page = context.new_page()
            page.goto(page_path.resolve().as_uri())
            if settle is None:
                page.wait_for_timeout(2500)
            else:
                settle(page)
            page.goto("about:blank")
            page.wait_for_timeout(300)
        finally:
            browser.close()
    time.sleep(0.2)


@pytest.mark.parametrize("vector", list(VECTORS), ids=list(VECTORS))
def test_guarded_page_opens_no_connection_to_loopback_listener(tmp_path: Path, listener: _Listener, vector: str) -> None:
    _render(tmp_path, VECTORS[vector].format(host="127.0.0.1", port=listener.port))

    assert listener.hits == 0


@pytest.mark.parametrize("vector", list(RTC_VECTORS), ids=list(RTC_VECTORS))
def test_vector_connects_without_launch_flags(tmp_path: Path, listener: _Listener, vector: str) -> None:
    _render(tmp_path, RTC_VECTORS[vector].format(host="127.0.0.1", port=listener.port), _launch_plain)

    assert listener.hits > 0


@pytest.mark.parametrize("host", ["localhost", "[::1]"])
def test_guarded_page_blocks_loopback_host_forms(tmp_path: Path, host: str) -> None:
    probe = _open_listener("::1" if host == "[::1]" else "127.0.0.1")
    if probe is None:
        pytest.skip(f"cannot bind a loopback listener for {host}")
    try:
        body = RTC_VECTORS["webrtc-turn-tcp"].format(host=host, port=probe.port)
        _render(tmp_path, body, _launch_plain)
        assert probe.hits > 0
        probe.hits = 0
        _render(tmp_path, body)
        assert probe.hits == 0
    finally:
        probe.close()


def _net_log_after_render(tmp_path: Path, launch_args: list[str], expect_entry: bool) -> str:
    log_path = tmp_path / "net.json"
    needle = "leak-probe.invalid"

    def settle(page) -> None:
        deadline = time.monotonic() + (15 if expect_entry else 6)
        while time.monotonic() < deadline:
            page.wait_for_timeout(250)
            if expect_entry and log_path.exists() and needle in log_path.read_text(encoding="utf-8", errors="replace"):
                return

    _render(
        tmp_path,
        LOOKUP_PROBE,
        lambda playwright: playwright.chromium.launch(args=[*launch_args, f"--log-net-log={log_path}"]),
        settle,
    )
    return log_path.read_text(encoding="utf-8", errors="replace")


def test_net_log_records_ice_server_name_lookup_without_launch_flags(tmp_path: Path) -> None:
    assert "leak-probe.invalid" in _net_log_after_render(tmp_path, [], True)


def test_guarded_page_resolves_no_ice_server_names(tmp_path: Path) -> None:
    assert "leak-probe.invalid" not in _net_log_after_render(tmp_path, CHROMIUM_LAUNCH_ARGS, False)


def test_guarded_page_still_renders_local_file(tmp_path: Path) -> None:
    from playwright.sync_api import Error, sync_playwright

    page_path = tmp_path / "page.html"
    page_path.write_text("<!DOCTYPE html><html><body><p id=t>ok</p></body></html>", encoding="utf-8")

    with sync_playwright() as playwright:
        try:
            browser = launch_guarded(playwright)
        except Error as exc:
            _chromium_unavailable(exc)
        try:
            context = browser.new_context()
            guard_context(context, tmp_path.resolve())
            page = context.new_page()
            page.goto(page_path.resolve().as_uri())
            assert page.inner_text("#t") == "ok"
        finally:
            browser.close()


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("ftp://127.0.0.1/x", "abort"),
        ("gopher://127.0.0.1/x", "abort"),
        ("chrome://version/", "abort"),
        ("data:text/plain,x", "continue"),
        ("blob:null/1234", "continue"),
        ("about:blank", "continue"),
    ],
)
def test_route_handler_continues_only_allowlisted_schemes(tmp_path: Path, url: str, expected: str) -> None:
    from wra.browser.guard import _make_route_handler

    class Route:
        outcome = None

        def abort(self, *_args) -> None:
            self.outcome = "abort"

        def continue_(self) -> None:
            self.outcome = "continue"

    class Request:
        pass

    request = Request()
    request.url = url
    route = Route()
    _make_route_handler(tmp_path.resolve())(route, request)
    assert route.outcome == expected
