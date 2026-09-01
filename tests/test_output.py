import json
from pathlib import Path

from wra.core.output import OutputManager


def test_output_manager_all_saves_iteration_artifacts(tmp_path: Path) -> None:
    manager = OutputManager(tmp_path, "all")

    saved = manager.save_iteration(
        iteration=1,
        html="<html></html>",
        design_spec={"concept": "test"},
        validation={"valid": True},
    )
    final_path = manager.finalize()

    assert saved.html_path is not None
    assert saved.html_path.read_text(encoding="utf-8") == "<html></html>"
    assert saved.html_path.parent.name == "iteration_001"
    assert (saved.html_path.parent / "design.json").exists()
    assert (saved.html_path.parent / "validation.json").exists()
    assert final_path.read_text(encoding="utf-8") == "<html></html>"


def test_output_manager_best_only_keeps_final_result(tmp_path: Path) -> None:
    manager = OutputManager(tmp_path, "best")

    first = manager.save_iteration(1, "<html>first</html>", score=7.0)
    second = manager.save_iteration(2, "<html>second</html>", score=9.0)
    manager.save_iteration(3, "<html>third</html>", score=8.0)
    final_path = manager.finalize()

    assert first.html_path is None
    assert second.html_path is None
    assert final_path.read_text(encoding="utf-8") == "<html>second</html>"

    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert result["best_iteration"] == 2
    assert result["best_score"] == 9.0
