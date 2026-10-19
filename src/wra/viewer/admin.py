"""The admin console: logs, config, security and the pipeline process.

Everything here is gated by :mod:`wra.viewer.security`; the result-viewing
routes in :mod:`wra.viewer.server` are untouched and stay open.
"""

from pathlib import Path
from urllib.parse import quote, urlsplit

from flask import Blueprint, jsonify, redirect, render_template_string, request, send_from_directory

from wra.core.config import CONFIG_DIR, load_raw
from wra.core.models import InputConfig, LoggingConfig, ViewerConfig
from wra.viewer import inputs as inputsmod
from wra.viewer import logs as logsmod
from wra.viewer import security, settings
from wra.viewer.admin_templates import (ADMIN_CSS, CONFIG_TEMPLATE, DENIED_TEMPLATE, HOME_TEMPLATE, INPUT_LIST_TEMPLATE,
    INPUT_VIEW_TEMPLATE, LOG_LIST_TEMPLATE, LOG_VIEW_TEMPLATE, LOGIN_TEMPLATE, PROCESS_TEMPLATE, SECURITY_TEMPLATE)
from wra.viewer.data import list_runs
from wra.viewer.process import ProcessManager
from wra.viewer.templates import BASE_CSS

IDLE = {"state": "idle", "started_at": None, "pid": None, "returncode": None, "log_path": None}


def log_directory() -> Path:
    """Where the pipeline writes its logs, per the config the pipeline reads."""
    raw = load_raw(required=()).get("logging") or {}
    return Path(LoggingConfig.model_validate(raw).directory)


def input_settings() -> InputConfig:
    """The input block of the config the pipeline reads."""
    return InputConfig.model_validate(load_raw(required=()).get("input") or {})


def input_directory() -> Path:
    return Path(input_settings().directory)


def process_view(manager: ProcessManager) -> dict:
    current = manager.current
    if current is None:
        return dict(IDLE)
    return {"state": current.state, "started_at": current.started_at.strftime("%Y-%m-%d %H:%M:%S"),
        "pid": current.pid, "returncode": current.returncode, "log_path": str(current.log_path)}


