"""Local Flask console for WRA: run artifacts plus the admin pages."""

import json
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, abort, render_template_string, request, send_from_directory

from wra.core.config import load_raw
from wra.core.models import ViewerConfig
from wra.viewer.admin import create_admin_blueprint
from wra.viewer.data import ARTIFACTS, asset_folder, criteria_scores, is_slug, iteration_artifacts, list_runs, load_run, read_json, safe_run_dir, score_band
from wra.viewer.inputs import MAX_UPLOAD_BYTES
from wra.viewer.templates import BASE_CSS, ITERATION_TEMPLATE, JSON_TEMPLATE, RUN_DETAIL_TEMPLATE, RUN_LIST_TEMPLATE


def _bare_slug(value: str) -> str:
    return value[:-5] if value.endswith(".html") else value


def _sandboxed(response):
    response.headers["Content-Security-Policy"] = "sandbox allow-scripts"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


LOOPBACK_HOSTNAMES = {"127.0.0.1", "localhost", "::1"}


def _host_allowed(host_header: str, config: ViewerConfig) -> bool:
    """Reject Host headers a DNS-rebinding page could forge onto a loopback request.

    A request that reaches this process at all already proves the TCP
    connection is to the configured bind address; what is not proven is that
    the browser's own same-origin checks (Origin/Referer) were computed
    against a name anyone but the attacker controls.
    """
    hostname = urlsplit(f"//{host_header}").hostname
    if hostname is None:
        return False
    hostname = hostname.lower()
    if hostname in LOOPBACK_HOSTNAMES:
        return True
    bind_host = (config.host or "").strip("[]").lower()
    return bind_host not in LOOPBACK_HOSTNAMES and bind_host != "" and hostname == bind_host


def load_viewer_config() -> ViewerConfig:
    # The viewer only reads finished runs, so an unconfigured checkout should
    # still be able to browse them on the defaults.
    return ViewerConfig.model_validate(load_raw(required=()).get("viewer") or {})


