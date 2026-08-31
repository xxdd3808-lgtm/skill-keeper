"""客户端适配器接口与发现注册表。

每个适配器只负责"发现位置":返回 Location 列表,不读取任何敏感配置字段;
marketplace 商品目录只是来源线索,绝不能因为目录存在就当作已安装。
"""
import hashlib
import os
import re
from pathlib import Path

from ..models import Location

WORKSPACE_CLIENT_PREFIX = "workspace-"


class ClientAdapter:
    name = ""

    def discover(self, home: Path, data_dir: Path):
        raise NotImplementedError


def dedupe_locations(rows):
    """按 (client, kind, 真实路径) 去重 + 按 location_id 去重;保留先出现者。"""
    seen_physical = set()
    seen_ids = set()
    out = []
    for row in rows:
        phys = (row.client, row.kind, os.path.realpath(row.path))
        if phys in seen_physical or row.location_id in seen_ids:
            continue
        seen_physical.add(phys)
        seen_ids.add(row.location_id)
        out.append(row)
    return out


def discover_locations(home, data_dir):
    """扫描虚构/真实 HOME 下所有受支持客户端的 skill 位置,返回按 ID 排序的 Location 列表。"""
    home, data_dir = Path(home), Path(data_dir)
    rows = [row for adapter in ADAPTERS for row in adapter.discover(home, data_dir)]
    return sorted(dedupe_locations(rows), key=lambda x: x.location_id)


def discover_skill_roots(location):
    """一个 Location 下直接容纳 skill 子目录的根目录列表。

    user/workspace/builtin 位置本身就是 skill 根;插件缓存按各客户端布局展开。
    """
    if location.kind in ("user", "workspace", "builtin"):
        return [Path(location.path)]
    root = Path(location.path)
    if not root.is_dir():
        return []
    nested = location.client in ("zcode", "claude-code", "accio")  # 市场/插件/版本/skills
    pattern = "*/*/*/skills" if nested else "*/*/skills"
    return sorted(p for p in root.glob(pattern) if p.is_dir())


def hashed_token(value: str, length: int = 10) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:length]


def account_label(account: str) -> str:
    """账号目录名的安全标签:正常短名称原样转小写;纯数字/过长/怪字符一律哈希,
    保证真实账号编号不会出现在 location_id 或报告里。"""
    label = re.sub(r"[^a-z0-9-]+", "-", str(account).lower()).strip("-")
    if not label or label.isdigit() or len(label) > 24:
        label = hashed_token(account)
    return label


# 底部导入避免循环依赖;ADAPTERS 顺序即发现顺序(dedupe 保留先出现者)
from .accio import AccioAdapter          # noqa: E402
from .cindy import CindyAdapter          # noqa: E402
from .codex import CodexAdapter          # noqa: E402
from .common import CommonAdapter, client_load_aliases  # noqa: E402,F401
from .workbuddy import WorkBuddyAdapter  # noqa: E402
from .zcode import ZCodeAdapter          # noqa: E402

ADAPTERS = (CommonAdapter(), ZCodeAdapter(), CodexAdapter(),
            AccioAdapter(), WorkBuddyAdapter(), CindyAdapter())
