# AGENTS.md — skill-keeper

一句话定位:本地 Agent Skill 管家——盘点本机全部 agent skill 的功能/来源/配套客户端,体检健康问题,生成 Markdown+HTML 报告;本身以 skill 形态存在(触发词见 SKILL.md)。

## 怎么跑

```bash
python3 scripts/scan.py                    # 全位置扫描 → data/inventory.json(只读)
python3 scripts/report.py                  # 生成 data/report.md + report.html
python3 scripts/check_updates.py           # 与上游比对(只读,gh api / skills.sh API)
python3 scripts/remove_skill.py <目录名>    # 备份→删除→清锁文件
python3 scripts/make_sample_report.py      # 个人盘点 → 脱敏示例报告
```

退出码约定:scan / report / check_updates 的 `--json` 模式,0=健康/无差异,1=有红色问题/有差异。

## 技术栈

纯 Python 3.8+ 标准库;可选 PyYAML(frontmatter 解析更稳)、gh CLI(GitHub 来源更新检查)。无构建步骤、无第三方依赖。

## 目录与约定

- 脚本用 `os.path.realpath(__file__)` 反推项目根,不依赖调用路径;项目实体可整体迁移,客户端发现靠符号链接(如 `~/.agents/skills/skill-keeper` → 项目根)。
- `data/groups.json`、`data/self-built.txt`、`data/known-sources.json` 是用户个人配置(已 gitignore),扫描行为受它们影响;新用户从同名 `.example` 文件复制。
- `data/inventory*.json`、`data/report.*`、`backups/` 是运行时产物,含个人数据,永远不入库。
- 铁律:扫描/报告只读;删除/更新前强制 tar 备份到 `backups/`;自建白名单 skill 受保护(删除需 `--force`);不修改插件缓存;任何操作后重跑 `scan.py`。
- 提交前自查:`git grep` 不得出现真实 skill 清单、个人路径或个人配置内容。

## 当前状态与下一步

- v1.0.0 已发布;已在 macOS + ZCode / Claude Code / Codex CLI / Ego 验证。
- 候选改进:更多客户端目录适配(增删 `scan.py` 的 `LOCATIONS`)、报告主题、按需增量扫描。
