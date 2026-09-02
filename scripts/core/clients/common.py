"""通用位置:共享 skill 库、Ego、工作区位置、Claude Code 用户/插件缓存与 Haha 包装器。

Haha 规则:存在启动器或 ~/.claude/cc-haha 配置时,只给 Claude 位置加 alias
(2026-09-02 按 Haha traces 核实:Haha 走标准 ~/.claude/skills 镜像,不直接读共享库),
绝不新建物理位置、绝不读取配置内容(env/token 一律不碰)。
"""
import os
from pathlib import Path

from ..models import Location
from .base import ClientAdapter, WORKSPACE_CLIENT_PREFIX, hashed_token


def haha_installed(home: Path) -> bool:
    """只判断 Haha 标志物是否存在,不做任何内容读取:
    ~/.claude/cc-haha 配置目录(应用启动后会重建),或已安装的 Claude Code Haha 应用
    (2026-09-02 用户卸载 Claude Code 只留 Haha 后,配置目录可能被一并清空)。
    系统 /Applications 只在扫描真实 HOME 时查看,避免污染测试的临时 HOME。"""
    marker = Path(home) / ".claude/cc-haha"
    if marker.is_dir() or marker.is_file():
        return True
    app = "Claude Code Haha.app"
    if (Path(home) / "Applications" / app).is_dir():
        return True
    try:
        same_home = str(Path(home).resolve()) == str(Path("~/").expanduser().resolve())
    except OSError:
        same_home = False
    return same_home and (Path("/Applications") / app).is_dir()


def client_load_aliases(home: Path):
    """底层客户端 → 复用它的包装客户端列表(加载拓扑,不算重复安装)。"""
    if haha_installed(home):
        return {"claude-code": ["haha"]}
    return {}


class CommonAdapter(ClientAdapter):
    name = "common"

    def discover(self, home: Path, data_dir: Path):
        home = Path(home)
        rows = []
        aliases = tuple(client_load_aliases(home).get("shared", []))
        rows.extend(self._default_dirs(home, aliases))
        rows.extend(self._workspace_dirs(Path(data_dir)))
        return rows

    def _default_dirs(self, home: Path, aliases):
        rows = []
        shared = home / ".agents/skills"
        if shared.is_dir():
            rows.append(Location("shared", "shared", str(shared), "user", True,
                                 ("default-shared-dir",), aliases))
        ego = home / ".local/share/ego/ego-skills"
        if ego.is_dir():
            rows.append(Location("ego-user", "ego", str(ego), "user", True,
                                 ("default-ego-dir",), aliases))
        claude = home / ".claude/skills"
        if claude.is_dir():
            haha_aliases = tuple(client_load_aliases(home).get("claude-code", []))
            rows.append(Location("claude-user", "claude-code", str(claude), "user", True,
                                 ("default-claude-dir",), haha_aliases))
        claude_cache = home / ".claude/plugins/cache"
        if claude_cache.is_dir():
            rows.append(Location("claude-plugin-cache", "claude-code", str(claude_cache),
                                 "plugin-cache", False, ("claude-plugin-cache-layout",)))
        return rows

    def _workspace_dirs(self, data_dir: Path):
        rows = []
        cfg = data_dir / "workspace-locations.txt"
        if not cfg.is_file():
            return rows
        for line in cfg.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            path = Path(os.path.expanduser(line))
            if not path.is_dir():
                continue
            client = (WORKSPACE_CLIENT_PREFIX + "claude" if ".claude" in path.parts
                      else WORKSPACE_CLIENT_PREFIX + "zcode")
            loc_id = "workspace-" + hashed_token(str(path), 8)
            rows.append(Location(loc_id, client, str(path), "workspace", True,
                                 ("workspace-locations.txt",)))
        return rows
