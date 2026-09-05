# AGENTS.md — skill-keeper

一句话定位：本地 Agent Skill 管家。它盘点 ZCode、Codex、Accio、WorkBuddy、Claude Code、Haha、Cindy、Ego 及未知客户端 Skill 的功能、来源、加载关系和健康问题，生成第三方价值审查，并通过备份与事务闭环安全执行删除、更新和恢复。本身也以 Skill 形态提供，触发词见 `SKILL.md`。

## 怎么跑

```bash
pip install .
skill-keeper doctor --json
skill-keeper scan --json
skill-keeper scan --root CLIENT=PATH --json
python3 scripts/report.py [--serve]
python3 scripts/check_updates.py
python3 scripts/value_review.py queue
python3 scripts/manage.py plan remove/update/restore --json
python3 scripts/manage.py apply <plan_id> --digest <digest> --confirm --json
python3 scripts/manage.py status/recover <plan_id> --json
python3 scripts/verify.py
```

当前版本 4.0.0。统一 CLI 与 `scripts/*.py` 参数一致；退出码 0=健康/无差异，1=有红色问题/有差异，2=失败或观察不完整。

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

v4 本地独立复核已修复验收器假成功、共享根声明关系丢失、模型根越界、Windows HOME 隔离、锁异常清理和输入回显边界。现役架构见 `docs/architecture.md`，版本演化见 `docs/changes.md`，执行证据见 `PROGRESS.md`，阻塞见 `BLOCKED.md`。

本地 macOS Python 3.9/3.12 门槛通过后，下一步是 push `case1` 触发四平台 CI；只有 CI 实际全绿后才能合并、tag 或发布。push/tag/release 均需用户授权。
