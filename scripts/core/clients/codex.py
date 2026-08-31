"""Codex 客户端:个人 skill、系统自带 skill(.system,不可变)与插件缓存。"""
from pathlib import Path

from ..models import Location
from .base import ClientAdapter


class CodexAdapter(ClientAdapter):
    name = "codex"

    def discover(self, home: Path, data_dir: Path):
        home = Path(home)
        rows = []
        user = home / ".codex/skills"
        if user.is_dir():
            rows.append(Location("codex-user", "codex", str(user), "user", True,
                                 ("default-codex-dir",)))
        system = home / ".codex/.system/skills"
        if system.is_dir():
            rows.append(Location("codex-system", "codex", str(system), "builtin", False,
                                 ("codex-system-dir",)))
        cache = home / ".codex/plugins/cache"
        if cache.is_dir():
            rows.append(Location("codex-plugin-cache", "codex", str(cache), "plugin-cache",
                                 False, ("codex-plugin-cache-layout",)))
        return rows
