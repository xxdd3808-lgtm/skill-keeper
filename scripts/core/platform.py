"""最小跨平台底座(Task 2):锁后端选择与原生绝对路径判断。

只处理当前代码会实际阻塞的边界(设计 §6),不做 OS 能力表、不 proliferate 平台分支:

- 锁:POSIX 用 fcntl.flock,Windows 用 msvcrt.locking;都是标准库,延迟导入,
  非阻塞语义一致 —— 忙时抛 BlockingIOError,进程退出(含异常退出)由 OS 释放;
  破锁恢复归事务状态管,本模块绝不删除/清空锁文件。
- 路径:is_absolute_path 走当前系统原生规则(os.path.isabs:POSIX 认 `/`,
  Windows 认 drive 与 UNC),只用于本机位置登记;归档内部路径仍是 POSIX `/`,
  由 paths.validate_archive_member_path 严格校验 —— 备份/恢复绝不能调用本函数。
"""
import errno
import os

IS_WINDOWS = os.name == "nt"

_BUSY_ERRNOS = (errno.EACCES, errno.EAGAIN, getattr(errno, "EDEADLOCK", 0))


def lock_backend_name() -> str:
    """当前实际可用的锁后端名:fcntl / msvcrt / none。"""
    try:
        import fcntl  # noqa: F401
        return "fcntl"
    except ImportError:
        pass
    try:
        import msvcrt  # noqa: F401
        return "msvcrt"
    except ImportError:
        return "none"


def try_lock_exclusive(fd) -> None:
    """对已打开的 fd 尝试非阻塞独占锁;忙时抛 BlockingIOError,其余 OSError 原样上抛。"""
    if IS_WINDOWS:
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as e:
            if e.errno in _BUSY_ERRNOS:
                raise BlockingIOError(str(e)) from e
            raise
    else:
        import fcntl
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            if e.errno in _BUSY_ERRNOS:
                raise BlockingIOError(str(e)) from e
            raise


def unlock_fd(fd) -> None:
    """释放 try_lock_exclusive 建立的锁(锁区从位置 0 起 1 字节 / 整文件)。"""
    if IS_WINDOWS:
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)


def is_absolute_path(path) -> bool:
    """当前系统原生的绝对路径判断(Windows 认 drive/UNC,POSIX 认 `/`)。

    只用于本机位置登记(scan 的 client-locations / 位置声明);归档成员路径
    校验走 paths.validate_archive_member_path,不得用本函数替代。
    """
    return os.path.isabs(os.fspath(path))
