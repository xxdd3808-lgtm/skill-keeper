# AGENTS.md — skill-keeper

一句话定位:本地 Agent Skill 管家——盘点本机全部 agent skill 的功能/来源/配套客户端,体检健康问题,生成 Markdown+HTML 报告;本身以 skill 形态存在(触发词见 SKILL.md)。

## 怎么跑

```bash
python3 scripts/scan.py                    # 全位置扫描 → data/inventory.json(只读)
python3 scripts/report.py                  # 生成 data/report.md + report.html(含处理建议)
python3 scripts/report.py --serve          # 交互报告:网页一键 更新/删除/忽略/恢复(仅 127.0.0.1+token)
./启动技能报告.command                      # macOS 双击可启动同一交互服务(自动开浏览器)
python3 scripts/check_updates.py           # 与上游比对(只读,gh api / skills.sh API)→ 缓存 data/updates.json
python3 scripts/remove_skill.py <目录名>    # 备份→删除→清锁文件
python3 scripts/make_sample_report.py      # 个人盘点 → 脱敏示例报告
```

退出码约定:scan / report / check_updates 的 `--json` 模式,0=健康/无差异,1=有红色问题/有差异。

## 技术栈

纯 Python 3.8+ 标准库;可选 PyYAML(frontmatter 解析更稳)、gh CLI(GitHub 来源更新检查)。无构建步骤、无第三方依赖。

## 目录与约定

- 脚本用 `os.path.realpath(__file__)` 反推项目根,不依赖调用路径;项目实体可整体迁移,客户端发现靠符号链接(如 `~/.agents/skills/skill-keeper` → 项目根)。
- `data/groups.json`、`data/self-built.txt`、`data/known-sources.json`、`data/ignore.json`、`data/workspace-locations.txt` 是用户个人配置(已 gitignore),扫描/报告行为受它们影响;新用户从同名 `.example` 文件复制(ignore.json 可选,无则不忽略任何问题)。
- `data/inventory*.json`、`data/updates.json`、`data/vetted.json`、`data/report.*`、`data/actions.log`、`backups/` 是运行时产物,含个人数据,永远不入库。
- 铁律:扫描/报告只读;删除/更新前强制 tar 备份到 `backups/`;自建白名单 skill 受保护(删除需 `--force`);不修改插件缓存;任何操作后重跑 `scan.py`。
- 提交前自查:`git grep` 不得出现真实 skill 清单、个人路径或个人配置内容。

## 当前状态与下一步

- v1.1.0:报告带「处理建议」分区(🟢建议更新/🛡️建议保留/🔍待安检/🟡待确认/🔵可自动处理/提示,自动研判给结论+人话理由,每条含功能/来源/客户端上下文),看差异为页内红绿 diff;`report.py --serve` 或双击 `启动技能报告.command` 本地一键执行(先备份、后重扫、token 鉴权,动作记入 data/actions.log);汇报必带报告 file:// 链接与交互入口;已在 macOS + ZCode / Claude Code / Codex CLI / Ego 验证。
- v1.2.0:安检台账接入 skill-vetter,**安检是体检固定步骤**——扫描出「待安检」(第三方没审过或内容变过)就当场逐个按 skill-vetter 四步清单审完记账(data/vetted.json),不用用户点名,复检靠内容指纹自动触发;报告健康列与处理建议区显示安检状态。2026-08-30 完成首轮全量安检(21 个第三方全部 safe)。
- 候选改进:更多客户端目录适配(家目录位置增删 `scan.py` 的 `LOCATIONS`;项目内工作区 skill 已由 `data/workspace-locations.txt` 配置驱动)、报告主题、按需增量扫描、更新建议信任分级。
