"""测试辅助:临时 HOME、skill fixture、多客户端目录搭建。

铁律:所有测试只用临时目录;fixture 里的 secret 全部是虚构值(FAKE-SECRET-000),
绝不写真实 token/key,也绝不指向真实用户目录。
"""
import json
import shutil
import tempfile
from pathlib import Path

# 虚构 secret(仅测试用;如果它出现在任何实现输出里就是泄漏 bug)
FAKE_SECRET = "FAKE-SECRET-000"


def write_skill(root, name, description="demo skill", version="1.0.0", body=""):
    """在 root 下创建 name/SKILL.md,返回 skill 目录。"""
    d = Path(root) / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        "---\nname: {}\ndescription: {}\nversion: {}\n---\n\n# {}\n\n{}\n".format(
            name, description, version, name, body),
        encoding="utf-8")
    return d


def temp_home(testcase, prefix="sk-home-"):
    td = tempfile.mkdtemp(prefix=prefix)
    testcase.addCleanup(shutil.rmtree, td, ignore_errors=True)
    return Path(td)


def make_plugin_cache(base, plugin, version, skill_name, nested=False, marketplace="official"):
    """搭一个插件缓存:base/<plugin>/<version>/skills/<skill>(nested 时多一层 marketplace)。"""
    parts = [base, marketplace, plugin] if nested else [base, plugin]
    d = Path(*[str(p) for p in parts]) / version / "skills"
    return write_skill(d, skill_name, description="plugin skill")


def build_multi_client_home(testcase):
    """搭建覆盖七类客户端的虚构 HOME,返回 home 路径。"""
    home = temp_home(testcase)

    # 共享 + 各客户端用户级 skill
    write_skill(home / ".agents/skills", "shared-demo")
    write_skill(home / ".zcode/skills", "zcode-only")
    write_skill(home / ".claude/skills", "claude-native")
    write_skill(home / ".codex/skills", "codex-user-tool")
    write_skill(home / ".local/share/ego/ego-skills", "ego-tool")

    # ZCode 插件缓存(nested 布局;含一个旧版本缓存)
    make_plugin_cache(home / ".zcode/cli/plugins/cache", "browser-use", "0.4.1",
                      "control-browser", nested=True)
    make_plugin_cache(home / ".zcode/cli/plugins/cache", "browser-use", "0.3.9",
                      "control-browser", nested=True)

    # Claude Code:用户 skill + 设置文件(含虚构 secret)+ 插件缓存 + 未安装的 marketplace checkout
    claude_settings = home / ".claude/settings.json"
    claude_settings.parent.mkdir(parents=True, exist_ok=True)
    claude_settings.write_text(json.dumps({
        "enabled": True,
        "env": {"FAKE_API_TOKEN": FAKE_SECRET},
    }, ensure_ascii=False), encoding="utf-8")
    make_plugin_cache(home / ".claude/plugins/cache", "cool-plugin", "1.0.0",
                      "cool-skill", nested=True, marketplace="community")
    write_skill(home / ".claude/plugins/marketplaces/community-market/cool-plugin", "marketplace-copy")

    # Haha 包装器:只放一个配置文件(含虚构 secret);实现只允许判断存在性
    haha_cfg = home / ".claude/cc-haha/config.json"
    haha_cfg.parent.mkdir(parents=True, exist_ok=True)
    haha_cfg.write_text(json.dumps({"haha_token": FAKE_SECRET}), encoding="utf-8")

    # Codex:用户 + 系统 + 插件缓存(flat 布局)
    write_skill(home / ".codex/.system/skills", "codex-system-docs")
    make_plugin_cache(home / ".codex/plugins/cache", "publisher-plugin", "2.0.0",
                      "another-skill", nested=False, marketplace="x")

    # Accio:两个账号(字母账号 a + 纯数字账号)+ 官方缓存 + 安装清单(含虚构 secret)
    accio_a = home / ".accio/accounts/a"
    write_skill(accio_a / "skills", "accio-skill")
    write_skill(accio_a / "official-cache", "accio-docs")
    (accio_a / "installed.json").write_text(json.dumps({
        "token": FAKE_SECRET,
        "skills": [{"name": "accio-skill", "id": "acc-1", "official": False,
                    "version": "1.2", "oss": "github"}],
    }, ensure_ascii=False), encoding="utf-8")
    write_skill(home / ".accio/accounts/10086/skills", "numeric-account-skill")

    # WorkBuddy:用户 + connector + 插件缓存;两个 marketplace 商品目录(未安装)
    write_skill(home / ".workbuddy/skills", "wb-user-skill")
    write_skill(home / ".workbuddy/connectors/skills", "wb-connector-skill")
    make_plugin_cache(home / ".workbuddy/plugins/cache", "wb-plugin", "1.1",
                      "wb-plugin-skill", nested=False, marketplace="x")
    write_skill(home / ".workbuddy/skills-marketplace", "catalog-entry-x")
    write_skill(home / ".workbuddy/connectors-marketplace", "catalog-connector")

    # Cindy:codex-home 投影 + 系统投影 + 插件投影
    cindy = home / "Library/Application Support/Cindy"
    write_skill(cindy / "codex-home/skills", "cindy-user-skill")
    write_skill(cindy / "codex-home/.system/skills", "cindy-system-skill")
    make_plugin_cache(cindy / "plugins/cache", "cindy-plugin", "1.0",
                      "cindy-plugin-skill", nested=False, marketplace="x")

    # 工作区位置:项目内 .claude/skills + 配置文件
    ws = write_skill(home / "projects/demo-app/.claude/skills", "workspace-skill")
    data_dir = home / "project-data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "workspace-locations.txt").write_text(
        "# 测试用工作区 skill 目录\n{}\n".format(ws.parent.parent), encoding="utf-8")
    return home


def build_multi_client_paths(testcase):
    """返回 (home, data_dir) 二元组,方便 build_inventory(home, data_dir) 直接消费。"""
    home = build_multi_client_home(testcase)
    return home, home / "project-data"