def create_app(config: ViewerConfig | None = None) -> Flask:
    resolved_config = config or load_viewer_config()
    root = Path(resolved_config.output_root).resolve()
    app = Flask(__name__)
    # Admin logins live in the session cookie; a per-process key means a restart
    # simply asks for the password again, which is the safer default here.
    app.secret_key = secrets.token_hex(32)
    # The console is served over plain HTTP by default, so Secure would lock
    # the user out; SameSite still keeps the cookie off cross-site requests.
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    # A folder drop legitimately carries many files in one request; 20x a
    # single file's cap bounds the request without rejecting an ordinary drop.
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES * 20
    if resolved_config.admin.enabled:
        app.register_blueprint(create_admin_blueprint(resolved_config, root))

    @app.before_request
    def _check_host():
        if not _host_allowed(request.host, resolved_config):
            abort(403)

    def band(score: float | None, maximum: float = 10.0) -> str:
        normalized = None if score is None else score * 10.0 / maximum
        return score_band(normalized, resolved_config.score_high_threshold, resolved_config.score_mid_threshold)

    def require_run(run_id: str, page: str | None = None):
        run = load_run(root, run_id, page)
        if run is None:
            abort(404)
        return run

    @app.route("/")
    def index():
        return render_template_string(RUN_LIST_TEMPLATE, css=BASE_CSS, runs=list_runs(root), output_root=root, score_band=band)

    @app.route("/run/<run_id>")
    def run_detail(run_id: str):
        return render_template_string(RUN_DETAIL_TEMPLATE, css=BASE_CSS, run=require_run(run_id, request.args.get("page")), score_band=band, artifacts=iteration_artifacts)

    @app.route("/run/<run_id>/iteration/<int:number>")
    def iteration_detail(run_id: str, number: int):
        run = require_run(run_id, request.args.get("page"))
        iteration = next((item for item in run.iterations if item.number == number), None)
        if iteration is None:
            abort(404)
        numbers = [item.number for item in run.iterations]
        position = numbers.index(number)
        evaluation = iteration.evaluation or {}
        suggestions = evaluation.get("improvement_suggestions")
        suggestions = [item for item in suggestions if isinstance(item, str)] if isinstance(suggestions, list) else []
        return render_template_string(ITERATION_TEMPLATE, css=BASE_CSS, run=run, iteration=iteration, score_band=band,
            criteria=criteria_scores(iteration.evaluation), summary=evaluation.get("summary"), suggestions=suggestions,
            validation=json.dumps(iteration.validation, ensure_ascii=False, indent=2) if iteration.validation else None,
            artifacts=iteration_artifacts(iteration), previous=numbers[position - 1] if position else None,
            next=numbers[position + 1] if position + 1 < len(numbers) else None)

    @app.route("/run/<run_id>/features")
    def run_features(run_id: str):
        run = require_run(run_id, request.args.get("page"))
        if run.features is None:
            abort(404)
        return render_template_string(JSON_TEMPLATE, css=BASE_CSS, run_id=run_id, heading="features.json", payload=json.dumps(run.features, ensure_ascii=False, indent=2))

    @app.route("/json/<run_id>/iteration/<int:number>/<artifact>")
    def iteration_json(run_id: str, number: int, artifact: str):
        run = require_run(run_id, request.args.get("page"))
        filename = ARTIFACTS.get(artifact)
        if filename is None:
            abort(404)
        payload = read_json(run.directory / "iterations" / f"iter{number}" / "data" / run.slug / filename)
        if payload is None:
            abort(404)
        return render_template_string(JSON_TEMPLATE, css=BASE_CSS, run_id=run_id, heading=f"반복 {number} / {filename}", payload=json.dumps(payload, ensure_ascii=False, indent=2))

    @app.route("/raw/<run_id>/final/<slug>")
    def raw_final(run_id: str, slug: str):
        slug = _bare_slug(slug)
        run_dir = safe_run_dir(root, run_id)
        if run_dir is None or not is_slug(slug):
            abort(404)
        return _sandboxed(send_from_directory(run_dir, f"{slug}.html"))

    @app.route("/raw/<run_id>/original/<slug>")
    def raw_original(run_id: str, slug: str):
        slug = _bare_slug(slug)
        run_dir = safe_run_dir(root, run_id)
        if run_dir is None or not is_slug(slug):
            abort(404)
        return _sandboxed(send_from_directory(run_dir / "data" / slug, "original.html"))

    @app.route("/raw/<run_id>/iteration/<int:number>/<slug>")
    def raw_iteration(run_id: str, number: int, slug: str):
        slug = _bare_slug(slug)
        run_dir = safe_run_dir(root, run_id)
        if run_dir is None or not is_slug(slug):
            abort(404)
        return _sandboxed(send_from_directory(run_dir / "iterations" / f"iter{number}", f"{slug}.html"))

    # Generated pages reference media as `<asset folder>/<file>`, relative to
    # wherever the page itself is served from, so every raw view needs the same
    # tree. Only the folder the run recorded is served, never the URL's choice.
    @app.route("/raw/<run_id>/<path:asset>")
    @app.route("/raw/<run_id>/final/<path:asset>")
    @app.route("/raw/<run_id>/original/<path:asset>")
    @app.route("/raw/<run_id>/iteration/<int:number>/<path:asset>")
    def raw_asset(run_id: str, asset: str, number: int | None = None):
        run_dir = safe_run_dir(root, run_id)
        if run_dir is None:
            abort(404)
        folder = asset_folder(run_dir)
        if not asset.startswith(f"{folder}/"):
            abort(404)
        return _sandboxed(send_from_directory(run_dir / folder, asset[len(folder) + 1:]))

    return app


def main() -> None:
    config = load_viewer_config()
    print(f"웹 브라우저를 열고 http://localhost:{config.port} 으로 접속하세요.")
    if config.admin.enabled:
        print(f"어드민 콘솔: http://localhost:{config.port}/admin")
    create_app(config).run(debug=config.debug, port=config.port, host=config.host, threaded=True)


if __name__ == "__main__":
    main()
