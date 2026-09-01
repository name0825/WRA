from pathlib import Path

from wra.cli import main


def test_cli_creates_feature_model(tmp_path: Path, monkeypatch) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    (input_dir / "input.html").write_text(
        "<html><head><title>Test</title></head><body><h1>Hello</h1></body></html>",
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("wra.cli.INPUT_PATH", input_dir / "input.html")
    monkeypatch.setattr("wra.cli.OUTPUT_ROOT", output_dir)

    main()

    feature_files = list(output_dir.glob("run_*/features.json"))
    assert len(feature_files) == 1
    assert '"title": "Test"' in feature_files[0].read_text(encoding="utf-8")
    assert '"text": "Hello"' in feature_files[0].read_text(encoding="utf-8")
