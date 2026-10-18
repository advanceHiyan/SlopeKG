"""Cross-process recognition lock shared by all routes."""
from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
from typing import Callable, Any
from .storage import ensure_dir

class PipelineAlreadyRunningError(RuntimeError):
    pass



def single_pipeline_run(method: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    def guarded(self: "DemoPipeline", *args: Any, **kwargs: Any) -> dict[str, Any]:
        ensure_dir(self.paths.output_dir)
        with pipeline_run_guard(self.paths.output_dir / ".pipeline.lock"):
            return method(self, *args, **kwargs)

    return guarded



@contextmanager
def pipeline_run_guard(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"pid": os.getpid(), "started_at": datetime.now().isoformat(timespec="seconds")}
    descriptor: int | None = None
    for attempt in range(2):
        try:
            descriptor = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(descriptor, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            break
        except FileExistsError:
            owner = read_pipeline_lock(lock_path)
            owner_pid = owner.get("pid")
            if attempt == 0 and owner_pid and not process_is_running(int(owner_pid)):
                lock_path.unlink(missing_ok=True)
                continue
            started_at = owner.get("started_at") or "未知时间"
            raise PipelineAlreadyRunningError(
                f"已有PDF解析任务正在运行（PID {owner_pid or '未知'}，开始于 {started_at}）。"
                "请等待前端进度完成，不要同时从命令行重复启动流水线。"
            )
    if descriptor is None:
        raise PipelineAlreadyRunningError("无法取得PDF解析任务锁。")
    try:
        yield
    finally:
        os.close(descriptor)
        current = read_pipeline_lock(lock_path)
        if current.get("pid") == os.getpid():
            lock_path.unlink(missing_ok=True)



def read_pipeline_lock(lock_path: Path) -> dict[str, Any]:
    try:
        return json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}



def process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # On POSIX, signal 0 probes a process without changing it. On Windows,
        # os.kill(pid, 0) may call TerminateProcess instead, so use the native
        # read-only process query API.
        import ctypes

        process_query_limited_information = 0x1000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        # Access denied also proves that the process exists.
        return ctypes.get_last_error() == 5
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
