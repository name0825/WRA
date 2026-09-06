import json
from pathlib import Path

from wra.core.output import OutputManager, reroot_assets


def test_saves_each_page_of_each_iteration(tmp_path: Path) -> None:
    manager = OutputManager(tmp_path, "all")
    manager.save_iteration(iteration=1, slug="index", html="<html>index 1</html>", design_spec={"a": 1}, validation={"valid": True}, evaluation={"overall_score": 7.0}, score=7.0)
    manager.save_iteration(iteration=1, slug="about", html="<html>about 1</html>", evaluation={"overall_score": 6.0}, score=6.0)
    manager.save_iteration(iteration=2, slug="index", html="<html>index 2</html>", evaluation={"overall_score": 9.0}, score=9.0)

    assert (tmp_path / "iterations" / "iter1" / "index.html").read_text(encoding="utf-8") == "<html>index 1</html>"
    assert (tmp_path / "iterations" / "iter1" / "about.html").is_file()
    assert (tmp_path / "iterations" / "iter2" / "index.html").is_file()
    assert (tmp_path / "iterations" / "iter1" / "data" / "index" / "design.json").is_file()
    assert (tmp_path / "iterations" / "iter1" / "data" / "index" / "validation.json").is_file()
    assert (tmp_path / "iterations" / "iter1" / "data" / "about" / "evaluation.json").is_file()


def test_finalize_writes_the_best_iteration_per_page(tmp_path: Path) -> None:
    manager = OutputManager(tmp_path, "all")
    manager.save_iteration(iteration=1, slug="index", html="<html>first</html>", score=7.0)
    manager.save_iteration(iteration=2, slug="index", html="<html>best</html>", score=9.0)
    manager.save_iteration(iteration=3, slug="index", html="<html>worse</html>", score=8.0)

    final = manager.finalize("index")
    assert final == tmp_path / "index.html"
    assert final.read_text(encoding="utf-8") == "<html>best</html>"

    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert result["pages"]["index"] == {"best_iteration": 2, "best_score": 9.0}


def test_best_mode_writes_no_iteration_tree(tmp_path: Path) -> None:
    manager = OutputManager(tmp_path, "best")
    manager.save_iteration(iteration=1, slug="index", html="<html>only</html>", score=5.0)
    manager.finalize("index")

    assert not (tmp_path / "iterations").exists()
    assert (tmp_path / "index.html").read_text(encoding="utf-8") == "<html>only</html>"


def test_the_page_order_is_on_disk_before_anything_is_redesigned(tmp_path: Path) -> None:
    """A reader must not have to guess the entry page from alphabetical order."""
    manager = OutputManager(tmp_path, "all")
    manager.register_pages(["index", "about"])

    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert list(result["pages"]) == ["index", "about"]
    assert result["entry"] == "index"
    assert result["pages"]["index"] == {"best_iteration": None, "best_score": None}


def test_the_registered_order_survives_finalizing(tmp_path: Path) -> None:
    manager = OutputManager(tmp_path, "all")
    manager.register_pages(["index", "about"])
    manager.save_iteration(iteration=1, slug="about", html="<html>about</html>", score=6.0)
    manager.save_iteration(iteration=1, slug="index", html="<html>index</html>", score=8.0)
    manager.finalize("about")
    manager.finalize("index")

    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert list(result["pages"]) == ["index", "about"]
    assert result["entry"] == "index"
    assert result["best_score"] == 8.0


def test_iteration_copies_reach_the_shared_assets(tmp_path: Path) -> None:
    """iterations/iter1/index.html sits two levels below the run's assets/."""
    manager = OutputManager(tmp_path, "all")
    html = '<img src="assets/hero.png"><div style="background:url(assets/bg.jpg)"><a href="about.html">about</a>'
    manager.save_iteration(iteration=1, slug="index", html=html, score=1.0)
    saved = (tmp_path / "iterations" / "iter1" / "index.html").read_text(encoding="utf-8")

    assert 'src="../../assets/hero.png"' in saved
    assert "url(../../assets/bg.jpg)" in saved
    # A sibling page sits right beside it, so cross-page links stay as they are.
    assert 'href="about.html"' in saved


