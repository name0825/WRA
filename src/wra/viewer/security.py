"""Access control for the admin console.

The viewer itself stays open: it only reads finished runs. The admin console
reads logs, rewrites config files and starts processes, so it is gated. Two
modes, decided by whether ``viewer.admin.password`` is set:

* no password - the console answers only to the loopback interface, which is
  the default bind anyway;
* password - a session login is required from every address.

The password itself is read from ``WRA_ADMIN_PASSWORD`` when that is set, and
from ``viewer.admin.password`` otherwise. The environment is the better place
for it: the config file is checked into the repository, copied around and shown
verbatim in the console's own editor, and a secret in it leaks by all three.
"""

import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from threading import Lock

from flask import session

from wra.core.models import ViewerConfig

LOOPBACK = {"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"}
SESSION_KEY = "wra_admin_until"
PASSWORD_ENV = "WRA_ADMIN_PASSWORD"


@dataclass(frozen=True)
class Access:
    """Why a request may or may not enter the console."""

    allowed: bool
    reason: str
    needs_login: bool = False


def is_loopback(remote_addr: str | None) -> bool:
    return remote_addr in LOOPBACK


def admin_password(config: ViewerConfig) -> str | None:
    """The password in force, or None when the console is loopback-only."""
    return os.environ.get(PASSWORD_ENV) or config.admin.password or None


def password_in_config_file(config: ViewerConfig) -> bool:
    """True when the password is sitting in a config file rather than the env."""
    return not os.environ.get(PASSWORD_ENV) and bool(config.admin.password)


def password_matches(config: ViewerConfig, given: str) -> bool:
    password = admin_password(config)
    if not password:
        return False
    return secrets.compare_digest(str(password).encode("utf-8"), given.encode("utf-8"))


def start_session(config: ViewerConfig) -> None:
    session[SESSION_KEY] = (datetime.now() + timedelta(hours=config.admin.session_hours)).isoformat()


def end_session() -> None:
    session.pop(SESSION_KEY, None)


def session_expires_at() -> datetime | None:
    raw = session.get(SESSION_KEY)
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def logged_in() -> bool:
    expires = session_expires_at()
    return expires is not None and expires > datetime.now()


@dataclass
class _Attempts:
    failures: int = 0
    blocked_until: datetime | None = None


@dataclass
class LoginThrottle:
    """Slows password guessing down, per client address.

    The console has one password and no account to lock, so the only thing
    between a weak password and a script is how fast guesses can be made. The
    first few misses are free - people mistype - and after that each one
    doubles the wait, up to a cap.
    """

    free_attempts: int = 5
    cap_seconds: int = 300
    _seen: dict[str, _Attempts] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def retry_after(self, remote_addr: str | None) -> int:
        """Seconds this address still has to wait; 0 when it may try now."""
        with self._lock:
            entry = self._seen.get(remote_addr or "")
            if entry is None or entry.blocked_until is None:
                return 0
            remaining = (entry.blocked_until - datetime.now()).total_seconds()
            return max(0, int(remaining + 0.999))

    def record_failure(self, remote_addr: str | None) -> None:
        with self._lock:
            entry = self._seen.setdefault(remote_addr or "", _Attempts())
            entry.failures += 1
            over = entry.failures - self.free_attempts
            if over > 0:
                entry.blocked_until = datetime.now() + timedelta(seconds=min(2 ** over, self.cap_seconds))

    def record_success(self, remote_addr: str | None) -> None:
        with self._lock:
            self._seen.pop(remote_addr or "", None)


def check_access(config: ViewerConfig, remote_addr: str | None) -> Access:
    # ``viewer.admin.enabled`` is not consulted here: the server does not mount
    # the console's blueprint at all when it is off, so a disabled console is a
    # 404 rather than a route that has to turn people away.
    if admin_password(config):
        if logged_in():
            return Access(True, "로그인됨")
        return Access(False, "로그인이 필요합니다.", needs_login=True)
    if is_loopback(remote_addr):
        return Access(True, "루프백 접속")
    return Access(False, "비밀번호가 설정되지 않아 원격 접속을 거부합니다 (viewer.admin.password).")


def warnings(config: ViewerConfig) -> list[str]:
    """Deployment risks worth naming on the security page."""
    items = []
    if config.debug:
        items.append("Flask 디버그 모드가 켜져 있습니다. 처리되지 않은 오류에서 대화형 파이썬 콘솔이 열립니다 (viewer.debug).")
    if not is_loopback(config.host):
        items.append(f"뷰어가 '{config.host}' 로 바인딩되어 루프백 밖에서도 접속됩니다 (viewer.host).")
    if not admin_password(config):
        items.append("어드민 비밀번호가 없습니다. 루프백 접속만 허용됩니다 (viewer.admin.password).")
    if password_in_config_file(config):
        items.append(f"비밀번호가 설정 파일에 평문으로 있습니다. 설정 편집 화면과 백업·저장소에 그대로 남습니다 ({PASSWORD_ENV} 환경변수를 쓰면 파일에서 빼낼 수 있습니다).")
    if config.admin.allow_config_edit:
        items.append("설정 파일 편집이 허용되어 있습니다 (viewer.admin.allow_config_edit).")
    if config.admin.allow_run:
        items.append("콘솔에서 파이프라인 실행이 허용되어 있습니다 (viewer.admin.allow_run).")
    if config.admin.allow_input_edit:
        items.append("입력 폴더에 파일을 올리고 지우는 것이 허용되어 있습니다 (viewer.admin.allow_input_edit).")
    return items
