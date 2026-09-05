"""skill-keeper v2 安全状态 I/O:原子 JSON 写入、白名单字段读取、结构化损坏报告、进程间文件锁。

铁律:任何客户端配置读取都走 read_json_fields 白名单,未知字段(可能含 token/key/cookie)
既不返回值,也不进入错误信息。
"""
import errno
import json
import os
import tempfile
import time
from contextlib import contextmanager
from typing import Optional, Tuple

from . import platform as platform_tools

# 客户端配置中的敏感字段名(小写比较);这些字段的值永远不读取、不返回、不进错误信息
SECRET_FIELD_NAMES = {
    "token", "tokens", "api_key", "apikey", "secret", "secrets", "cookie",
    "cookies", "authorization", "auth", "env", "envs", "password", "passwd",
    "access_token", "refresh_token", "session", "private_key", "credentials",
}

# JSON 状态文件损坏时返回的结构化 issue 类型
ISSUE_CORRUPT = "corrupt-json"
ISSUE_NOT_FOUND = "missing-file"


def is_secret_field(name) -> bool:
    try:
        return str(name).strip().lower() in SECRET_FIELD_NAMES
    except Exception:
        return False


def redact_secrets(value):
    """递归剔除敏感字段的值(保留键名占位),用于任何要输出/落盘的结构化数据。"""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if is_secret_field(k):
                out[k] = "[REDACTED]"
            else:
                out[k] = redact_secrets(v)
        return out
    if isinstance(value, list):
        return [redact_secrets(x) for x in value]
    return value


def replace_atomic(tmp, path, attempts=5, delay=0.05):
    """os.replace 原子改名;Windows 上对刚落盘文件的瞬时占用(Defender/索引器
    WinError 5)做短退避重试,其余错误立即上抛。"""
    last = None
    for attempt in range(attempts):
        try:
            os.replace(tmp, path)
            return
        except PermissionError as e:
            last = e
            time.sleep(delay * (attempt + 1))
    raise last


def atomic_write_json(path, value) -> None:
    """同目录临时文件 → flush + fsync → os.replace 原子落盘;异常时清理临时文件。"""
    path = os.fspath(path)
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=1, sort_keys=False)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        replace_atomic(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_json_checked(path, default):
    """读 JSON,返回 (value, issues)。损坏/缺失产生结构化 issue,不静默吞掉、不抛裸异常。"""
    try:
        with open(os.fspath(path), "r", encoding="utf-8") as f:
            return json.load(f), []
    except FileNotFoundError:
        return default, [{"code": ISSUE_NOT_FOUND, "path": os.path.basename(os.fspath(path))}]
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        return default, [{"code": ISSUE_CORRUPT, "path": os.path.basename(os.fspath(path)),
                          "reason": type(e).__name__}]


def read_json_fields(path, allowed):
    """只返回 allowed 集合内的顶层字段;文件缺失/损坏/非对象都返回 {},绝不泄露其他字段。"""
    value, _ = load_json_checked(path, {})
    if not isinstance(value, dict):
        return {}
    out = {}
    for key in allowed:
        if key in value:
            out[key] = value[key]
    return out


class FileLock:
    """互斥文件锁(Task 2 起 POSIX/Windows 双后端,见 core/platform.py)。

    非阻塞获取:拿不到立刻抛 BlockingIOError,防止两个窗口同时变更;
    进程正常/异常退出时 OS 自动释放 fd 锁,锁文件本身绝不静默清除
    (中断恢复由事务状态负责,见 core/transactions.py)。
    """

    def __init__(self, path):
        self.path = os.fspath(path)
        self._fd: Optional[int] = None

    def acquire(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)) or ".", exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            platform_tools.try_lock_exclusive(fd)
        except BlockingIOError as e:
            os.close(fd)
            raise BlockingIOError("另一个 skill-keeper 变更正在进行,请稍后再试") from e
        except BaseException:
            os.close(fd)
            raise
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, str(os.getpid()).encode())
        except BaseException:
            try:
                platform_tools.unlock_fd(fd)
            except OSError:
                pass
            os.close(fd)
            raise
        self._fd = fd
        return self

    def release(self):
        if self._fd is not None:
            try:
                platform_tools.unlock_fd(self._fd)
            finally:
                os.close(self._fd)
                self._fd = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()
        return False


@contextmanager
def change_lock(lock_path):
    """变更互斥锁的便捷封装:拿不到锁直接抛 BlockingIOError。"""
    lock = FileLock(lock_path)
    lock.acquire()
    try:
        yield lock
    finally:
        lock.release()
