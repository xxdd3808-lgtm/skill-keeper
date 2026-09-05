"""模型位置声明解析(Task 3):大模型只提供"客户端名 + 本机 Skill 根目录"。

这是位置声明(location declaration),不是资产清单:声明不包含 Skill 名称、
正文、来源台账或账号,也不上传。本模块只做纯文本校验 —— 绝不打开声明中的
任何 Skill 文件,只有后续现有扫描器按根目录读取。

安全合同:
- 字段白名单:schema_version / client / observed_by / complete / roots[
  path / scope / load_state];任何其他键(mutable、instance_id、tree_hash、
  command、url、token、env……)一律拒绝;
- 限额:总输入 ≤ 64 KiB、roots ≤ 32、字符串 ≤ 4 KiB、JSON 嵌套 ≤ 6 层、UTF-8;
- load_state 只允许 "reported":模型只能"客户端自报",永远不能自证 confirmed;
- 解析错误绝不回显字段值或白名单外键名(可能含秘密),只回显结构位置;
- 声明默认只读、只用于本次扫描、不单独持久化,永远不能产生变更计划
  (mutable 只能由用户本地 client-locations.json 登记)。
"""
import json
import re

from .platform import expand_user_path, is_absolute_path

MAX_DECL_BYTES = 64 * 1024
MAX_ROOTS = 32
MAX_STRING_CHARS = 4096
MAX_DEPTH = 6

CLIENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")
ALLOWED_SCOPES = ("user", "workspace")
ALLOWED_LOAD_STATES = ("reported",)
OBSERVED_BY_VALUES = ("model",)


class LocationInputError(ValueError):
    """位置声明不合法;错误信息只含键名/位置,绝不含字段值。"""


def _depth(value, current=0):
    if current > MAX_DEPTH:
        raise LocationInputError("声明嵌套超过 {} 层".format(MAX_DEPTH))
    if isinstance(value, dict):
        for k, v in value.items():
            _depth(v, current + 1)
    elif isinstance(value, list):
        for v in value:
            _depth(v, current + 1)


def _string_limited(value, where):
    if not isinstance(value, str):
        raise LocationInputError("{} 必须是字符串".format(where))
    if len(value) > MAX_STRING_CHARS:
        raise LocationInputError("{} 超过 {} 字符上限".format(where, MAX_STRING_CHARS))
    return value


def _decode(text):
    """统一解码为 str 并执行总大小限制;非 UTF-8 / 超限直接拒绝。"""
    if isinstance(text, bytes):
        if len(text) > MAX_DECL_BYTES:
            raise LocationInputError("声明超过 {} 字节上限".format(MAX_DECL_BYTES))
        try:
            text = text.decode("utf-8")
        except UnicodeDecodeError:
            raise LocationInputError("声明必须是 UTF-8 文本")
    else:
        text = str(text)
        if len(text.encode("utf-8", "ignore")) > MAX_DECL_BYTES:
            raise LocationInputError("声明超过 {} 字节上限".format(MAX_DECL_BYTES))
    return text


