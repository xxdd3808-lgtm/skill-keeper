"""Codex 客户端:个人 skill、系统自带 skill(.system,不可变)与插件缓存。

2026-08-25 起的 Codex 桌面版还会自动导入外部 Agent 技能库 ~/.agents/skills(共享库),
该位置由 common 适配器发现,加载拓扑在 scan.py 的客户端加载模型里体现。
"""
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
        # 系统技能随版本放在 skills/.system(当前)或 .system/skills(旧布局),两处都认
        for system in (home / ".codex/skills/.system", home / ".codex/.system/skills"):
            if system.is_dir():
                rows.append(Location("codex-system", "codex", str(system), "builtin", False,
                                     ("codex-system-dir",)))
                break
        cache = home / ".codex/plugins/cache"
        if cache.is_dir():
            rows.append(Location("codex-plugin-cache", "codex", str(cache), "plugin-cache",
                                 False, ("codex-plugin-cache-layout",)))
        return rows
