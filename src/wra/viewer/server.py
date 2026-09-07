"""Local Flask viewer for WRA run artifacts."""

import json
import re
from pathlib import Path

from flask import Flask, abort, render_template_string, request, send_from_directory

from wra.core.config import load_raw
from wra.core.models import ViewerConfig
from wra.viewer.data import ARTIFACTS, criteria_scores, iteration_artifacts, list_runs, load_run, read_json, safe_run_dir, score_band
from wra.viewer.templates import BASE_CSS, ITERATION_TEMPLATE, JSON_TEMPLATE, RUN_DETAIL_TEMPLATE, RUN_LIST_TEMPLATE

SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _is_slug(value: str) -> bool:
    """Guard the raw routes: a slug names a file, never a path."""
    return bool(SLUG_RE.match(value)) and ".." not in value


def load_viewer_config() -> ViewerConfig:
    # The viewer only reads finished runs, so an unconfigured checkout should
    # still be able to browse them on the defaults.
    return ViewerConfig.model_validate(load_raw(required=()).get("viewer") or {})


def create_app(config: ViewerConfig | None = None) -> Flask:
    resolved_config = config or load_viewer_config()
    root = Path(resolved_config.output_root).resolve()
    app = Flask(__name__)

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
        return render_template_string(ITERATION_TEMPLATE, css=BASE_CSS, run=run, iteration=iteration, score_band=band,
            criteria=criteria_scores(iteration.evaluation), summary=evaluation.get("summary"), suggestions=evaluation.get("improvement_suggestions") or [],
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
        run_dir = safe_run_dir(root, run_id)
        if run_dir is None or not _is_slug(slug):
            abort(404)
        return send_from_directory(run_dir, f"{slug}.html")

    @app.route("/raw/<run_id>/original/<slug>")
    def raw_original(run_id: str, slug: str):
        run_dir = safe_run_dir(root, run_id)
        if run_dir is None or not _is_slug(slug):
            abort(404)
        return send_from_directory(run_dir / "data" / slug, "original.html")

    @app.route("/raw/<run_id>/iteration/<int:number>/<slug>")
    def raw_iteration(run_id: str, number: int, slug: str):
        run_dir = safe_run_dir(root, run_id)
        if run_dir is None or not _is_slug(slug):
            abort(404)
        return send_from_directory(run_dir / "iterations" / f"iter{number}", f"{slug}.html")

    # Generated pages reference media as `assets/<file>`, relative to wherever
    # the page itself is served from, so every raw view needs the same tree.
    @app.route("/raw/<run_id>/assets/<path:asset>")
    @app.route("/raw/<run_id>/final/assets/<path:asset>")
    @app.route("/raw/<run_id>/original/assets/<path:asset>")
    @app.route("/raw/<run_id>/iteration/<int:number>/assets/<path:asset>")
    def raw_asset(run_id: str, asset: str, number: int | None = None):
        run_dir = safe_run_dir(root, run_id)
        if run_dir is None:
            abort(404)
        return send_from_directory(run_dir / "assets", asset)

    return app


def main() -> None:
    config = load_viewer_config()
    print(f"웹 브라우저를 열고 http://localhost:{config.port} 으로 접속하세요.")
    create_app(config).run(debug=config.debug, port=config.port, host=config.host)


if __name__ == "__main__":
    main()
