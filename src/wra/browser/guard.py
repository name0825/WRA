from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlparse


CHROMIUM_LAUNCH_ARGS = [
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--dns-prefetch-disable",
    "--proxy-server=http://127.0.0.1:1",
    "--proxy-bypass-list=<-loopback>",
]


def launch_guarded(playwright):
    return playwright.chromium.launch(args=list(CHROMIUM_LAUNCH_ARGS))


def _path_from_file_url(url: str) -> Path:
    raw = unquote(urlparse(url).path)
    if len(raw) >= 3 and raw[0] == "/" and raw[2] == ":":
        raw = raw[1:]
    if raw[:2] in ("//", "\\\\", "/\\", "\\/"):
        # An empty netloc with a doubled leading slash/backslash is still a
        # UNC path (file:////evil/share/x): resolve() can touch it - and leak
        # NTLM credentials to it - before any containment check runs.
        raise ValueError(f"UNC-like file path rejected: {url}")
    return Path(raw)


def _make_route_handler(allowed_root: Path | None):
    def handle_route(route, request):
        scheme = request.url.split(":", 1)[0].lower()
        if scheme in ("http", "https"):
            route.abort("blockedbyclient")
            return
        if scheme == "file":
            if allowed_root is None:
                route.abort()
                return
            netloc = urlparse(request.url).netloc
            if netloc and netloc.lower() != "localhost":
                # A non-empty, non-localhost netloc makes this a UNC/SMB
                # request (file://evil/share/...), which the path check below
                # never sees since it only looks at the URL's path part.
                route.abort()
                return
            try:
                requested = _path_from_file_url(request.url).resolve()
            except (OSError, ValueError):
                route.abort()
                return
            if requested == allowed_root or allowed_root in requested.parents:
                route.continue_()
            else:
                route.abort()
            return
        if scheme in ("data", "blob", "about"):
            route.continue_()
        else:
            route.abort()

    return handle_route


def _handle_web_socket(ws) -> None:
    # ``context.route`` never sees WebSocket upgrades, so page JS could still
    # reach ws://127.0.0.1:* or any remote host through one. Never calling
    # ``connect_to_server()`` here means no real socket to that host is ever
    # opened - the page gets a local mock connection instead. (``ws.close()``
    # deadlocks the sync driver when called from inside this handler, so it
    # is deliberately not called; not connecting is what blocks the network.)
    pass


def guard_context(context, allowed_root: Path | None) -> None:
    context.route("**/*", _make_route_handler(allowed_root))
    context.route_web_socket("**/*", _handle_web_socket)
