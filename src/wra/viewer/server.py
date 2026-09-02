"""Local Flask viewer for WRA run artifacts."""

import json
from pathlib import Path

import yaml
from flask import Flask, abort, render_template_string, send_from_directory

from wra.core.models import ViewerConfig
from wra.viewer.data import criteria_scores, iteration_artifacts, list_runs, load_run, read_json, safe_run_dir, score_band
from wra.viewer.templates import BASE_CSS, ITERATION_TEMPLATE, JSON_TEMPLATE, RUN_DETAIL_TEMPLATE, RUN_LIST_TEMPLATE

CONFIG_PATH = Path("config/config.yaml")
ARTIFACT_FILES = {"design": "design.json", "validation": "validation.json", "evaluation": "evaluation.json", "browser": "browser.json"}


def load_viewer_config() -> ViewerConfig:
    if not CONFIG_PATH.is_file():
        return ViewerConfig()
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    return ViewerConfig.model_validate(data.get("viewer") or {})


def create_app(config: ViewerConfig | None = None) -> Flask:
    resolved_config = config or load_viewer_config()
    root = Path(resolved_config.output_root).resolve()
    app = Flask(__name__)

    def band(score: float | None, maximum: float = 10.0) -> str:
        normalized = None if score is None else score * 10.0 / maximum
        return score_band(normalized, resolved_config.score_high_threshold, resolved_config.score_mid_threshold)

    def require_run(run_id: str):
        run = load_run(root, run_id)
        if run is None:
            abort(404)
        return run

    @app.route("/")
    def index():
        return render_template_string(RUN_LIST_TEMPLATE, css=BASE_CSS, runs=list_runs(root), output_root=root, score_band=band)

    @app.route("/run/<run_id>")
    def run_detail(run_id: str):
        return render_template_string(RUN_DETAIL_TEMPLATE, css=BASE_CSS, run=require_run(run_id), score_band=band, artifacts=iteration_artifacts)

    @app.route("/run/<run_id>/iteration/<int:number>")
    def iteration_detail(run_id: str, number: int):
        run = require_run(run_id)
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
        run = require_run(run_id)
        if run.features is None:
            abort(404)
        return render_template_string(JSON_TEMPLATE, css=BASE_CSS, run_id=run_id, heading="features.json", payload=json.dumps(run.features, ensure_ascii=False, indent=2))

    @app.route("/json/<run_id>/iteration/<int:number>/<artifact>")
    def iteration_json(run_id: str, number: int, artifact: str):
        run = require_run(run_id)
        filename = ARTIFACT_FILES.get(artifact)
        if filename is None:
            abort(404)
        payload = read_json(run.directory / "iterations" / f"iteration_{number:03d}" / filename)
        if payload is None:
            abort(404)
        return render_template_string(JSON_TEMPLATE, css=BASE_CSS, run_id=run_id, heading=f"반복 {number} / {filename}", payload=json.dumps(payload, ensure_ascii=False, indent=2))

    @app.route("/raw/<run_id>/<page>")
    def raw_page(run_id: str, page: str):
        run_dir = safe_run_dir(root, run_id)
        if run_dir is None or page not in {"final", "original"}:
            abort(404)
        return send_from_directory(run_dir, f"{page}.html")

    @app.route("/raw/<run_id>/iteration/<int:number>")
    def raw_iteration(run_id: str, number: int):
        run_dir = safe_run_dir(root, run_id)
        if run_dir is None:
            abort(404)
        return send_from_directory(run_dir / "iterations" / f"iteration_{number:03d}", "html.html")

    return app


def main() -> None:
    config = load_viewer_config()
    print(f"웹 브라우저를 열고 http://localhost:{config.port} 으로 접속하세요.")
    create_app(config).run(debug=config.debug, port=config.port, host=config.host)


if __name__ == "__main__":
    main()
