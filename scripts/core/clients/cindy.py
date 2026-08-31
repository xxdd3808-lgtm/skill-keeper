"""Cindy:~/Library/Application Support/Cindy 下的 codex-home、系统投影与插件投影。

系统/插件投影为只读;同一实体若真实路径相同,由 dedupe_locations 去重。
"""
import os
from pathlib import Path

from ..models import Location
from .base import ClientAdapter, hashed_token


def cindy_base(home: Path) -> Path:
    return Path(home) / "Library/Application Support/Cindy"


class CindyAdapter(ClientAdapter):
    name = "cindy"

    def discover(self, home: Path, data_dir: Path):
        base = cindy_base(home)
        if not base.is_dir():
            return []
        rows = []
        codex_home_skills = base / "codex-home/skills"
        if codex_home_skills.is_dir():
            rows.append(Location("cindy-codex-home", "cindy", str(codex_home_skills), "user",
                                 False, ("cindy-codex-home-projection",)))
        system = base / "codex-home/.system/skills"
        if system.is_dir():
            rows.append(Location("cindy-system", "cindy", str(system), "builtin", False,
                                 ("cindy-system-projection",)))
        cache = base / "plugins/cache"
        if cache.is_dir():
            rows.append(Location("cindy-plugin-cache", "cindy", str(cache), "plugin-cache",
                                 False, ("cindy-plugin-projection",)))
        return rows
