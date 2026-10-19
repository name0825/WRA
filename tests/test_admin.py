import os
import signal
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from pydantic import ValidationError

from wra.core.models import AdminConfig, ViewerConfig

pytest.importorskip("flask")

from wra.viewer import admin, inputs, logs, security, settings  # noqa: E402
from wra.viewer.process import ProcessManager  # noqa: E402
from wra.viewer.server import create_app  # noqa: E402

LOOPBACK = {"REMOTE_ADDR": "127.0.0.1"}
SAME_ORIGIN = {"Origin": "http://localhost"}
REMOTE = {"REMOTE_ADDR": "10.0.0.9"}


@pytest.fixture
def console(tmp_path: Path, monkeypatch):
    def build(**admin_kwargs):
        log_dir = tmp_path / "log"
        log_dir.mkdir(exist_ok=True)
        (log_dir / "wra.log").write_text(
            "2026-01-01 00:00:00,000 | INFO | wra.pipeline | 시작\n"
            "2026-01-01 00:00:01,000 | ERROR | wra.generator | 실패\n"
            "Traceback (most recent call last):\n"
            "2026-01-01 00:00:02,000 | DEBUG | wra.llm | 프롬프트\n",
            encoding="utf-8")
        monkeypatch.setattr(admin, "log_directory", lambda: log_dir)
        input_dir = tmp_path / "input"
        input_dir.mkdir(exist_ok=True)
        (input_dir / "input.html").write_text("<html><body>원본</body></html>", encoding="utf-8")
        monkeypatch.setattr(admin, "input_directory", lambda: input_dir)
        config = ViewerConfig(output_root=str(tmp_path / "output"), admin=AdminConfig(**admin_kwargs))
        return create_app(config).test_client(), log_dir

    return build


def test_the_console_answers_the_loopback_interface(console) -> None:
    client, _ = console()
    assert client.get("/admin/", environ_overrides=LOOPBACK).status_code == 200


def test_without_a_password_a_remote_address_is_refused(console) -> None:
    client, _ = console()
    assert client.get("/admin/", environ_overrides=REMOTE).status_code == 403


def test_a_password_turns_every_address_into_a_login(console) -> None:
    client, _ = console(password="secret")
    assert client.get("/admin/", environ_overrides=LOOPBACK).status_code == 302
    assert client.post("/admin/login", data={"password": "wrong"}, environ_overrides=LOOPBACK, headers=SAME_ORIGIN).status_code == 401
    assert client.post("/admin/login", data={"password": "secret"}, environ_overrides=LOOPBACK, headers=SAME_ORIGIN).status_code == 302
    assert client.get("/admin/", environ_overrides=REMOTE).status_code == 200
    client.post("/admin/logout", environ_overrides=REMOTE, headers=SAME_ORIGIN)
    assert client.get("/admin/", environ_overrides=REMOTE).status_code == 302


def test_subroutes_redirect_to_login_when_not_authenticated(console) -> None:
    client, _ = console(password="secret")
    for path in ("/admin/config", "/admin/logs", "/admin/security", "/admin/process"):
        assert client.get(path, environ_overrides=LOOPBACK).status_code == 302
    response = client.post("/admin/process/start", environ_overrides=LOOPBACK, headers=SAME_ORIGIN)
    assert response.status_code == 302
    tail = client.get("/admin/process/tail", environ_overrides=LOOPBACK)
    assert tail.status_code == 302


def test_an_expired_session_is_treated_as_logged_out(console) -> None:
    client, _ = console(password="secret")
    client.post("/admin/login", data={"password": "secret"}, environ_overrides=LOOPBACK, headers=SAME_ORIGIN)
    assert client.get("/admin/", environ_overrides=LOOPBACK).status_code == 200
    with client.session_transaction() as flask_session:
        flask_session[security.SESSION_KEY] = (datetime.now() - timedelta(hours=1)).isoformat()
    assert client.get("/admin/", environ_overrides=LOOPBACK).status_code == 302


