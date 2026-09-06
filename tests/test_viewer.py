import json
from pathlib import Path

import pytest

from wra.core.models import ViewerConfig
from wra.core.output import OutputManager
from wra.viewer.data import load_run

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
