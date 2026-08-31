"""WorkBuddy(用户称 Workbody):用户 skill、connector skill 与插件缓存是加载位置;
skills/connectors/插件 marketplace 只是商品目录,除非安装/启用清单证明,绝不计入已安装。
"""
from pathlib import Path

from ..models import Location
from .base import ClientAdapter


class WorkBuddyAdapter(ClientAdapter):
    name = "workbuddy"

    def discover(self, home: Path, data_dir: Path):
        root = Path(home) / ".workbuddy"
        if not root.is_dir():
            return []
        rows = []
        user = root / "skills"
        if user.is_dir():
            rows.append(Location("workbuddy-user", "workbuddy", str(user), "user", True,
                                 ("default-workbuddy-dir",)))
        connectors = root / "connectors/skills"
        if connectors.is_dir():
            rows.append(Location("workbuddy-connector", "workbuddy", str(connectors), "user",
                                 False, ("workbuddy-connector-managed",)))
        cache = root / "plugins/cache"
        if cache.is_dir():
            rows.append(Location("workbuddy-plugin-cache", "workbuddy", str(cache),
                                 "plugin-cache", False, ("workbuddy-plugin-cache-layout",)))
        # 注意:skills-marketplace / connectors-marketplace / plugins/marketplace 一律不发现
        return rows
