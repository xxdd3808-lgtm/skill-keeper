# 给执行 Agent 的任务书：skill-keeper v4 精简泛化

你负责把 skill-keeper 升级为三平台可安装、私人版零退化、未知 Agent 也能盘点的 v4。先读 `AGENTS.md`、[精简设计](../specs/2026-09-05-skill-keeper-open-source-design.md)、[详细 Plan](2026-09-05-skill-keeper-open-source-upgrade.md)、`PROGRESS.md`、`BLOCKED.md` 和 Git diff。详细 Plan 是唯一任务清单。使用 `executing-plans`，按 Task 0–5 串行执行，不创建子 Agent；阶段门槛全绿后自动继续。

## 核心办法

把大模型当作“运行时客户端适配器”。它只告诉 skill-keeper 当前客户端名和本机 Skill 根目录；不列 Skill 清单，不上传回执。skill-keeper 在本机自行读取 SKILL.md、计算指纹、识别重复并生成报告。

统一输入仅有：

```text
skill-keeper scan --root CLIENT=PATH
skill-keeper scan --locations-json FILE|-
```

位置声明限 64 KiB、32 个根，只允许 client/observed_by/complete 和 root 的 path/scope/load_state。禁止 mutable、instance ID、tree hash、命令、URL 和秘密字段；默认只读、只用于本次扫描、不单独持久化。模型报告只标“客户端自报”，不能覆盖本机事实或变成 confirmed。

长期使用继续写本地 `client-locations.json`。只有用户本地明确登记 `mutable:true`，且实例由本机扫描确认，才可能进入现有 ChangePlan → digest 确认 → 锁 → 备份 → 事务 → 验证/回滚 → 审计。临时位置声明永远不能删除或更新文件。

## 基线与范围

制定计划时 HEAD 为 `f858d2ba89d82acc93bcd78e30f3bc6ac1b24b04`，Python 3.9.6，`python3 scripts/verify.py` 为 233 项成功、0 失败、0 skipped。开工重跑 git status/rev-parse、Python 版本和 verify；变化时保护用户修改、记录新基线，不能 reset。

只做六项：

- **Task 0：** 用纯虚构 fixture 冻结 v3.1.1 私人适配器、load context、报告、审查、备份、旧 CLI 和运行态兼容。
- **Task 1：** 增加 `pyproject.toml` 和统一 `skill-keeper` CLI；新安装统一使用 `~/.skill-keeper/`，显式配置与旧仓库运行态继续优先。
- **Task 2：** 修复 `fcntl`、Windows drive/UNC、原生路径与归档路径隔离；只增加必要的平台工具。
- **Task 3：** 实现 `--root`/`--locations-json`，在根 `SKILL.md` 教模型自适应寻找目录；未知客户端不改适配器即可盘点。
- **Task 4：** apply 时在真实目标同目录做创建/rename/fsync 最小预检，随后复用现有备份事务；不建复杂 capability 系统。
- **Task 5：** GitHub Actions 跑 Ubuntu Python 3.8/主力版、macOS、Windows；同步 README、SECURITY、架构和验收。

不做社区回执、遥测、服务器、客户端名单/profile、每客户端适配器、portable hash、支持等级排行、报告改版、迁移扩展、数据库、模型 API、守护进程、云端 Skill 管理和发布前人工客户端测试。

## 地界与验收

只改详细 Plan 白名单内的代码、测试、打包、CI 和文档。真实 Skill、客户端目录、插件缓存、个人 data/backups 只读；不读取或输出 token/key/cookie/env，不自动迁移，不 push/tag/release。

每项按“红测试→最小实现→相关测试→`python3 scripts/verify.py`→文档→小提交”执行。禁止删测试、skip、放宽断言、吞错、`|| true`、mock 被测业务函数、伪造平台或把模型输入升级为可写。连续三次失败写 `BLOCKED.md`，不得越过对应阶段；每项更新 `PROGRESS.md`，无阻塞也写“无”。

完成时必须证明：三平台可安装并运行；未知客户端只提供根目录即可盘点；位置声明不上传且不能写；本地 mutable 根可复用现有安全闭环；私人版和全部旧测试零退化；四个 CI job 与全部测试 0 skipped。交付实际测试数、安全反例、事务恢复和 Git 范围。push、tag、GitHub Release 只在用户授权后由 AI 执行。