def create_admin_blueprint(config: ViewerConfig, output_root: Path) -> Blueprint:
    blueprint = Blueprint("admin", __name__, url_prefix="/admin")
    log_dir = log_directory()
    input_dir = input_directory()
    manager = ProcessManager(log_dir, config.admin.run_command or None)
    throttle = security.LoginThrottle()
    css = BASE_CSS + ADMIN_CSS

    def page(template: str, active: str, **context):
        return render_template_string(template, css=css, active=active, title=context.pop("title", "WRA 어드민 콘솔"),
            subtitle=context.pop("subtitle", ""),
            login_required=bool(security.admin_password(config)), message=context.pop("message", None) or request.args.get("message"),
            error=context.pop("error", None) or request.args.get("error"), **context)

    @blueprint.before_request
    def guard():
        # Loopback access needs no login, so a page in the user's browser could
        # otherwise post to the console on their behalf. Every state change must
        # come from the console's own origin.
        if request.method == "POST" and not _same_origin():
            return render_template_string(DENIED_TEMPLATE, css=css, reason="다른 출처에서 온 요청은 처리하지 않습니다."), 403
        if request.endpoint in {"admin.login", "admin.logout"}:
            return None
        access = security.check_access(config, request.remote_addr)
        if access.allowed:
            return None
        if access.needs_login:
            return redirect("/admin/login")
        return render_template_string(DENIED_TEMPLATE, css=css, reason=access.reason), 403

    @blueprint.route("/login", methods=["GET", "POST"])
    def login():
        if not security.admin_password(config):
            return redirect("/admin")
        error, status = None, 200
        if request.method == "POST":
            wait = throttle.retry_after(request.remote_addr)
            if wait:
                error, status = f"로그인 시도가 너무 잦습니다. {wait}초 뒤에 다시 시도하세요.", 429
            elif security.password_matches(config, request.form.get("password", "")):
                throttle.record_success(request.remote_addr)
                security.start_session(config)
                return redirect("/admin")
            else:
                throttle.record_failure(request.remote_addr)
                error, status = "비밀번호가 맞지 않습니다.", 401
        return render_template_string(LOGIN_TEMPLATE, css=css, error=error), status

    @blueprint.route("/logout", methods=["POST"])
    def logout():
        security.end_session()
        return redirect("/admin/login" if security.admin_password(config) else "/")

    @blueprint.route("/")
    def home():
        log_files = logsmod.list_logs(log_dir)
        pipeline = logsmod.safe_log_path(log_dir, "wra.log")
        return page(HOME_TEMPLATE, "home", subtitle="상태 요약", process=process_view(manager),
            run_count=len(list_runs(output_root)), output_root=output_root, logs=log_files, log_dir=log_dir,
            config_files=settings.list_config_files(), allow_config_edit=config.admin.allow_config_edit,
            input_count=len(inputsmod.list_files(input_dir)), input_dir=input_dir,
            warning_count=len(security.warnings(config)),
            recent=logsmod.read_tail(pipeline, 40) if pipeline else [])

    @blueprint.route("/process")
    def process_page():
        current = manager.current
        return page(PROCESS_TEMPLATE, "process", subtitle="파이프라인 실행", process=process_view(manager),
            command=" ".join(manager.command), allow_run=config.admin.allow_run,
            output=logsmod.read_tail(current.log_path, config.admin.log_tail_lines) if current else [])

    @blueprint.route("/process/start", methods=["POST"])
    def process_start():
        if not config.admin.allow_run:
            return redirect("/admin/process?error=실행이 잠겨 있습니다 (viewer.admin.allow_run).")
        try:
            manager.start()
        except (RuntimeError, OSError) as error:
            return redirect("/admin/process?error=" + quote(f"실행을 시작하지 못했습니다: {error}"))
        return redirect("/admin/process?message=파이프라인을 시작했습니다.")

    @blueprint.route("/process/stop", methods=["POST"])
    def process_stop():
        if not config.admin.allow_run:
            return redirect("/admin/process?error=실행이 잠겨 있습니다 (viewer.admin.allow_run).")
        stopped = manager.stop()
        return redirect("/admin/process?" + ("message=프로세스를 중지했습니다." if stopped else "error=실행 중인 프로세스가 없습니다."))

    @blueprint.route("/process/tail")
    def process_tail():
        current = manager.current
        output = logsmod.read_tail(current.log_path, config.admin.log_tail_lines) if current else []
        return jsonify({"state": process_view(manager)["state"], "output": output})

    @blueprint.route("/logs")
    def log_list():
        return page(LOG_LIST_TEMPLATE, "logs", subtitle="로그", logs=logsmod.list_logs(log_dir), log_dir=log_dir)

    @blueprint.route("/logs/<name>")
    def log_view(name: str):
        path = logsmod.safe_log_path(log_dir, name)
        if path is None:
            return render_template_string(DENIED_TEMPLATE, css=css, reason=f"로그 파일이 없습니다: {name}"), 404
        tail_lines = _int_arg("lines", config.admin.log_tail_lines, 10, 20000)
        level = request.args.get("level") or None
        if level not in logsmod.LEVELS:
            level = None
        query = request.args.get("q") or None
        lines = logsmod.filter_lines(logsmod.read_tail(path, tail_lines), level, query)
        return page(LOG_VIEW_TEMPLATE, "logs", subtitle=name, name=name, log_dir=log_dir,
            lines=[(line, logsmod.line_level(line)) for line in lines],
            tail_lines=tail_lines, level=level, query=query, levels=logsmod.LEVELS)

    @blueprint.route("/logs/<name>/download")
    def log_download(name: str):
        path = logsmod.safe_log_path(log_dir, name)
        if path is None:
            return render_template_string(DENIED_TEMPLATE, css=css, reason=f"로그 파일이 없습니다: {name}"), 404
        return send_from_directory(path.parent.resolve(), path.name, as_attachment=True)

    @blueprint.route("/input")
    def input_list():
        return page(INPUT_LIST_TEMPLATE, "input", subtitle="입력 파일", files=inputsmod.list_files(input_dir),
            input_dir=input_dir, allow_input_edit=config.admin.allow_input_edit)

    @blueprint.route("/input/view")
    def input_view():
        name = request.args.get("path", "")
        path = inputsmod.safe_file(input_dir, name)
        if path is None:
            return render_template_string(DENIED_TEMPLATE, css=css, reason=f"입력 파일이 없습니다: {name}"), 404
        item = inputsmod.describe(input_dir, path)
        text, truncated = inputsmod.read_text(path) if item.kind == "text" else (None, False)
        return page(INPUT_VIEW_TEMPLATE, "input", subtitle=item.relative, name=item.relative, file=item,
            text=text, truncated=truncated, allow_input_edit=config.admin.allow_input_edit,
            backup_dir=inputsmod.BACKUP_DIR)

    @blueprint.route("/input/save", methods=["POST"])
    def input_save():
        name = request.form.get("path", "")
        if not config.admin.allow_input_edit:
            return redirect("/admin/input?error=" + quote("입력 파일 편집이 잠겨 있습니다 (viewer.admin.allow_input_edit)."))
        try:
            backup = inputsmod.write_text(input_dir, name, request.form.get("text", ""))
        except (ValueError, OSError) as failure:
            return redirect(f"/admin/input/view?path={quote(name)}&error=" + quote(f"저장하지 못했습니다: {failure}"))
        return redirect(f"/admin/input/view?path={quote(name)}&message="
            + quote(f"{name} 저장 완료. 이전 내용은 {inputsmod.BACKUP_DIR}/{backup.name} 에 있습니다."))

    @blueprint.route("/input/raw")
    def input_raw():
        name = request.args.get("path", "")
        path = inputsmod.safe_file(input_dir, name)
        if path is None:
            return render_template_string(DENIED_TEMPLATE, css=css, reason=f"입력 파일이 없습니다: {name}"), 404
        # Only a raster image is handed back inline. Anything the browser would
        # run - an uploaded page, an SVG - must arrive as a download, or it
        # would execute inside the console's own origin.
        inline = inputsmod.describe(input_dir, path).kind == "image" and not request.args.get("download")
        response = send_from_directory(path.parent.resolve(), path.name, as_attachment=not inline)
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @blueprint.route("/input/upload", methods=["POST"])
    def input_upload():
        if not config.admin.allow_input_edit:
            return jsonify({"error": "입력 파일 편집이 잠겨 있습니다 (viewer.admin.allow_input_edit)."}), 403
        uploads = request.files.getlist("files")
        names = request.form.getlist("paths")
        if len(uploads) > inputsmod.MAX_UPLOAD_FILES:
            return jsonify({"error": f"한 번에 올릴 수 있는 파일 수를 넘었습니다 (최대 {inputsmod.MAX_UPLOAD_FILES}개)."}), 400
        payloads = [upload.read() for upload in uploads]
        incoming = sum(len(data) for data in payloads)
        if inputsmod.directory_size(input_dir) + incoming > inputsmod.MAX_INPUT_TOTAL_BYTES:
            limit_gib = inputsmod.MAX_INPUT_TOTAL_BYTES // (1024 ** 3)
            return jsonify({"error": f"입력 폴더 용량 제한을 넘었습니다 (최대 {limit_gib}GiB)."}), 400
        saved, failures = 0, []
        for index, upload in enumerate(uploads):
            relative = names[index] if index < len(names) else (upload.filename or "")
            try:
                inputsmod.save_upload(input_dir, relative or upload.filename or "", payloads[index])
            except (ValueError, OSError) as failure:
                failures.append(str(failure))
            else:
                saved += 1
        if not saved and failures:
            return jsonify({"error": " / ".join(failures[:5])}), 400
        message = f"{saved}개 파일을 저장했습니다."
        if failures:
            message += f" 건너뛴 파일 {len(failures)}개: " + " / ".join(failures[:5])
        return jsonify({"message": message, "saved": saved, "skipped": len(failures)})

    @blueprint.route("/input/delete", methods=["POST"])
    def input_delete():
        name = request.form.get("path", "")
        if not config.admin.allow_input_edit:
            return redirect("/admin/input?error=" + quote("입력 파일 편집이 잠겨 있습니다 (viewer.admin.allow_input_edit)."))
        try:
            removed = inputsmod.delete(input_dir, name)
        except OSError as failure:
            return redirect("/admin/input?error=" + quote(f"삭제하지 못했습니다: {failure}"))
        if not removed:
            return redirect("/admin/input?error=" + quote(f"입력 파일이 없습니다: {name}"))
        return redirect("/admin/input?message=" + quote(f"{name} 을(를) 지웠습니다."))

    @blueprint.route("/config", methods=["GET", "POST"])
    def config_page():
        name = (request.form.get("file") if request.method == "POST" else request.args.get("file")) or settings.EDITABLE[0]
        if name not in settings.EDITABLE:
            return render_template_string(DENIED_TEMPLATE, css=css, reason=f"편집할 수 없는 파일입니다: {name}"), 404
        text = settings.read_text(name)
        message = error = None
        if request.method == "POST":
            if not config.admin.allow_config_edit:
                error = "설정 편집이 잠겨 있습니다 (viewer.admin.allow_config_edit)."
            else:
                text = request.form.get("text", "")
                try:
                    if request.form.get("action") == "save":
                        backup = settings.save(name, text)
                        text = settings.read_text(name)
                        message = f"{name} 저장 완료. 이전 내용은 {backup.name} 에 있습니다. 실행 중인 프로세스에는 적용되지 않습니다."
                    else:
                        settings.validate(name, text)
                        message = f"{name} 검증 통과. 저장하지 않았습니다."
                except ValueError as failure:
                    error = str(failure)
        return page(CONFIG_TEMPLATE, "config", subtitle=f"{CONFIG_DIR.as_posix()}/{name}", name=name, text=text,
            config_files=settings.list_config_files(), allow_config_edit=config.admin.allow_config_edit,
            message=message, error=error)

    @blueprint.route("/security")
    def security_page():
        expires = security.session_expires_at()
        return page(SECURITY_TEMPLATE, "security", subtitle="보안", admin=config.admin, host=config.host, port=config.port,
            password_set=bool(security.admin_password(config)), password_source=security.PASSWORD_ENV if not security.password_in_config_file(config) else "config.yaml",
            debug=config.debug, remote_addr=request.remote_addr, warnings=security.warnings(config),
            session_expires=expires.strftime("%Y-%m-%d %H:%M") if expires else None)

    return blueprint


def _same_origin() -> bool:
    """True when the request was sent from a page the console itself served."""
    source = request.headers.get("Origin") or request.headers.get("Referer")
    if not source:
        # A form post from the console always carries one; a bare POST does not.
        return False
    return urlsplit(source).netloc == request.host


def _int_arg(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(request.args.get(name, default))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))
