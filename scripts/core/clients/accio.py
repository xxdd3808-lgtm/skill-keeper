"""Accio Work:动态发现 ~/.accio/accounts/*/skills、官方缓存与插件目录。

账号目录名只用于生成安全标签(纯数字编号一律哈希),绝不输出原始账号值;
安装/远端清单只按字段白名单 name/id/official/version/oss 读取。
"""
import json
from pathlib import Path

from ..models import Location
from .base import ClientAdapter, account_label

INSTALLED_FIELDS = ("name", "id", "official", "version", "oss")


def accio_installed_entries(account_dir: Path):
    """读取账号安装/远端清单,只返回白名单字段;缺失/损坏返回 []。"""
    path = Path(account_dir) / "installed.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if isinstance(data, dict):
        data = data.get("skills", [])
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if isinstance(item, dict):
            out.append({k: item[k] for k in INSTALLED_FIELDS if k in item})
    return out


class AccioAdapter(ClientAdapter):
    name = "accio"

    def discover(self, home: Path, data_dir: Path):
        accounts = Path(home) / ".accio/accounts"
        if not accounts.is_dir():
            return []
        rows = []
        for account_dir in sorted(accounts.iterdir()):
            if not account_dir.is_dir():
                continue
            label = account_label(account_dir.name)
            skills = account_dir / "skills"
            if skills.is_dir():
                rows.append(Location(
                    f"accio-account-{label}", "accio", str(skills), "user", True,
                    ("accio-accounts-dir",)))
            official = account_dir / "official-cache"
            if official.is_dir():
                rows.append(Location(
                    f"accio-account-{label}-official", "accio", str(official), "builtin",
                    False, ("accio-official-cache",)))
            # installed.json 安装清单不生成 Location,由 provenance 阶段按白名单消费
        plugins = Path(home) / ".accio/plugins/cache"
        if plugins.is_dir():
            rows.append(Location("accio-plugin-cache", "accio", str(plugins), "plugin-cache",
                                 False, ("accio-plugin-cache-layout",)))
        return rows
