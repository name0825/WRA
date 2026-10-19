import pytest

from wra import cli


def test_main_exits_with_code_1_on_expected_failure(monkeypatch):
    def raise_value_error():
        raise ValueError("bad config")

    monkeypatch.setattr(cli, "load_config", raise_value_error)

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code == 1


def test_main_exits_with_code_1_on_unexpected_failure(monkeypatch):
    def raise_key_error():
        raise KeyError("unexpected")

    monkeypatch.setattr(cli, "load_config", raise_key_error)

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code == 1


def test_main_exits_with_code_130_on_keyboard_interrupt(monkeypatch):
    def raise_keyboard_interrupt():
        raise KeyboardInterrupt()

    monkeypatch.setattr(cli, "load_config", raise_keyboard_interrupt)

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code == 130


def _single_config(**single):
    from wra.core.models import WRAConfig
    from support import sample_config_data

    data = sample_config_data()
    data["models"] = {**data["models"], "single": {"enabled": True, "name": "pinned:26b", **single}}
    return WRAConfig.model_validate(data)


def test_visual_inspector_receives_single_model_num_ctx():
    inspector = cli.build_visual_inspector(_single_config(num_ctx=16384), object())
    assert inspector._num_ctx == 16384


def test_visual_inspector_keeps_default_num_ctx_without_single_pin():
    assert cli.build_visual_inspector(_single_config(num_ctx=None), object())._num_ctx == 32768
    config = _single_config(num_ctx=16384).model_copy(update={"models": _single_config().models.model_copy(update={"single": _single_config(enabled=False).models.single})})
    assert cli.build_visual_inspector(config, object())._num_ctx == 32768
