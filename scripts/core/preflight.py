"""apply 前真实目标预检(Task 4):在目标同目录实际验证将要发生的文件系统操作。

设计(设计稿 §6):不建持久 capability snapshot、不维护 OS 支持表 —— 每次 apply
针对当前目标、当前文件系统重新验证,网络盘、权限变化和陈旧结论都无从藏身。
预检只用工具自有的唯一临时对象并全部清理,目标实体从头到尾不被触碰;
任何一步失败立即清理并拒绝(apply 在改动目标前中止)。

验证的正是执行引擎真实使用的原语(transactions/changes):
1. 创建唯一临时目录与文件,写入内容并 fsync(证明可写、可落盘);
2. 同目录 os.rename 改名(删除=改名入保管、更新=同目录交换,全部是同目录 rename);
3. 文件级 os.replace 覆盖(备份归档发布用同一原语);
4. 父目录 fsync(平台不支持时跳过,与 transactions.fsync_dir 同口径);
5. 清理全部临时对象;清理不干净同样拒绝,绝不在 skill 目录里留垃圾。
"""
import os
import shutil

PREFLIGHT_PREFIX = ".sk-preflight-"


class PreflightError(RuntimeError):
    """目标目录无法安全执行变更;apply 必须在改动任何目标前拒绝。"""


def _unique_base(directory, plan_id):
    token = os.urandom(6).hex()
    return os.path.join(str(directory), "{}{}-{}-{}".format(
        PREFLIGHT_PREFIX, str(plan_id)[-8:], os.getpid(), token))


def _fsync_dir(path):
    """父目录 fsync;不支持目录句柄的平台(Windows)自动跳过。"""
    try:
        fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _cleanup(paths):
    """尽力清理临时对象;返回仍残留的名字列表。"""
    leftover = []
    for p in paths:
        try:
            if os.path.isdir(p) and not os.path.islink(p):
                shutil.rmtree(p, ignore_errors=True)
            else:
                os.remove(p)
        except OSError:
            pass
        if os.path.lexists(p):
            leftover.append(os.path.basename(p))
    return leftover


def preflight_target_directory(directory, plan_id) -> None:
    """在 directory(目标实体的同目录)做最小真实预检;失败抛 PreflightError。"""
    directory = os.fspath(directory)
    if not os.path.isdir(directory):
        raise PreflightError("目标父目录不存在或不是目录")
    base = _unique_base(directory, plan_id)
    tmp_dir_a, tmp_dir_b = base + "-d1", base + "-d2"
    tmp_file_a, tmp_file_b, tmp_file_c = base + "-f1", base + "-f2", base + "-f3"
    try:
        # 目录原语:创建 → 写入并 fsync → 同目录 rename
        os.mkdir(tmp_dir_a)
        marker = os.path.join(tmp_dir_a, "marker.txt")
        with open(marker, "w", encoding="utf-8") as f:
            f.write("skill-keeper preflight")
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp_dir_a, tmp_dir_b)
        # 文件原语:创建 → fsync → rename → replace(覆盖)
        for name in (tmp_file_a, tmp_file_c):
            with open(name, "w", encoding="utf-8") as f:
                f.write("x")
                f.flush()
                os.fsync(f.fileno())
        os.rename(tmp_file_a, tmp_file_b)
        os.replace(tmp_file_c, tmp_file_b)
        _fsync_dir(directory)
    except OSError as e:
        detail = e.strerror or str(e)
        raise PreflightError(
            "目标同目录无法完成创建/rename/fsync 预检({}: {}),已在改动目标前拒绝".format(
                type(e).__name__, detail)) from e
    finally:
        leftover = _cleanup([tmp_file_b, tmp_file_a, tmp_file_c, tmp_dir_b, tmp_dir_a])
    if leftover:
        raise PreflightError(
            "预检临时对象清理失败({}),已在改动目标前拒绝".format(", ".join(leftover)))
