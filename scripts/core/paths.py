"""路径边界合同(Task 1):相对路径规范化 + 目标落地约束。

所有备份恢复/候选落地要写真实文件系统的地方都必须经过这里;
绝不允许用 resolve() 把越界输入"洗白"成合法路径,只能原样拒绝。
"""
import os
from pathlib import Path


class PathScopeError(ValueError):
    """路径不符合相对路径规范,或落地位置越出登记根目录。"""


def validate_relative_path(value):
    """校验并返回规范路径组件元组。

    拒绝:非字符串、空路径、绝对路径、反斜杠、控制字符、空组件、`.`、`..`。
    """
    if not isinstance(value, str) or not value or value.startswith("/"):
        raise PathScopeError("路径必须是非空相对路径: {!r}".format(str(value)[:80]))
    if "\\" in value:
        raise PathScopeError("路径不得包含反斜杠")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise PathScopeError("路径含控制字符")
    parts = tuple(value.split("/"))
    if any(p in ("", ".", "..") for p in parts):
        raise PathScopeError("路径组件不得为空、. 或 ..: {!r}".format(value[:80]))
    return parts


def confined_destination(root, relative) -> Path:
    """返回 root 下的落地目标;逐级检查中间父目录,拒绝符号链接父目录与越界归属。"""
    root_abs = os.path.abspath(os.fspath(root))
    if not os.path.isdir(root_abs):
        raise PathScopeError("登记根目录不存在或不是目录")
    real_root = os.path.realpath(root_abs)
    parts = validate_relative_path(relative)
    current = root_abs
    for part in parts[:-1]:
        current = os.path.join(current, part)
        if os.path.islink(current):
            raise PathScopeError("中间父目录是符号链接,拒绝落地: " + part)
        if os.path.lexists(current) and not os.path.isdir(current):
            raise PathScopeError("中间父路径被非目录占用: " + part)
    dest = os.path.join(root_abs, *parts)
    parent = os.path.dirname(dest)
    if os.path.isdir(parent) and not os.path.islink(parent):
        real_parent = os.path.realpath(parent)
        if real_parent != real_root and not real_parent.startswith(real_root + os.sep):
            raise PathScopeError("实际父目录越出登记根目录")
    return Path(dest)
