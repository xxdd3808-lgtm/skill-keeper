# AGENTS.md — skill-keeper

一句话定位：本地 Agent Skill 管家。它盘点 ZCode、Codex、Accio、WorkBuddy、Claude Code、Haha、Cindy、Ego 及未知客户端 Skill 的功能、来源、加载关系和健康问题，生成第三方价值审查，并通过备份与事务闭环安全执行删除、更新和恢复。本身也以 Skill 形态提供，触发词见 `SKILL.md`。

## 怎么跑

```bash
pip install .
skill-keeper scan [--json]                 # 盘点(只读;--root CLIENT=PATH 声明未知客户端)
skill-keeper report [--json] [--serve]     # 价值审查报告
skill-keeper updates [--json]              # 更新检查(只读)
skill-keeper review queue/show/record [--json]   # 价值审查(默认增量待办;--all 完整)
skill-keeper manage plan/vet/apply/status/recover/rescan [--json]
skill-keeper doctor [--json]               # 环境自检
python3 scripts/verify.py                  # 全量验收(判卷入口)
```

当前版本 4.1.0。统一 CLI 与 `scripts/*.py` 参数一致；退出码 0=健康/无差异，1=有红色问题/有差异，2=失败或观察不完整。

## 技术栈

纯 Python 3.8+ 标准库；PyYAML 和 gh CLI 都是可选能力。运行时零第三方依赖。CI 定义 Ubuntu Python 3.8、Ubuntu、macOS、Windows 四个 job；发布前以 GitHub Actions 实际结果为准。

## 目录与约定

- 代码用 `os.path.realpath(__file__)` 定位项目根，不依赖当前工作目录。
- 新安装默认把运行态放在 `~/.skill-keeper/`；真实旧仓库运行态继续使用仓库 `data/`、`backups/`，不自动迁移。`SKILL_KEEPER_HOME/DATA/STAGING` 可显式隔离。
- 个人配置：`data/groups.json`、`self-built.txt`、`known-sources.json`、`ignore.json`、`workspace-locations.txt`、`client-locations.json`，全部 gitignore。
- inventory、报告、审查台账、计划、事务、审计、备份和候选缓存都是本地运行态，不得提交。仓库只允许 `data/*.example.*` 模板。
- 候选暂存不得放进任何 Skill 树，避免被客户端递归识别成已安装 Skill。
- 数据 schema 当前为 2；JSON 状态用同目录临时文件、fsync、`os.replace` 原子发布。

## 不可破坏的规则

- scan/report/check_updates/queue 只读。系统永不自动删除。
- 所有变更必须走不可变 ChangePlan → digest 确认 → 互斥锁 → 目标旁预检 → 已验证备份 → 持久事务 → 验证或回滚 → 审计。
- 变更目标只能是当前 inventory 里本机确认、mutable 且策略允许的稳定 instance ID；插件缓存、模型临时声明、自建和客户端托管正本不可直接变更。
- `known-sources` 的 builtin-app 可登记 owner：owner 正本继续拒绝；非 owner 位置的散布副本只允许正规 remove，update 仍拒绝。保护配置损坏时拒写。
- 客户端配置只读字段白名单；token/key/cookie/env 不读取、不输出。GitHub 星数只表示仓库热度，不能单独触发删除。
- 未知客户端由模型临时声明“客户端名 + HOME 内 Skill 根目录”；同一物理目录只扫描一次，但 `observation.reported_roots` 必须保留每个客户端的读取关系。声明只读、不可升级为 confirmed 或 mutable。
- 提交前运行 `python3 scripts/verify.py` 和 `git diff --check`；不得删除/改名 v3.1.1 冻结的 233 个测试 ID，不得 skip、放宽断言或伪造平台结果。

## 当前状态与下一步

v4 已于 2026-09-05 发布(tag v4.0.0,Release 166d1a9,四平台 CI 全绿)。现役架构见 `docs/architecture.md`,版本演化见 `docs/changes.md`,使用细节见 `docs/skill-manual.md`,执行证据见 `PROGRESS.md`,阻塞见 `BLOCKED.md`。

2026-09-06 全面审查(docs/reviews/2026-09-06/,F01–F13)**收尾轮已全部修完**:第一阶段 F01–F06/F08/F09;独立复核封住的 3 个缺口(网页安检不得用 confirm 造证据、其他事务损坏 fail-closed、审查记录保存并比对 review_policy_version);第二阶段 F10–F13(加载模型统一 evaluate_load、观察不完整各入口退出 2、回滚基准=计划前置哈希、指纹按真实根复用+空哈希身份隔离)+报告 view 分离;第三阶段定点优化(F07 候选邻接表,语义冻结零差异);第四阶段(统一 CLI 补 updates/review、need_vet/queue 默认增量体检、SKILL.md 精简 66% 细节下沉 docs/skill-manual.md)。独立复核的 6 个发布阻断项(SKILL.md frontmatter/更新顺序、复制命令安装态 CLI、占位证据后端拒绝、AGENTS 状态同步、版本计划)已修复。verify **373 项**全绿。

v4.1.0 已于 2026-09-06 发布(tag=1eb9345,四平台 CI 全绿,[Release](https://github.com/xxdd3808-lgtm/skill-keeper/releases/tag/v4.1.0))。发布后 main 经分支 CI 快进 3 笔:022f3df(SKILL.md 报告交付固定两件套:HTML 链接正下方贴 --serve 网页版 URL)、f4929ea(verify 输出防污染:4 处进程内打印接住,--json 的 stdout=纯 JSON)、a677a7e(全项目 review 第二轮 [docs/reviews/2026-09-06-r2/](docs/reviews/2026-09-06-r2/):0 P0/P1,3 个 P2 已修——/api/ignore 进程内锁、serve 死代码、CSP 声明措辞)。当前无进行中工作;后续方向:31 个第三方 Skill 旧审查结论过期待重审、SKILL 触发词调优,按真实使用反馈决定。发布流程注意:main 受保护,新 SHA 需先经分支 CI 出全绿再快进 main。