def test_a_post_from_another_origin_is_refused(console) -> None:
    client, _ = console()
    response = client.post("/admin/process/start", environ_overrides=LOOPBACK, headers={"Origin": "http://evil.example"})
    assert response.status_code == 403
    assert client.get("/admin/process/tail", environ_overrides=LOOPBACK).get_json()["state"] == "idle"


def test_a_disabled_console_is_not_mounted(tmp_path: Path) -> None:
    app = create_app(ViewerConfig(output_root=str(tmp_path), admin=AdminConfig(enabled=False)))
    assert app.test_client().get("/admin/", environ_overrides=LOOPBACK).status_code == 404


def test_logs_are_listed_and_read_from_the_end(console) -> None:
    client, _ = console()
    listing = client.get("/admin/logs", environ_overrides=LOOPBACK)
    assert b"wra.log" in listing.data
    body = client.get("/admin/logs/wra.log", environ_overrides=LOOPBACK).get_data(as_text=True)
    assert "실패" in body and "프롬프트" in body


def test_a_level_filter_keeps_the_traceback_with_its_own_line(console) -> None:
    client, _ = console()
    body = client.get("/admin/logs/wra.log?level=ERROR", environ_overrides=LOOPBACK).get_data(as_text=True)
    assert "실패" in body and "Traceback" in body and "프롬프트" not in body


def test_log_content_is_escaped_in_the_response(console) -> None:
    client, log_dir = console()
    with (log_dir / "wra.log").open("a", encoding="utf-8") as handle:
        handle.write("2026-01-01 00:00:03,000 | INFO | wra.pipeline | <script>alert(1)</script>\n")
    body = client.get("/admin/logs/wra.log", environ_overrides=LOOPBACK).get_data(as_text=True)
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body


def test_a_log_name_may_not_escape_the_log_directory(console) -> None:
    client, _ = console()
    assert client.get("/admin/logs/..%2Fconfig%2Fconfig.yaml", environ_overrides=LOOPBACK).status_code == 404
    assert client.get("/admin/logs/config.yaml", environ_overrides=LOOPBACK).status_code == 404


def test_a_drive_relative_log_name_is_refused(tmp_path: Path) -> None:
    assert logs.safe_log_path(tmp_path, "C:secrets.log") is None
    assert logs.safe_log_path(tmp_path, "../wra.log") is None


def test_filter_lines_drops_everything_below_the_threshold() -> None:
    lines = ["a | DEBUG | x | one", "b | ERROR | x | two", "  detail", "c | INFO | x | three"]
    assert logs.filter_lines(lines, "WARNING") == ["b | ERROR | x | two", "  detail"]


def test_read_tail_returns_only_the_last_lines(tmp_path: Path) -> None:
    path = tmp_path / "big.log"
    path.write_text("\n".join(f"line {index}" for index in range(5000)), encoding="utf-8")
    tail = logs.read_tail(path, 3)
    assert tail == ["line 4997", "line 4998", "line 4999"]