def test_the_final_page_keeps_its_plain_asset_paths(tmp_path: Path) -> None:
    manager = OutputManager(tmp_path, "all")
    manager.save_iteration(iteration=1, slug="index", html='<img src="assets/hero.png">', score=1.0)
    manager.finalize("index")
    assert 'src="assets/hero.png"' in (tmp_path / "index.html").read_text(encoding="utf-8")


def test_reroot_handles_srcset_entries() -> None:
    html = '<img srcset="assets/a.png 1x, assets/b.png 2x, https://cdn/x.png 3x">'
    rerooted = reroot_assets(html, "../../")
    assert "../../assets/a.png 1x" in rerooted
    assert "../../assets/b.png 2x" in rerooted
    assert "https://cdn/x.png 3x" in rerooted


def test_reroot_with_no_prefix_is_a_no_op() -> None:
    html = '<img src="assets/hero.png">'
    assert reroot_assets(html, "") == html


# --- a killed run keeps what it had already earned ---------------------------

def test_a_run_that_never_reaches_finalize_still_has_its_best(tmp_path: Path) -> None:
    """Termination is normally off, so a run ends by being stopped -- sometimes
    by a kill that never unwinds. What was scored must already be on disk."""
    manager = OutputManager(tmp_path, "all")
    manager.register_pages(["index"])
    manager.save_iteration(iteration=1, slug="index", html="<html>first</html>", score=7.0)
    manager.save_iteration(iteration=2, slug="index", html="<html>best</html>", score=9.0)
    # No finalize(): the process is gone.

    assert (tmp_path / "index.html").read_text(encoding="utf-8") == "<html>best</html>"
    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert result["pages"]["index"] == {"best_iteration": 2, "best_score": 9.0}


def test_a_worse_iteration_does_not_overwrite_the_published_page(tmp_path: Path) -> None:
    manager = OutputManager(tmp_path, "all")
    manager.save_iteration(iteration=1, slug="index", html="<html>best</html>", score=9.0)
    manager.save_iteration(iteration=2, slug="index", html="<html>worse</html>", score=8.0)

    assert (tmp_path / "index.html").read_text(encoding="utf-8") == "<html>best</html>"


def test_an_unscored_iteration_does_not_overwrite_the_published_page(tmp_path: Path) -> None:
    """An iteration whose HTML failed validation is never evaluated, so it
    arrives with no score. It must not become the best: the run root would end
    up holding a page the loop itself rejected."""
    manager = OutputManager(tmp_path, "all")
    manager.save_iteration(iteration=1, slug="index", html="<html>best</html>", score=9.0)
    manager.save_iteration(iteration=2, slug="index", html="<broken", score=None)

    assert (tmp_path / "index.html").read_text(encoding="utf-8") == "<html>best</html>"
    assert manager.finalize("index").read_text(encoding="utf-8") == "<html>best</html>"
    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert result["pages"]["index"] == {"best_iteration": 1, "best_score": 9.0}


def test_the_first_iteration_is_published_even_unscored(tmp_path: Path) -> None:
    manager = OutputManager(tmp_path, "all")
    manager.save_iteration(iteration=1, slug="index", html="<html>only</html>", score=None)

    assert (tmp_path / "index.html").read_text(encoding="utf-8") == "<html>only</html>"


def test_every_page_is_published_as_it_is_scored(tmp_path: Path) -> None:
    manager = OutputManager(tmp_path, "all")
    manager.register_pages(["index", "about"])
    manager.save_iteration(iteration=1, slug="index", html="<html>i</html>", score=7.0)
    manager.save_iteration(iteration=1, slug="about", html="<html>a</html>", score=6.0)

    assert (tmp_path / "index.html").is_file() and (tmp_path / "about.html").is_file()
    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert result["pages"]["about"]["best_score"] == 6.0
