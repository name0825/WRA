import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import pytest

from wra.core.models import ViewerConfig
from wra.core.output import OutputManager
from wra.viewer import data as viewer_data
from wra.viewer.data import _number, asset_folder, criteria_scores, is_slug, iteration_artifacts, load_run, page_slugs, read_json, safe_run_dir

pytest.importorskip("flask")

from wra.viewer.server import create_app  # noqa: E402


@pytest.fixture
def viewer(tmp_path: Path):
    root = tmp_path / "output"
    run = root / "run_20260101_000000"
    (run / "assets").mkdir(parents=True)
    (run / "assets" / "hero.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    for slug in ("index", "about"):
        data_dir = run / "data" / slug
        data_dir.mkdir(parents=True)
        (data_dir / "original.html").write_text(f"<html>original {slug}</html>", encoding="utf-8")
        (data_dir / "features.json").write_text(json.dumps({"metadata": {"title": f"{slug} title"}}), encoding="utf-8")

    manager = OutputManager(run, "all")
    for iteration in (1, 2):
        for slug in ("index", "about"):
            manager.save_iteration(
                iteration=iteration, slug=slug,
                html=f'<html><img src="assets/hero.png">{slug} v{iteration}</html>',
                validation={"valid": True}, evaluation={"overall_score": 7.0 + iteration, "summary": "ok"},
                design_spec={"iteration": iteration}, score=7.0 + iteration,
            )
    for slug in ("index", "about"):
        manager.finalize(slug)

    app = create_app(ViewerConfig(output_root=str(root)))
    return app.test_client(), run.name


def test_run_view_defaults_to_the_entry_page(viewer) -> None:
    client, run_id = viewer
    body = client.get(f"/run/{run_id}").get_data(as_text=True)
    assert "index title" in body


def test_run_view_switches_between_pages(viewer) -> None:
    client, run_id = viewer
    body = client.get(f"/run/{run_id}?page=about").get_data(as_text=True)
    assert "about title" in body
    assert f"/raw/{run_id}/final/about" in body


def test_iteration_pages_and_their_artifacts_are_reachable(viewer) -> None:
    client, run_id = viewer
    assert client.get(f"/run/{run_id}/iteration/2?page=about").status_code == 200
    assert client.get(f"/raw/{run_id}/iteration/1/about").status_code == 200
    assert client.get(f"/json/{run_id}/iteration/1/design?page=index").status_code == 200


@pytest.mark.parametrize("view", ["", "/iteration/1"])
def test_json_links_carry_the_selected_page_and_serve_its_artifact(tmp_path: Path, viewer, view: str) -> None:
    client, run_id = viewer
    run = tmp_path / "output" / run_id
    for slug in ("index", "about"):
        (run / "iterations" / "iter1" / "data" / slug / "design.json").write_text(json.dumps({"marker": f"{slug}-design"}), encoding="utf-8")
    body = client.get(f"/run/{run_id}{view}?page=about").get_data(as_text=True)
    link = f"/json/{run_id}/iteration/1/design?page=about"
    assert link in body
    served = client.get(link).get_data(as_text=True)
    assert "about-design" in served and "index-design" not in served
    assert "index-design" in client.get(f"/json/{run_id}/iteration/1/design").get_data(as_text=True)
    assert "index-design" in client.get(f"/json/{run_id}/iteration/1/design?page=..%2F..").get_data(as_text=True)


def test_raw_routes_accept_the_real_html_filename(viewer) -> None:
    client, run_id = viewer
    for prefix in ("final", "original", "iteration/1"):
        assert client.get(f"/raw/{run_id}/{prefix}/about").status_code == 200
        assert client.get(f"/raw/{run_id}/{prefix}/about.html").status_code == 200
        assert client.get(f"/raw/{run_id}/{prefix}/missing.html").status_code == 404
    assert client.get(f"/raw/{run_id}/final/..%2f..%2fetc.html").status_code == 404


def test_assets_resolve_from_every_page_location(viewer) -> None:
    """An iteration page reaches assets through ../../, a final page directly."""
    client, run_id = viewer
    assert client.get(f"/raw/{run_id}/iteration/1/assets/hero.png").status_code == 200
    assert client.get(f"/raw/{run_id}/final/assets/hero.png").status_code == 200
    assert client.get(f"/raw/{run_id}/assets/hero.png").status_code == 200


def test_a_slug_may_not_escape_the_run_directory(viewer) -> None:
    client, run_id = viewer
    assert client.get(f"/raw/{run_id}/final/../../etc").status_code == 404
    assert client.get(f"/raw/{run_id}/original/..%2f..%2fetc").status_code == 404


def test_a_raw_asset_is_sandboxed_against_script_execution(viewer) -> None:
    client, run_id = viewer
    response = client.get(f"/raw/{run_id}/assets/hero.png")
    assert response.headers["Content-Security-Policy"] == "sandbox allow-scripts"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_a_forged_host_header_is_refused(viewer) -> None:
    client, _ = viewer
    assert client.get("/", headers={"Host": "evil.example:5002"}).status_code == 403
    assert client.get("/", headers={"Host": "localhost"}).status_code == 200
    assert client.get("/", headers={"Host": "127.0.0.1:5002"}).status_code == 200


def test_a_run_in_progress_still_shows_the_entry_page_first(tmp_path: Path) -> None:
    """Alphabetical order would put 'about' before 'index'."""
    root = tmp_path / "output"
    run = root / "run_20260101_000000"
    for slug in ("about", "index"):
        (run / "data" / slug).mkdir(parents=True)
    OutputManager(run, "all").register_pages(["index", "about"])

    assert load_run(root, run.name).slug == "index"

    client = create_app(ViewerConfig(output_root=str(root))).test_client()
    body = client.get(f"/run/{run.name}").get_data(as_text=True)
    # The selector marks the page being shown; the other is a link.
    assert "<strong>index</strong>" in body
    assert f"/run/{run.name}?page=about" in body


def test_an_unknown_page_falls_back_to_the_entry_page(viewer) -> None:
    client, run_id = viewer
    body = client.get(f"/run/{run_id}?page=nope").get_data(as_text=True)
    assert "index title" in body


@pytest.mark.parametrize("run_id", ["D:..", "D:..\\x.html", "C:/Windows/win.ini", "\\\\host\\share\\x", "../x.html", "..", ".", "", "C:", "D:foo", "a/b"])
def test_a_run_id_may_not_leave_the_output_root(tmp_path: Path, monkeypatch, run_id: str) -> None:
    root = tmp_path / "output"
    (root / "run_1").mkdir(parents=True)
    (tmp_path / "x.html").write_text("<html>secret</html>", encoding="utf-8")
    monkeypatch.chdir(root)
    assert safe_run_dir(root, run_id) is None


def test_a_plain_run_id_still_resolves(tmp_path: Path) -> None:
    root = tmp_path / "output"
    (root / "run_1").mkdir(parents=True)
    assert safe_run_dir(root, "run_1") == root / "run_1"
    assert safe_run_dir(root, "missing") is None


def test_a_drive_relative_run_id_cannot_serve_html_outside_the_root(viewer) -> None:
    client, _ = viewer
    assert client.get("/raw/D:../final/index").status_code == 404
    assert client.get("/run/D:..").status_code == 404


def _build_run(tmp_path: Path, folder: str, recorded: object = None):
    root = tmp_path / "output"
    run = root / "run_20260101_000000"
    for name in {folder, "assets"}:
        (run / name).mkdir(parents=True, exist_ok=True)
    (run / folder / "hero.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (run / "assets" / "decoy.png").write_bytes(b"decoy")
    (root / "other").mkdir()
    (root / "other" / "hero.png").write_bytes(b"outside")
    data_dir = run / "data" / "about"
    data_dir.mkdir(parents=True)
    (data_dir / "original.html").write_text("<html>original</html>", encoding="utf-8")
    manager = OutputManager(run, "all", folder)
    manager.save_iteration(iteration=1, slug="about", html=f'<html><img src="{folder}/hero.png"></html>', score=8.0)
    manager.finalize("about")
    if recorded is not None:
        result = json.loads((run / "result.json").read_text(encoding="utf-8"))
        result["asset_directory"] = recorded
        (run / "result.json").write_text(json.dumps(result), encoding="utf-8")
    return create_app(ViewerConfig(output_root=str(root))).test_client(), run


@pytest.mark.parametrize("folder", ["media", "static/img"])
def test_a_custom_asset_folder_is_served_to_every_page_location(tmp_path: Path, folder: str) -> None:
    client, run = _build_run(tmp_path, folder, recorded=folder)
    pages = {
        f"/raw/{run.name}/iteration/1/about": run / "iterations" / "iter1" / "about.html",
        f"/raw/{run.name}/final/about": run / "about.html",
    }
    for page_url, html_path in pages.items():
        source = re.search(r'src="([^"]+)"', html_path.read_text(encoding="utf-8")).group(1)
        response = client.get(urlsplit(urljoin(f"http://localhost{page_url}", source)).path)
        assert response.status_code == 200, page_url
        assert response.data.startswith(b"\x89PNG")
    assert client.get(f"/raw/{run.name}/{folder}/hero.png").status_code == 200
    assert client.get(f"/raw/{run.name}/original/{folder}/hero.png").status_code == 200


def test_only_the_recorded_asset_folder_is_reachable(tmp_path: Path) -> None:
    client, run = _build_run(tmp_path, "media", recorded="media")
    for path in ("assets/decoy.png", "data/about/original.html", "iterations/iter1/about.html", "result.json"):
        for prefix in ("", "final/", "original/", "iteration/1/"):
            assert client.get(f"/raw/{run.name}/{prefix}{path}").status_code == 404, (prefix, path)


def test_a_run_that_records_no_asset_folder_serves_assets(tmp_path: Path) -> None:
    client, run = _build_run(tmp_path, "assets")
    assert client.get(f"/raw/{run.name}/assets/hero.png").status_code == 200
    assert client.get(f"/raw/{run.name}/iteration/1/assets/hero.png").status_code == 200
    assert asset_folder(run) == "assets"


@pytest.mark.parametrize("path", [
    "assets/../data/about/original.html",
    "assets/%2e%2e/result.json",
    "assets/..%5cresult.json",
    "assets/%5c..%5cresult.json",
    "assets//etc/passwd",
    "assets/%2fetc%2fpasswd",
    "assets/C:/Windows/win.ini",
    "assets/C:%5cWindows%5cwin.ini",
])
def test_an_asset_path_may_not_escape_the_asset_folder(viewer, path: str) -> None:
    client, run_id = viewer
    for prefix in ("", "final/", "original/", "iteration/1/"):
        assert client.get(f"/raw/{run_id}/{prefix}{path}").status_code == 404, prefix


@pytest.mark.parametrize("recorded", ["..", "../other", "C:/x", "/etc", "\\etc", "a\\b", "x/../y", "./x", "x//y", "", ".", "a\x00b", 7, ["assets"], None])
def test_an_unsafe_recorded_asset_folder_falls_back_to_assets(tmp_path: Path, recorded: object) -> None:
    client, run = _build_run(tmp_path, "assets")
    result = json.loads((run / "result.json").read_text(encoding="utf-8"))
    result["asset_directory"] = recorded
    (run / "result.json").write_text(json.dumps(result), encoding="utf-8")
    assert asset_folder(run) == "assets"
    assert client.get(f"/raw/{run.name}/assets/hero.png").status_code == 200
    for path in ("other/hero.png", "../other/hero.png", "hero.png", "etc/hero.png"):
        assert client.get(f"/raw/{run.name}/{path}").status_code == 404, path


@pytest.mark.parametrize("content", [
    "[1]", '"str"', "5", "true", "null", '{"asset_directory": "med',
    pytest.param("9" * 5000, id="huge-int"), pytest.param("[" * 100000, id="deep-nesting"),
])
def test_a_malformed_result_json_falls_back_to_assets_without_a_server_error(tmp_path: Path, content: str) -> None:
    client, run = _build_run(tmp_path, "assets")
    (run / "result.json").write_text(content, encoding="utf-8")
    assert asset_folder(run) == "assets"
    assert client.get(f"/raw/{run.name}/assets/hero.png").status_code == 200
    assert client.get(f"/raw/{run.name}/assets/x.png").status_code == 404
    assert client.get(f"/raw/{run.name}/data/about/original.html").status_code == 404


@pytest.mark.parametrize("content", [
    "[1]", '"str"', "5", "true", "null", '{"pages": ', pytest.param("9" * 5000, id="huge-int"), pytest.param("[" * 100000, id="deep-nesting"),
    '{"pages": "x"}', '{"pages": [1, null]}', '{"pages": 5}', '{"pages": {"about": 5}}', '{"pages": {"about": [1]}}',
    '{"pages": {"about": {"best_score": "high", "best_iteration": "x"}}}', '{"entry": ["about"], "pages": {"about": {}}}', '{"best_score": "high"}',
])
def test_a_malformed_result_json_does_not_break_the_run_pages(tmp_path: Path, content: str) -> None:
    client, run = _build_run(tmp_path, "assets")
    (run / "result.json").write_text(content, encoding="utf-8")
    assert client.get("/").status_code == 200
    assert client.get(f"/run/{run.name}").status_code == 200
    assert client.get(f"/run/{run.name}?page=about").status_code == 200


@pytest.mark.parametrize("name", ["evaluation.json", "validation.json", "features.json"])
@pytest.mark.parametrize("content", ["[1]", '"str"', "null", pytest.param("[" * 100000, id="deep-nesting"), pytest.param('{"a":' * 1500 + "1" + "}" * 1500, id="object-nested-past-the-indenting-encoder")])
def test_a_non_object_page_artifact_does_not_break_the_run_pages(tmp_path: Path, name: str, content: str) -> None:
    client, run = _build_run(tmp_path, "assets")
    (run / "iterations" / "iter1" / "data" / "about" / name if name != "features.json" else run / "data" / "about" / name).write_text(content, encoding="utf-8")
    assert client.get("/").status_code == 200
    assert client.get(f"/run/{run.name}").status_code == 200
    assert client.get(f"/run/{run.name}/iteration/1").status_code == 200
    assert client.get(f"/run/{run.name}/features").status_code in {200, 404}


@pytest.mark.parametrize("recorded", ["final", "original", "iteration/3", "data", "Data", "iterations", "ITERATIONS", ".git", ".hidden/x", "data/sub", "data.", "data ", "Final.", "media/sub.", " data", "media/ sub"])
def test_a_reserved_recorded_asset_folder_falls_back_to_assets(tmp_path: Path, recorded: str) -> None:
    client, run = _build_run(tmp_path, "assets")
    result = json.loads((run / "result.json").read_text(encoding="utf-8"))
    result["asset_directory"] = recorded
    (run / "result.json").write_text(json.dumps(result), encoding="utf-8")
    assert asset_folder(run) == "assets"
    assert client.get(f"/raw/{run.name}/assets/hero.png").status_code == 200
    for path in ("data/about/original.html", "iterations/iter1/about.html", f"{recorded}/about/original.html"):
        assert client.get(f"/raw/{run.name}/{path}").status_code == 404, path


def _patch_result(run: Path, **changes: object) -> dict:
    result = json.loads((run / "result.json").read_text(encoding="utf-8"))
    result.update(changes)
    (run / "result.json").write_text(json.dumps(result), encoding="utf-8")
    return result


def _write_evaluation(run: Path, payload: object) -> None:
    (run / "iterations" / "iter1" / "data" / "about" / "evaluation.json").write_text(json.dumps(payload), encoding="utf-8")


HOSTILE_SLUGS = ["C:/Windows", "../../other", "//10.0.0.9/share/x", "..", "a/b", "a\b", "", "x\n"]


@pytest.mark.parametrize("bad", HOSTILE_SLUGS)
def test_a_recorded_page_key_that_is_not_a_plain_slug_is_dropped(tmp_path: Path, bad: str) -> None:
    client, run = _build_run(tmp_path, "assets")
    result = _patch_result(run, entry=bad, pages={"about": {}, bad: {"best_score": 9}})
    assert page_slugs(run, result) == ["about"]
    assert page_slugs(run, {"pages": {bad: {}}}) == ["about"]


@pytest.mark.parametrize("bad", ["C:/Windows", "../../other"])
def test_a_hostile_page_key_does_not_break_or_redirect_the_run_pages(tmp_path: Path, bad: str) -> None:
    client, run = _build_run(tmp_path, "assets")
    _patch_result(run, entry=bad, pages={bad: {"best_score": 9}, "about": {"best_score": 8}})
    assert client.get("/").status_code == 200
    assert client.get(f"/run/{run.name}").status_code == 200
    assert client.get(f"/run/{run.name}", query_string={"page": bad}).status_code == 200
    assert load_run(run.parent, run.name, bad).slug == "about"


def test_plain_recorded_page_keys_keep_their_recorded_order(tmp_path: Path) -> None:
    client, run = _build_run(tmp_path, "assets")
    (run / "data" / "index").mkdir()
    result = _patch_result(run, entry="index", pages={"about": {}, "index": {}})
    assert page_slugs(run, result) == ["index", "about"]


def test_an_int_too_large_for_a_float_is_not_a_number(tmp_path: Path) -> None:
    huge = int("9" * 400)
    assert _number(huge) is None
    assert _number(float("nan")) is None and _number(float("inf")) is None and _number(-float("inf")) is None
    assert _number(True) is None and _number("1") is None
    assert _number(7) == 7 and _number(7.5) == 7.5

    client, run = _build_run(tmp_path, "assets")
    result = _patch_result(run, best_score=huge)
    _patch_result(run, pages={"about": {**result["pages"]["about"], "best_score": huge, "best_iteration": huge}})
    _write_evaluation(run, {"overall_score": huge, "ux": {"score": huge}})
    assert client.get("/").status_code == 200
    assert client.get(f"/run/{run.name}").status_code == 200
    assert client.get(f"/run/{run.name}/iteration/1").status_code == 200


@pytest.mark.parametrize("score", ["high", [1], {"a": 1}, True, None])
def test_a_malformed_criterion_score_does_not_break_the_iteration_page(tmp_path: Path, score: object) -> None:
    client, run = _build_run(tmp_path, "assets")
    _write_evaluation(run, {"overall_score": 5, "ux": {"score": score}, "readability": [1], "originality": "x", "html_quality": {"score": 6, "feedback": "fine"}})
    assert [(key, value) for key, value, _ in criteria_scores(read_json(run / "iterations" / "iter1" / "data" / "about" / "evaluation.json"))] == [("ux", None), ("html_quality", 6)]
    assert client.get(f"/run/{run.name}/iteration/1").status_code == 200


@pytest.mark.parametrize("suggestions", [5, "text", {"a": 1}, True, [5, None, ["x"]]])
def test_malformed_improvement_suggestions_do_not_break_the_iteration_page(tmp_path: Path, suggestions: object) -> None:
    client, run = _build_run(tmp_path, "assets")
    _write_evaluation(run, {"overall_score": 5, "improvement_suggestions": suggestions})
    response = client.get(f"/run/{run.name}/iteration/1")
    assert response.status_code == 200
    assert "개선 제안" not in response.get_data(as_text=True)


def test_well_formed_improvement_suggestions_are_listed(tmp_path: Path) -> None:
    client, run = _build_run(tmp_path, "assets")
    _write_evaluation(run, {"overall_score": 5, "improvement_suggestions": ["tighten the hero", 3, "한글 제안"]})
    body = client.get(f"/run/{run.name}/iteration/1").get_data(as_text=True)
    assert "tighten the hero" in body and "한글 제안" in body


def test_a_lone_surrogate_makes_an_artifact_unusable_instead_of_breaking_the_pages(tmp_path: Path) -> None:
    client, run = _build_run(tmp_path, "assets")
    (run / "data" / "about" / "features.json").write_text(json.dumps({"metadata": {"title": "bad \ud800 title"}}), encoding="utf-8")
    _write_evaluation(run, {"overall_score": 5, "summary": "bad \ud800 summary"})
    _patch_result(run, note="bad \ud800 note")
    assert read_json(run / "data" / "about" / "features.json") is None
    assert read_json(run / "result.json") is None
    assert client.get("/").status_code == 200
    assert client.get(f"/run/{run.name}").status_code == 200
    assert client.get(f"/run/{run.name}/iteration/1").status_code == 200
    assert client.get(f"/run/{run.name}/features").status_code == 404
    assert client.get(f"/json/{run.name}/iteration/1/evaluation").status_code == 404


def test_non_ascii_artifacts_still_render(tmp_path: Path) -> None:
    client, run = _build_run(tmp_path, "assets")
    (run / "data" / "about" / "features.json").write_text(json.dumps({"metadata": {"title": "한글 제목 \U0001F600"}}), encoding="utf-8")
    assert "한글 제목" in client.get(f"/run/{run.name}").get_data(as_text=True)


def _link_directory(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            pytest.skip("cannot create a symlink")
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, check=False)
    if not link.exists():
        pytest.skip("cannot create a symlink or junction")


@pytest.mark.parametrize("reserved", ["data", "iterations"])
def test_a_recorded_asset_folder_linked_to_a_reserved_root_falls_back_to_assets(tmp_path: Path, reserved: str) -> None:
    client, run = _build_run(tmp_path, "assets")
    _link_directory(run / "media", run / reserved)
    _patch_result(run, asset_directory="media")
    assert asset_folder(run) == "assets"
    assert client.get(f"/raw/{run.name}/media/about/original.html").status_code == 404
    assert client.get(f"/raw/{run.name}/assets/hero.png").status_code == 200


def test_a_recorded_asset_folder_resolving_to_a_reserved_root_by_another_spelling_falls_back(tmp_path: Path, monkeypatch) -> None:
    client, run = _build_run(tmp_path, "assets")
    _patch_result(run, asset_directory="ITERAT~1")
    real_resolve = Path.resolve
    monkeypatch.setattr(Path, "resolve", lambda self, *args, **kwargs: real_resolve(run, *args, **kwargs) / "Iterations" if self.name == "ITERAT~1" else real_resolve(self, *args, **kwargs))
    assert asset_folder(run) == "assets"


@pytest.mark.parametrize("slug", ["index", "about-us", "page-2", "a.b", "v1.2", "Contact", "console", "communication", "com10", "lpt0", "com", "aux-x"])
def test_ordinary_slugs_are_accepted(slug: str) -> None:
    assert is_slug(slug)


@pytest.mark.parametrize("slug", ["con", "nul", "prn", "aux", "CON", "Nul", "COM1", "com9", "LPT1", "lpt9", "aux.x", "NUL.tar.gz", "com1.html", "about.", "x..", "a.b."])
def test_windows_device_names_and_a_trailing_dot_are_not_slugs(slug: str) -> None:
    assert not is_slug(slug)


def test_a_device_named_page_directory_is_not_listed_and_its_url_is_rejected(tmp_path: Path) -> None:
    client, run = _build_run(tmp_path, "assets")
    (run / "data" / "aux").mkdir()
    assert page_slugs(run, read_json(run / "result.json")) == ["about"]
    assert client.get(f"/raw/{run.name}/final/aux").status_code == 404


def test_a_json_file_over_the_size_cap_is_unusable(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "big.json"
    path.write_text(json.dumps({"a": "x" * 100}), encoding="utf-8")
    assert read_json(path) == {"a": "x" * 100}
    monkeypatch.setattr(viewer_data, "MAX_JSON_BYTES", 50)
    assert read_json(path) is None
    monkeypatch.setattr(viewer_data, "MAX_JSON_BYTES", path.stat().st_size)
    assert read_json(path) == {"a": "x" * 100}


def test_the_json_size_cap_is_twenty_megabytes() -> None:
    assert viewer_data.MAX_JSON_BYTES == 20 * 1024 * 1024


def test_an_oversized_result_makes_the_run_unusable_without_breaking_the_pages(tmp_path: Path, monkeypatch) -> None:
    client, run = _build_run(tmp_path, "assets")
    monkeypatch.setattr(viewer_data, "MAX_JSON_BYTES", 10)
    assert read_json(run / "result.json") is None
    assert asset_folder(run) == "assets"
    assert client.get("/").status_code == 200
    assert client.get(f"/run/{run.name}").status_code == 200


def test_the_iteration_page_lists_only_artifacts_the_json_route_can_serve(tmp_path: Path) -> None:
    client, run = _build_run(tmp_path, "assets")
    data_dir = run / "iterations" / "iter1" / "data" / "about"
    (data_dir / "design.json").write_text(json.dumps({"iteration": 1}), encoding="utf-8")
    (data_dir / "validation.json").write_text("[1, 2]", encoding="utf-8")
    (data_dir / "evaluation.json").write_text("{not json", encoding="utf-8")
    (data_dir / "browser.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    iteration = load_run(run.parent, run.name).iterations[0]
    assert iteration_artifacts(iteration) == ["design", "browser"]
    body = client.get(f"/run/{run.name}/iteration/1").get_data(as_text=True)
    for artifact, status in (("design", 200), ("browser", 200), ("validation", 404), ("evaluation", 404)):
        assert (f"/json/{run.name}/iteration/1/{artifact}" in body) == (status == 200), artifact
        assert client.get(f"/json/{run.name}/iteration/1/{artifact}").status_code == status, artifact


def test_a_well_formed_iteration_lists_every_artifact_it_has(tmp_path: Path) -> None:
    client, run = _build_run(tmp_path, "assets")
    data_dir = run / "iterations" / "iter1" / "data" / "about"
    for filename in ("design.json", "validation.json", "evaluation.json", "browser.json"):
        (data_dir / filename).write_text(json.dumps({"ok": True}), encoding="utf-8")
    assert iteration_artifacts(load_run(run.parent, run.name).iterations[0]) == ["design", "validation", "evaluation", "browser"]


@pytest.mark.parametrize("name", ["bad name", "페이지", "a;b"])
def test_a_page_directory_whose_name_is_not_a_slug_is_not_listed(tmp_path: Path, name: str) -> None:
    client, run = _build_run(tmp_path, "assets")
    try:
        (run / "data" / name).mkdir()
    except OSError:
        pytest.skip("cannot create the directory")
    assert page_slugs(run, read_json(run / "result.json")) == ["about"]
    assert load_run(run.parent, run.name, name).slug == "about"
    assert client.get("/").status_code == 200
    body = client.get(f"/run/{run.name}").get_data(as_text=True)
    assert name not in body


def test_a_page_directory_with_a_lone_surrogate_name_does_not_break_the_pages(tmp_path: Path) -> None:
    client, run = _build_run(tmp_path, "assets")
    try:
        os.mkdir(os.fsencode(run / "data") + b"/" + "bad\ud800name".encode("utf-8", "surrogatepass"))
        (run / "data" / "bad\ud800name").stat()
    except (OSError, UnicodeError, ValueError):
        pytest.skip("the OS cannot hold a lone-surrogate directory name")
    assert page_slugs(run, read_json(run / "result.json")) == ["about"]
    assert client.get("/").status_code == 200
    assert client.get(f"/run/{run.name}").status_code == 200