def test_an_invalid_config_edit_is_refused_before_it_is_written(tmp_path: Path, monkeypatch) -> None:
    _isolate_config(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        settings.save("config.yaml", "viewer: [not a mapping]\n")
    with pytest.raises(ValueError):
        settings.save("config.yaml", "viewer:\n  port: [1, 2\n")
    assert "port: 5002" in settings.read_text("config.yaml")


def test_viewer_admin_settings_cannot_be_changed_from_the_console(tmp_path: Path, monkeypatch) -> None:
    _isolate_config(tmp_path, monkeypatch)
    original = settings.read_text("config.yaml")
    with pytest.raises(ValueError):
        settings.save("config.yaml", original.replace("run_command: []", 'run_command: ["evil"]'))
    assert "run_command: []" in settings.read_text("config.yaml")
    # A change elsewhere in the same file is still allowed.
    backup = settings.save("config.yaml", original.replace("port: 5002", "port: 5010"))
    assert "port: 5010" in settings.read_text("config.yaml")
    assert backup.read_text(encoding="utf-8") == original


def test_viewer_debug_and_host_cannot_be_changed_from_the_console(tmp_path: Path, monkeypatch) -> None:
    _isolate_config(tmp_path, monkeypatch)
    original = settings.read_text("config.yaml")
    with pytest.raises(ValueError):
        settings.save("config.yaml", original.replace("debug: false", "debug: true"))
    assert "debug: false" in settings.read_text("config.yaml")
    with pytest.raises(ValueError):
        settings.save("config.yaml", original.replace('host: "127.0.0.1"', 'host: "0.0.0.0"'))
    assert 'host: "127.0.0.1"' in settings.read_text("config.yaml")


def test_another_file_may_not_wipe_the_viewer_block(tmp_path: Path, monkeypatch) -> None:
    from wra.core.config import load_raw
    _isolate_config(tmp_path, monkeypatch)
    original_models = settings.read_text("models.yaml")
    with pytest.raises(ValueError):
        settings.save("models.yaml", "viewer: {}\n" + original_models)
    assert settings.read_text("models.yaml") == original_models
    assert load_raw(required=())["viewer"]["port"] == 5002


def test_a_path_setting_pointed_at_the_config_directory_is_refused(tmp_path: Path, monkeypatch) -> None:
    _isolate_config(tmp_path, monkeypatch)
    original = settings.read_text("config.yaml")
    with pytest.raises(ValueError):
        settings.save("config.yaml", original.replace('directory: "input"', 'directory: "config"'))
    assert 'directory: "input"' in settings.read_text("config.yaml")


def test_a_path_setting_pointed_outside_the_repository_is_refused(tmp_path: Path, monkeypatch) -> None:
    _isolate_config(tmp_path, monkeypatch)
    original = settings.read_text("config.yaml")
    with pytest.raises(ValueError):
        settings.save("config.yaml", original.replace('directory: "input"', 'directory: "../elsewhere"'))
    assert 'directory: "input"' in settings.read_text("config.yaml")


def test_a_normal_in_repo_path_setting_is_still_accepted(tmp_path: Path, monkeypatch) -> None:
    _isolate_config(tmp_path, monkeypatch)
    original = settings.read_text("config.yaml")
    settings.save("config.yaml", original.replace('directory: "input"', 'directory: "input2"'))
    assert 'directory: "input2"' in settings.read_text("config.yaml")


def test_the_merged_viewer_comparison_runs_inside_the_save_lock(tmp_path: Path, monkeypatch) -> None:
    """Moved inside `_save_lock` so two concurrent saves cannot each validate
    against the pre-write disk state and both pass."""
    _isolate_config(tmp_path, monkeypatch)
    original = settings.read_text("config.yaml")
    real_merged_viewer_block = settings._merged_viewer_block
    lock_states = []

    def recording(overrides=None):
        lock_states.append(settings._save_lock.locked())
        return real_merged_viewer_block(overrides=overrides)

    monkeypatch.setattr(settings, "_merged_viewer_block", recording)
    settings.save("config.yaml", original.replace("port: 5002", "port: 5010"))
    assert lock_states and all(lock_states)


def test_saving_a_config_keeps_the_previous_text_as_a_backup(tmp_path: Path, monkeypatch) -> None:
    config_dir = _isolate_config(tmp_path, monkeypatch)
    original = settings.read_text("config.yaml")
    backup = settings.save("config.yaml", original.replace("port: 5002", "port: 5010"))
    assert "port: 5010" in settings.read_text("config.yaml")
    assert backup.read_text(encoding="utf-8") == original
    assert backup.parent == config_dir


def test_two_saves_in_the_same_second_keep_both_backups(tmp_path: Path, monkeypatch) -> None:
    _isolate_config(tmp_path, monkeypatch)
    original = settings.read_text("config.yaml")
    first = settings.save("config.yaml", original.replace("port: 5002", "port: 5010"))
    second = settings.save("config.yaml", original.replace("port: 5002", "port: 5011"))
    assert first != second
    assert first.read_text(encoding="utf-8") == original
    assert "port: 5010" in second.read_text(encoding="utf-8")


def test_the_console_can_start_and_stop_a_process(console) -> None:
    client, log_dir = console(allow_run=True, run_command=[sys.executable, "-c", "import time; time.sleep(30)"])
    assert client.post("/admin/process/start", environ_overrides=LOOPBACK, headers=SAME_ORIGIN).status_code == 302
    status = client.get("/admin/process/tail", environ_overrides=LOOPBACK).get_json()
    assert status["state"] == "running"
    assert client.post("/admin/process/stop", environ_overrides=LOOPBACK, headers=SAME_ORIGIN).status_code == 302
    assert client.get("/admin/process/tail", environ_overrides=LOOPBACK).get_json()["state"] == "stopped"


def test_a_locked_console_refuses_to_run_or_edit(console) -> None:
    client, _ = console(allow_run=False, allow_config_edit=False)
    client.post("/admin/process/start", environ_overrides=LOOPBACK, headers=SAME_ORIGIN)
    assert client.get("/admin/process/tail", environ_overrides=LOOPBACK).get_json()["state"] == "idle"
    response = client.post("/admin/config", data={"file": "config.yaml", "action": "save", "text": ""}, environ_overrides=LOOPBACK, headers=SAME_ORIGIN)
    assert "잠겨" in response.get_data(as_text=True)


def test_the_process_manager_records_what_the_child_printed(tmp_path: Path) -> None:
    manager = ProcessManager(tmp_path, [sys.executable, "-c", "print('hello from the child')"])
    process = manager.start()
    process.popen.wait(timeout=30)
    for _ in range(20):
        if logs.read_tail(process.log_path, 10):
            break
        time.sleep(0.05)
    assert "hello from the child" in "\n".join(logs.read_tail(process.log_path, 10))
    assert process.state == "finished"


def test_the_security_page_names_the_open_doors(console) -> None:
    client, _ = console()
    body = client.get("/admin/security", environ_overrides=LOOPBACK).get_data(as_text=True)
    assert "viewer.admin.password" in body and "루프백" in body


def test_only_the_newest_backups_are_kept(tmp_path: Path, monkeypatch) -> None:
    _isolate_config(tmp_path, monkeypatch)
    original = settings.read_text("config.yaml")
    kept = [settings.save("config.yaml", original.replace("port: 5002", f"port: {5010 + index}"))
        for index in range(settings.BACKUPS_KEPT + 3)]
    surviving = sorted((tmp_path / "config").glob("config.yaml.*.bak"))
    assert len(surviving) == settings.BACKUPS_KEPT
    assert surviving == sorted(kept[3:])
    assert not any(dropped.exists() for dropped in kept[:3])


def test_a_session_that_expires_at_once_is_refused_by_the_config(tmp_path: Path, monkeypatch) -> None:
    with pytest.raises(ValidationError):
        AdminConfig(session_hours=0)
    with pytest.raises(ValidationError):
        AdminConfig(log_tail_lines=-1)
    _isolate_config(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        settings.save("config.yaml", settings.read_text("config.yaml").replace("session_hours: 12", "session_hours: 0"))


def test_the_environment_password_wins_over_the_config_file(monkeypatch) -> None:
    config = ViewerConfig(admin=AdminConfig(password="from-file"))
    monkeypatch.setenv(security.PASSWORD_ENV, "from-env")
    assert security.admin_password(config) == "from-env"
    assert security.password_matches(config, "from-env")
    assert not security.password_matches(config, "from-file")
    assert not security.password_in_config_file(config)
    monkeypatch.delenv(security.PASSWORD_ENV)
    assert security.admin_password(config) == "from-file"
    assert security.password_in_config_file(config)


def test_a_password_only_in_the_environment_still_gates_the_console(console, monkeypatch) -> None:
    monkeypatch.setenv(security.PASSWORD_ENV, "from-env")
    client, _ = console()
    assert client.get("/admin/", environ_overrides=LOOPBACK).status_code == 302
    assert client.post("/admin/login", data={"password": "from-env"}, environ_overrides=LOOPBACK,
        headers=SAME_ORIGIN).status_code == 302
    assert client.get("/admin/", environ_overrides=REMOTE).status_code == 200


def test_a_password_kept_in_the_config_file_is_named_on_the_security_page(console, monkeypatch) -> None:
    monkeypatch.delenv(security.PASSWORD_ENV, raising=False)
    client, _ = console(password="secret")
    client.post("/admin/login", data={"password": "secret"}, environ_overrides=LOOPBACK, headers=SAME_ORIGIN)
    body = client.get("/admin/security", environ_overrides=LOOPBACK).get_data(as_text=True)
    assert "secret" not in body
    assert "평문" in body
    monkeypatch.setenv(security.PASSWORD_ENV, "from-env")
    assert "평문" not in client.get("/admin/security", environ_overrides=LOOPBACK).get_data(as_text=True)


def test_the_login_throttle_locks_an_address_after_its_free_attempts() -> None:
    throttle = security.LoginThrottle(free_attempts=2)
    for _ in range(2):
        assert throttle.retry_after("1.1.1.1") == 0
        throttle.record_failure("1.1.1.1")
    assert throttle.retry_after("1.1.1.1") == 0
    throttle.record_failure("1.1.1.1")
    assert throttle.retry_after("1.1.1.1") > 0
    assert throttle.retry_after("2.2.2.2") == 0
    throttle.record_success("1.1.1.1")
    assert throttle.retry_after("1.1.1.1") == 0


def test_the_login_route_throttles_repeated_wrong_passwords(console) -> None:
    client, _ = console(password="secret")
    for _ in range(6):
        response = client.post("/admin/login", data={"password": "wrong"}, environ_overrides=LOOPBACK, headers=SAME_ORIGIN)
        assert response.status_code == 401
    response = client.post("/admin/login", data={"password": "wrong"}, environ_overrides=LOOPBACK, headers=SAME_ORIGIN)
    assert response.status_code == 429


def _isolate_config(tmp_path: Path, monkeypatch) -> Path:
    """Point both config loaders at a copy, so a test never writes config/."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    for name in settings.EDITABLE:
        sample = Path("config") / f"{name}.sample"
        source = sample if sample.is_file() else Path("config") / name
        if source.is_file():
            (config_dir / name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(settings, "CONFIG_DIR", config_dir)
    monkeypatch.setattr("wra.core.config.CONFIG_DIR", config_dir)
    return config_dir


def upload(client, name: str, data: bytes = b"<html>x</html>", path: str | None = None):
    from io import BytesIO
    form = {"files": (BytesIO(data), name)}
    if path is not None:
        form["paths"] = path
    return client.post("/admin/input/upload", data=form, content_type="multipart/form-data",
        environ_overrides=LOOPBACK, headers=SAME_ORIGIN)


def test_the_input_page_lists_the_files_in_the_input_directory(console) -> None:
    client, _ = console()
    body = client.get("/admin/input", environ_overrides=LOOPBACK).get_data(as_text=True)
    assert "input.html" in body


def test_a_text_input_file_is_shown_inline(console) -> None:
    client, _ = console()
    body = client.get("/admin/input/view?path=input.html", environ_overrides=LOOPBACK).get_data(as_text=True)
    assert "원본" in body


def test_a_path_outside_the_input_directory_is_refused(console) -> None:
    client, _ = console()
    for target in ("../secret.html", "..%2Fsecret.html", "C:secret.html", "/etc/passwd"):
        assert client.get(f"/admin/input/view?path={target}", environ_overrides=LOOPBACK).status_code == 404
        assert client.get(f"/admin/input/raw?path={target}", environ_overrides=LOOPBACK).status_code == 404


def test_an_upload_lands_in_the_input_directory(console, tmp_path: Path) -> None:
    client, _ = console(allow_input_edit=True)
    response = upload(client, "page.html", b"<html>new</html>", "site/page.html")
    assert response.status_code == 200
    assert response.get_json()["saved"] == 1
    assert (tmp_path / "input" / "site" / "page.html").read_text(encoding="utf-8") == "<html>new</html>"


def test_an_upload_is_flattened_into_the_root_and_refused_by_suffix(console, tmp_path: Path) -> None:
    client, _ = console(allow_input_edit=True)
    assert upload(client, "evil.html", b"x", "../evil.html").status_code == 200
    assert not (tmp_path / "evil.html").exists()
    assert (tmp_path / "input" / "evil.html").exists()
    assert upload(client, "evil.exe", b"x", "evil.exe").status_code == 400
    assert not (tmp_path / "input" / "evil.exe").exists()


def test_deleting_an_input_file_removes_it_and_its_empty_folder(console, tmp_path: Path) -> None:
    client, _ = console()
    upload(client, "page.html", b"x", "site/page.html")
    response = client.post("/admin/input/delete", data={"path": "site/page.html"},
        environ_overrides=LOOPBACK, headers=SAME_ORIGIN)
    assert response.status_code == 302
    assert not (tmp_path / "input" / "site").exists()
    assert (tmp_path / "input" / "input.html").exists()


def test_a_directory_cleanup_failure_does_not_fail_the_delete(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "input"
    (root / "site").mkdir(parents=True)
    (root / "site" / "page.html").write_bytes(b"x")
    monkeypatch.setattr(Path, "rmdir", lambda self: (_ for _ in ()).throw(OSError("locked")))
    assert inputs.delete(root, "site/page.html") is True
    assert not (root / "site" / "page.html").exists()


def test_input_writes_are_refused_when_the_lock_is_on(console, tmp_path: Path) -> None:
    client, _ = console(allow_input_edit=False)
    assert upload(client, "page.html", b"x", "page.html").status_code == 403
    client.post("/admin/input/delete", data={"path": "input.html"}, environ_overrides=LOOPBACK, headers=SAME_ORIGIN)
    assert (tmp_path / "input" / "input.html").exists()
    assert client.get("/admin/input", environ_overrides=LOOPBACK).status_code == 200


def test_an_upload_batch_over_the_file_count_cap_is_refused(console, tmp_path: Path, monkeypatch) -> None:
    client, _ = console(allow_input_edit=True)
    monkeypatch.setattr(inputs, "MAX_UPLOAD_FILES", 2)
    from io import BytesIO
    form = {"files": [(BytesIO(b"x"), f"f{i}.html") for i in range(3)]}
    response = client.post("/admin/input/upload", data=form, content_type="multipart/form-data",
        environ_overrides=LOOPBACK, headers=SAME_ORIGIN)
    assert response.status_code == 400
    assert not (tmp_path / "input" / "f0.html").exists()


def test_an_upload_over_the_input_folder_size_cap_is_refused(console, tmp_path: Path, monkeypatch) -> None:
    client, _ = console(allow_input_edit=True)
    monkeypatch.setattr(inputs, "MAX_INPUT_TOTAL_BYTES", 10)
    response = upload(client, "big.html", b"x" * 20, "big.html")
    assert response.status_code == 400
    assert not (tmp_path / "input" / "big.html").exists()


def test_stopping_on_windows_kills_the_process_tree(monkeypatch, tmp_path: Path) -> None:
    from wra.viewer import process as processmod
    monkeypatch.setattr(processmod.sys, "platform", "win32")
    monkeypatch.setattr(processmod, "_interrupt", lambda popen: None)
    calls = []
    monkeypatch.setattr(processmod.subprocess, "run", lambda command, **kwargs: calls.append(command))
    manager = ProcessManager(tmp_path / "log", [sys.executable, "-c", "import time; time.sleep(30)"])
    manager.start()
    real_kill = manager._current.popen.kill
    monkeypatch.setattr(manager._current.popen, "wait",
        lambda timeout=None: (_ for _ in ()).throw(processmod.subprocess.TimeoutExpired("x", timeout)))
    try:
        manager.stop()
    finally:
        real_kill()
    assert calls and calls[0][0] == "taskkill"


def test_posix_kill_signals_the_whole_process_group(monkeypatch) -> None:
    from wra.viewer import process as processmod
    monkeypatch.setattr(processmod.sys, "platform", "linux")
    monkeypatch.setattr(signal, "SIGKILL", 9, raising=False)
    groups = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: groups.append((pgid, sig)), raising=False)
    killed = []
    popen = SimpleNamespace(pid=4242, kill=lambda: killed.append("kill"))
    processmod._kill(popen)
    assert groups == [(4242, getattr(signal, "SIGKILL", 9))]
    assert killed == []


def test_posix_kill_falls_back_to_the_child_when_the_group_is_gone(monkeypatch) -> None:
    from wra.viewer import process as processmod
    monkeypatch.setattr(processmod.sys, "platform", "linux")
    monkeypatch.setattr(signal, "SIGKILL", 9, raising=False)
    def group_is_gone(pgid, sig):
        raise ProcessLookupError
    monkeypatch.setattr(os, "killpg", group_is_gone, raising=False)
    killed = []
    popen = SimpleNamespace(pid=4242, kill=lambda: killed.append("kill"))
    processmod._kill(popen)
    assert killed == ["kill"]


def test_posix_interrupt_signals_the_whole_process_group(monkeypatch) -> None:
    from wra.viewer import process as processmod
    monkeypatch.setattr(processmod.sys, "platform", "linux")
    groups = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: groups.append((pgid, sig)), raising=False)
    terminated = []
    popen = SimpleNamespace(pid=4242, terminate=lambda: terminated.append("terminate"))
    processmod._interrupt(popen)
    assert groups == [(4242, signal.SIGTERM)]
    assert terminated == []


def test_posix_interrupt_falls_back_to_the_child_when_the_group_is_gone(monkeypatch) -> None:
    from wra.viewer import process as processmod
    monkeypatch.setattr(processmod.sys, "platform", "linux")
    def group_is_gone(pgid, sig):
        raise ProcessLookupError
    monkeypatch.setattr(os, "killpg", group_is_gone, raising=False)
    terminated = []
    popen = SimpleNamespace(pid=4242, terminate=lambda: terminated.append("terminate"))
    processmod._interrupt(popen)
    assert terminated == ["terminate"]


def test_an_input_upload_from_another_origin_is_refused(console) -> None:
    client, _ = console()
    from io import BytesIO
    response = client.post("/admin/input/upload", data={"files": (BytesIO(b"x"), "a.html")},
        content_type="multipart/form-data", environ_overrides=LOOPBACK, headers={"Origin": "http://evil.example"})
    assert response.status_code == 403


def test_a_clean_upload_path_keeps_folders_and_drops_the_rest(tmp_path: Path) -> None:
    assert inputs.clean_upload_path("site/assets/logo.png") == "site/assets/logo.png"
    assert inputs.clean_upload_path("site\\assets\\logo.png") == "site/assets/logo.png"
    assert inputs.clean_upload_path("../logo.png") == "logo.png"
    assert inputs.clean_upload_path("...") is None
    assert inputs.clean_upload_path("") is None


def test_only_a_raster_image_is_served_inline(console, tmp_path: Path) -> None:
    client, _ = console()
    (tmp_path / "input" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "input" / "card.svg").write_text("<svg><script>x</script></svg>", encoding="utf-8")
    png = client.get("/admin/input/raw?path=logo.png", environ_overrides=LOOPBACK)
    assert "attachment" not in png.headers.get("Content-Disposition", "")
    for name in ("card.svg", "input.html"):
        response = client.get(f"/admin/input/raw?path={name}", environ_overrides=LOOPBACK)
        assert response.headers["Content-Disposition"].startswith("attachment")


def test_a_quote_in_a_file_name_stays_out_of_the_page_script(console) -> None:
    client, _ = console(allow_input_edit=True)
    name = "x'); alert(1); z.html"
    assert upload(client, name, b"x", name).status_code == 200
    for url in ("/admin/input", "/admin/input/view?path=" + quote(name)):
        body = client.get(url, environ_overrides=LOOPBACK).get_data(as_text=True)
        # The name is data, never JS: it may not reach an inline handler, where
        # the browser would decode the entities back before the JS engine ran.
        assert "&#39;); alert(1); z.html" in body
        assert "onsubmit" not in body


def save_text(client, name: str, text: str):
    return client.post("/admin/input/save", data={"path": name, "text": text},
        environ_overrides=LOOPBACK, headers=SAME_ORIGIN)


def test_a_text_input_file_can_be_edited_in_place(console, tmp_path: Path) -> None:
    client, _ = console(allow_input_edit=True)
    assert save_text(client, "input.html", "<html>고침</html>\r\n").status_code == 302
    target = tmp_path / "input" / "input.html"
    assert target.read_bytes() == "<html>고침</html>\n".encode("utf-8")
    backups = list((tmp_path / "input" / inputs.BACKUP_DIR).glob("input.html.*.bak"))
    assert len(backups) == 1
    assert "원본" in backups[0].read_text(encoding="utf-8")


def test_a_backup_never_shows_up_as_an_input_file(console, tmp_path: Path) -> None:
    client, _ = console()
    save_text(client, "input.html", "<html>고침</html>")
    listed = [item.relative for item in inputs.list_files(tmp_path / "input")]
    assert listed == ["input.html"]


def test_editing_is_refused_for_the_lock_and_for_a_non_text_file(console, tmp_path: Path) -> None:
    client, _ = console(allow_input_edit=False)
    (tmp_path / "input" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    save_text(client, "input.html", "덮어쓰기")
    assert "원본" in (tmp_path / "input" / "input.html").read_text(encoding="utf-8")
    open_client, _ = console()
    save_text(open_client, "logo.png", "덮어쓰기")
    assert (tmp_path / "input" / "logo.png").read_bytes().startswith(b"\x89PNG")


def test_editing_may_not_reach_outside_the_input_directory(console, tmp_path: Path) -> None:
    client, _ = console()
    outside = tmp_path / "outside.html"
    outside.write_text("원본", encoding="utf-8")
    save_text(client, "../outside.html", "덮어쓰기")
    assert outside.read_text(encoding="utf-8") == "원본"


def test_a_truncated_file_is_shown_without_an_editor(console, tmp_path: Path) -> None:
    client, _ = console()
    (tmp_path / "input" / "big.html").write_text("x" * (inputs.MAX_TEXT_BYTES + 10), encoding="utf-8")
    body = client.get("/admin/input/view?path=big.html", environ_overrides=LOOPBACK).get_data(as_text=True)
    assert "/admin/input/save" not in body
    # And a save posted straight at the route may not drop what the editor never held.
    save_text(client, "big.html", "짧게")
    assert (tmp_path / "input" / "big.html").stat().st_size > inputs.MAX_TEXT_BYTES


def test_a_non_ascii_password_is_compared_without_error(monkeypatch) -> None:
    monkeypatch.delenv(security.PASSWORD_ENV, raising=False)
    config = ViewerConfig(admin=AdminConfig(password="관리자비밀번호1"))
    assert security.password_matches(config, "관리자비밀번호1")
    assert not security.password_matches(config, "관리자비밀번호2")
    assert not security.password_matches(config, "wrong")
    ascii_config = ViewerConfig(admin=AdminConfig(password="secret"))
    assert security.password_matches(ascii_config, "secret")
    assert not security.password_matches(ascii_config, "관리자")
    assert not security.password_matches(ascii_config, "secre")