def _reject_duplicate_keys(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise LocationInputError("声明含重复字段")
        out[key] = value
    return out


def parse_declaration(text):
    """解析并规范化一份位置声明 JSON;返回 normalized dict;任何问题抛 LocationInputError。"""
    text = _decode(text)
    if not text.strip():
        raise LocationInputError("声明为空")
    try:
        value = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except RecursionError:
        raise LocationInputError("声明嵌套超过 {} 层".format(MAX_DEPTH))
    except json.JSONDecodeError as e:
        raise LocationInputError("声明不是合法 JSON(位置 {})".format(e.pos))
    if not isinstance(value, dict):
        raise LocationInputError("声明顶层必须是对象")
    _depth(value)

    unknown = sorted(set(value) - {"schema_version", "client", "observed_by",
                                   "complete", "roots"})
    if unknown:
        raise LocationInputError("声明含白名单外字段")

    version = value.get("schema_version")
    if version != 1 or isinstance(version, bool):
        raise LocationInputError("schema_version 必须是 1")

    client = _string_limited(value.get("client"), "client")
    if not client or not CLIENT_RE.fullmatch(client):
        raise LocationInputError("client 只允许字母数字与 ._-")

    observed_by = value.get("observed_by", "model")
    observed_by = _string_limited(observed_by, "observed_by")
    if observed_by not in OBSERVED_BY_VALUES:
        raise LocationInputError("observed_by 只允许: " + "/".join(OBSERVED_BY_VALUES))

    complete = value.get("complete", False)
    if not isinstance(complete, bool):
        raise LocationInputError("complete 必须是布尔")

    roots_raw = value.get("roots")
    if not isinstance(roots_raw, list):
        raise LocationInputError("roots 必须是数组")
    if len(roots_raw) > MAX_ROOTS:
        raise LocationInputError("roots 超过 {} 个上限".format(MAX_ROOTS))

    roots = []
    for i, root in enumerate(roots_raw):
        if not isinstance(root, dict):
            raise LocationInputError("roots[{}] 必须是对象".format(i))
        unknown = sorted(set(root) - {"path", "scope", "load_state"})
        if unknown:
            raise LocationInputError("roots[{}] 含白名单外字段".format(i))
        path = _string_limited(root.get("path"), "roots[{}].path".format(i))
        if not path:
            raise LocationInputError("roots[{}].path 不能为空".format(i))
        if "\x00" in path:
            raise LocationInputError("roots[{}].path 含非法字符".format(i))
        path = expand_path(path)
        if not is_absolute_path(path):
            raise LocationInputError(
                "roots[{}].path 展开后必须是当前系统绝对路径".format(i))
        scope = root.get("scope", "user")
        scope = _string_limited(scope, "roots[{}].scope".format(i))
        if scope not in ALLOWED_SCOPES:
            raise LocationInputError("roots[{}].scope 只允许: {}".format(
                i, "/".join(ALLOWED_SCOPES)))
        load_state = root.get("load_state", "reported")
        load_state = _string_limited(load_state, "roots[{}].load_state".format(i))
        if load_state not in ALLOWED_LOAD_STATES:
            raise LocationInputError("roots[{}].load_state 只允许: {}".format(
                i, "/".join(ALLOWED_LOAD_STATES)))
        roots.append({"path": path, "scope": scope, "load_state": load_state})

    return {"schema_version": 1, "client": client, "observed_by": observed_by,
            "complete": complete, "roots": roots}


def expand_path(path):
    """声明路径的 ~ 展开;绝不触碰文件系统(不存在性由扫描器稍后判定)。"""
    return expand_user_path(path)


def parse_cli_roots(pairs):
    """解析 --root CLIENT=PATH 参数列表;返回 [{client, path, scope, load_state}]。

    与位置声明同一限额与白名单;scope/load_state 固定为 user/reported
    (命令行单根就是"客户端自报该目录")。
    """
    if len(pairs) > MAX_ROOTS:
        raise LocationInputError("--root 超过 {} 个上限".format(MAX_ROOTS))
    roots = []
    for i, pair in enumerate(pairs):
        pair = _string_limited(pair, "--root[{}]".format(i))
        if "=" not in pair:
            raise LocationInputError("--root[{}] 必须是 CLIENT=PATH 形式".format(i))
        client, path = pair.split("=", 1)
        client = client.strip()
        if not client or not CLIENT_RE.fullmatch(client):
            raise LocationInputError("--root[{}] 客户端名只允许字母数字与 ._-".format(i))
        if "\x00" in path:
            raise LocationInputError("--root[{}] 路径含非法字符".format(i))
        path = expand_path(path.strip())
        if not path or not is_absolute_path(path):
            raise LocationInputError("--root[{}] 展开后必须是当前系统绝对路径".format(i))
        roots.append({"client": client, "path": path, "scope": "user",
                      "load_state": "reported"})
    return roots
