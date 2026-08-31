"""ZCode 客户端:用户 skill 目录与插件缓存(缓存只读)。"""
from pathlib import Path

from ..models import Location
from .base import ClientAdapter


class ZCodeAdapter(ClientAdapter):
    name = "zcode"

    def discover(self, home: Path, data_dir: Path):
        home = Path(home)
        rows = []
        user = home / ".zcode/skills"
        if user.is_dir():
            rows.append(Location("zcode-user", "zcode", str(user), "user", True,
                                 ("default-zcode-dir",)))
        cache = home / ".zcode/cli/plugins/cache"
        if cache.is_dir():
            rows.append(Location("zcode-plugin-cache", "zcode", str(cache), "plugin-cache",
                                 False, ("zcode-plugin-cache-layout",)))
        return rows
