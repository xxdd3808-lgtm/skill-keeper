"""客户端加载规则(Task 4,F05):每条规则带来源标识、核实日期与适用范围。

规则口径(2026-09 按各客户端真实行为核实;新证据出现时更新本表并升 RULE_VERSION):
- ZCode:~/.zcode/skills → 共享库 → 工作区 .zcode/.agents → 插件缓存;
- Codex:2026-08-25 起自动导入共享库,叠加自身目录、.system 与插件缓存;
- Claude Code:~/.claude/skills + 插件缓存,不读共享库;Haha 走同一镜像(alias 表达);
- Cindy:只读投影;工作区位置只属于其所在项目上下文,不进全局统计。

eligible(位置在客户端读取集合内)≠ confirmed(有直接运行时证据);
没有证据时 confirmed 保持未知,不把推断冒充事实。
"""
import re

RULE_VERSION = "2026-09-load-rules-v2"

CLIENT_LABELS = {
    "zcode": "ZCode", "codex": "Codex", "claude-code": "Claude Code", "haha": "Haha",
    "cindy": "Cindy", "accio": "Accio", "workbuddy": "WorkBuddy", "ego": "Ego",
}

# 重复加载逐个报告的客户端;Haha 聚合为一条,Cindy 不报
DUP_FINDING_CLIENTS = ("zcode", "codex", "claude-code", "accio", "workbuddy", "ego")

# 规则表:client → (谓词描述, 适用范围, 核实日期, 谓词)
# 谓词输入 loc(dict);scope: global=所有上下文, workspace-only=只影响所在工作区
LOAD_RULES = {
    "zcode": [
        {"match": lambda loc: loc.get("client") == "zcode"
            or loc.get("client") == "workspace-zcode" or loc.get("location_id") == "shared",
         "scope": "global", "source": "zcode-runtime", "verified": "2026-09",
         "note": "ZCode 读取 ~/.zcode/skills、共享库与工作区 .zcode"},
    ],
    "codex": [
        {"match": lambda loc: loc.get("client") == "codex"
            or loc.get("location_id") == "shared",
         "scope": "global", "source": "codex-changelog", "verified": "2026-09",
         "note": "Codex 2026-08-25 起自动导入 ~/.agents/skills"},
    ],
    "claude-code": [
        {"match": lambda loc: loc.get("client") == "claude-code"
            or loc.get("client") == "workspace-claude",
         "scope": "global", "source": "claude-runtime", "verified": "2026-09",
         "note": "Claude Code 读 ~/.claude/skills 与工作区 .claude"},
    ],
    "haha": [
        {"match": lambda loc: "haha" in (loc.get("aliases") or []),
         "scope": "global", "source": "haha-traces", "verified": "2026-09-02",
         "note": "Haha 走 ~/.claude/skills 镜像(alias),不直接读共享库"},
    ],
}

DEFAULT_RULE = {"scope": "global", "source": "client-location", "verified": "-",
                "note": "同客户端位置默认视为可加载(推断)"}


def rules_for(client):
    return LOAD_RULES.get(client)


def location_in_client(loc, client):
    """位置是否在该客户端的读取集合内(eligible 口径)。"""
    if client == "zcode":
        return (loc.get("client") == "zcode" or loc.get("client") == "workspace-zcode"
                or loc.get("location_id") == "shared")
    if client == "codex":
        return loc.get("client") == "codex" or loc.get("location_id") == "shared"
    if client == "claude-code":
        return loc.get("client") == "claude-code" or loc.get("client") == "workspace-claude"
    if client == "haha":
        return "haha" in (loc.get("aliases") or [])
    return loc.get("client") == client


def rule_evidence(client):
    """返回该客户端规则的证据列表(来源/核实日期/范围)。"""
    rules = rules_for(client)
    if rules:
        return [{"source": r["source"], "verified": r["verified"],
                 "scope": r["scope"], "note": r["note"]} for r in rules]
    return [dict(DEFAULT_RULE, client=client)]
