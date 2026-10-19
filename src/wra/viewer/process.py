"""Running the redesign pipeline from the admin console.

One run at a time, on purpose: a WRA run holds the Ollama server and the output
directory, and two concurrent runs would fight over both. The child's console
output goes to its own file under the log directory so the console can tail it
the same way it tails everything else.
"""

import io
import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Lock

DEFAULT_COMMAND = (sys.executable, "-m", "wra.cli")


@dataclass
class RunProcess:
    command: list[str]
    log_path: Path
    started_at: datetime
    popen: subprocess.Popen
    log_handle: io.TextIOBase | None = None
    stopped: bool = False

    @property
    def pid(self) -> int:
        return self.popen.pid

    @property
    def returncode(self) -> int | None:
        return self.popen.poll()

    @property
    def running(self) -> bool:
        return self.returncode is None

    def close_log(self) -> None:
        """Release our end of the output file; the child holds its own."""
        if self.log_handle is not None and not self.log_handle.closed:
            self.log_handle.close()

    @property
    def state(self) -> str:
        if self.running:
            return "running"
        if self.stopped:
            return "stopped"
        return "finished" if self.returncode == 0 else "failed"


class ProcessManager:
    """Owns the single pipeline child process the console may start."""

    def __init__(self, log_dir: Path, command: list[str] | None = None) -> None:
        self._log_dir = Path(log_dir)
        self._command = list(command) if command else list(DEFAULT_COMMAND)
        self._lock = Lock()
        self._current: RunProcess | None = None

    @property
    def command(self) -> list[str]:
        return list(self._command)

    @property
    def current(self) -> RunProcess | None:
        """The last run, with a finished child's output file closed first.

        Nothing here waits on the child, so an exited process is noticed on the
        next look at it - which is what every console page does.
        """
        current = self._current
        if current is not None and not current.running:
            current.close_log()
        return current

    @property
    def running(self) -> bool:
        return self._current is not None and self._current.running

    def start(self) -> RunProcess:
        with self._lock:
            if self.running:
                raise RuntimeError("이미 실행 중인 프로세스가 있습니다.")
            if self._current is not None:
                self._current.close_log()
            self._log_dir.mkdir(parents=True, exist_ok=True)
            log_path = self._log_dir / f"console_{datetime.now():%Y%m%d_%H%M%S}.log"
            handle = log_path.open("w", encoding="utf-8", buffering=1)
            # A child started from the console inherits the server's working
            # directory, which is where the relative config/, input/ and output/
            # paths are resolved from.
            popen = subprocess.Popen(self._command, stdout=handle, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace", env=_child_env(), **_platform_flags())
            self._current = RunProcess(list(self._command), log_path, datetime.now(), popen, handle)
            return self._current

    def stop(self) -> bool:
        """Ask the running child to exit. False when nothing was running."""
        with self._lock:
            current = self._current
            if current is None or not current.running:
                return False
            current.stopped = True
            # Ask first: a run holds the output directory, and killing it
            # outright leaves a half-written run behind.
            _interrupt(current.popen)
            try:
                current.popen.wait(timeout=10)
            except subprocess.TimeoutExpired:
                _kill(current.popen)
                try:
                    current.popen.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    # Unkillable child; the handle still has to go back.
                    pass
            finally:
                current.close_log()
            return True


def _kill(popen: subprocess.Popen) -> None:
    """Kill the child and everything it spawned.

    On POSIX the child leads its own process group (``start_new_session=True``),
    so the kill goes to the whole group. On Windows a plain kill leaves
    grandchildren running while the state says the process has stopped, so
    ``taskkill /T`` walks the tree instead.
    """
    if sys.platform == "win32":
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(popen.pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=10)
            return
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(popen.pid, signal.SIGKILL)
            return
        except OSError:
            pass
    popen.kill()


def _interrupt(popen: subprocess.Popen) -> None:
    if sys.platform == "win32":
        try:
            popen.send_signal(signal.CTRL_BREAK_EVENT)
            return
        except (OSError, ValueError):
            pass
    else:
        try:
            os.killpg(popen.pid, signal.SIGTERM)
            return
        except OSError:
            pass
    popen.terminate()


def _child_env() -> dict[str, str]:
    env = dict(os.environ)
    # The console reads the child's log file as UTF-8; a cp949 console would
    # otherwise mangle the Korean log lines on the way in.
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def _platform_flags() -> dict:
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}
