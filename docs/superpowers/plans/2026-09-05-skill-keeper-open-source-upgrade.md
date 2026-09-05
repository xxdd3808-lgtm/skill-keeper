# skill-keeper v4 精简开源泛化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` task-by-task. 每项使用复选框跟踪；阶段门槛全绿后自动继续，不要求创建子 Agent 或等待人工逐项验收。

**Goal:** 保留 v3.1.1 私人版全部精确能力与安全闭环，让 Windows、macOS、Linux 可以安装运行，并让未知 Agent 由其大模型提供本地 Skill 根目录后立即进入统一盘点。

**Architecture:** 大模型只充当“运行时位置适配器”，输入客户端名和根目录；本机扫描器确认 Skill 内容。保留现有适配器、inventory v2、tree hash、报告、ChangePlan、备份和事务。只新增统一 CLI、最小位置声明、跨平台锁/路径和 apply 前目标预检。

**Tech Stack:** Python 3.8+；运行时标准库，可选 PyYAML/gh；unittest；GitHub Actions。

**Design:** [精简开源泛化设计](../specs/2026-09-05-skill-keeper-open-source-design.md)。

## 约束与地界

- 计划制定时基线：HEAD `f858d2ba89d82acc93bcd78e30f3bc6ac1b24b04`，Python 3.9.6，`python3 scripts/verify.py` 为 233 项成功、0 失败、0 skipped。开工必须重测，变化则保护用户修改并记录新基线，不 reset。
- 不改变现有 tree hash、instance/logical ID、inventory schema 主语义、备份格式、审查绑定、known-sources、builtin owner 和计划 digest 安全前提。
- 模型位置声明默认只读，不能包含 mutable、instance ID、tree hash 或变更指令；只允许本机扫描实例进入 plan/apply。
- 扫描、报告和位置输入只读；remove/update/restore 继续走 ChangePlan → 确认 digest → 锁 → 备份 → 事务 → 验证/回滚 → 审计。
- 全部新测试使用临时 HOME 和虚构目录。真实客户端目录、插件缓存、个人 data/backups 只读；不收集、不上传、不遥测，不读取 token/key/cookie/env。
- 运行时继续仅依赖标准库；CLI 不引入 Click/Typer，不增加数据库、模型 API、服务端或守护进程。
- 不做客户端 profile/名单、社区回执、portable hash、完整 capability 框架、报告改版、迁移扩展、云端 Skill 管理或人工客户端验收。
- 允许修改：`scripts/`、`tests/`、`pyproject.toml`、`.github/workflows/ci.yml`、`.gitignore`、`README.md`、`SKILL.md`、`AGENTS.md`、`SECURITY.md`、相关 `docs/`、`PROGRESS.md`、`BLOCKED.md` 和现有启动器。其余只读。
- 不自动 push、tag、发布、改历史。每个 Task 小提交，保留执行前已有修改。

## 执行与防作弊

- 首次读取 `AGENTS.md`、设计、Plan、`PROGRESS.md`、`BLOCKED.md` 和 Git diff。每项按“红测试 → 最小实现 → 相关测试 → `python3 scripts/verify.py` → 文档 → 小提交”执行。
- 禁止删测试、skip、放宽断言、吞错、`|| true`、伪造平台、mock 被测业务函数或让模型输入升级为可写目标。
- 同一门槛连续失败三次，将命令、输出、判断和下一步写入 `BLOCKED.md`；继续不依赖工作，但不得越过该阶段宣称完成。
- 每项完成立即更新 `PROGRESS.md`；BLOCKED 无问题也写“无”。

## 两个阶段

```text
Task 0 冻结私人版
  → Task 1 可安装统一 CLI 与单一运行态
  → Task 2 最小跨平台底座
  → Task 3 模型位置声明与未知客户端盘点  [阶段 A：三平台只读闭环]
  → Task 4 跨平台安全变更
  → Task 5 CI、文档与最终验收            [阶段 B：最终闭环]
```

## Task 0：冻结私人版零退化合同

**Files:** 创建 `tests/fixtures/private-v311/`、`tests/test_private_compatibility.py`；修改 `tests/helpers.py`、`PROGRESS.md`、`BLOCKED.md`。

- [ ] 运行 `git status --short`、`git rev-parse HEAD`、`python3 --version`、`python3 scripts/verify.py`，记录真实基线和原测试 ID。
- [ ] 手工建立完全虚构 fixture，覆盖 shared/Codex/WorkBuddy、自建、builtin owner、symlink、重复加载、审查、备份和旧 CLI；不得复制个人 inventory 或真实 Skill 名单。
- [ ] 冻结现有适配器发现、load context、共享库区块、计划/备份、仓库内 data/backups 兼容布局和旧 `python3 scripts/*.py` 入口。
- [ ] 检查 fixture 与日志无用户名、账号和真实绝对路径。

**Acceptance:** 新兼容测试在未改业务代码时通过；现有 233 项风险语义不减少，skipped=0。

## Task 1：统一安装、CLI 和运行态

**Files:** 创建 `pyproject.toml`、`scripts/__init__.py`、`scripts/cli.py`、`tests/test_cli_v4.py`、`tests/test_packaging_install.py`；修改 `scripts/core/runtime.py`、`scripts/manage.py`、现有入口脚本、`.gitignore`。

- [ ] 先写测试：`python -m scripts.cli --help` 和临时安装后的 `skill-keeper --help` 可从仓库外运行；旧脚本继续调用同一 service 层。
- [ ] `pyproject.toml` 声明 Python `>=3.8` 和 console script `skill-keeper = scripts.cli:main`；构建工具只在安装时使用，运行时不新增依赖。
- [ ] 统一命令保留现有 scan/report/manage，并增加轻量 `doctor --json`，只报告版本、Python、运行目录、锁后端和已登记位置。
- [ ] 新安装统一使用 `~/.skill-keeper/data`、`~/.skill-keeper/cache`、`~/.skill-keeper/backups`；解析优先级为显式参数 > 现有环境变量 > 可识别旧仓库运行态 > 新默认。
- [ ] 只识别真实 v2/v3 marker 才启用旧仓库布局；沿用已有迁移预演，不增加迁移系统或自动搬家。

**Acceptance:** `pip install .` 到临时环境后可离线运行 doctor/scan/report；私人兼容合同全绿。

## Task 2：修复必要的跨平台边界

**Files:** 创建 `scripts/core/platform.py`、`tests/test_platform_minimum.py`、`tests/test_windows_paths.py`；修改 `scripts/core/io.py`、`scripts/core/paths.py`、`scripts/core/runtime.py`、`scripts/scan.py`。

- [ ] 将 `fcntl` 改为延迟选择的 POSIX/Windows 标准库锁后端，保留 `FileLock`/`change_lock` 接口和非阻塞错误语义。
- [ ] 写真实双进程竞争测试；同一锁只能一个成功，正常退出释放。异常退出恢复由现有事务状态处理，不能静默破锁。
- [ ] 用原生路径函数替换 `_extra_locations()` 的 `startswith("/")`；Windows 接受 drive 和 UNC，macOS/Linux 接受各自绝对路径。
- [ ] 归档路径继续走 `validate_archive_member_path`，拒绝反斜杠、绝对路径、`.`、`..` 和链接越界；本机路径函数不能被备份恢复调用。
- [ ] Windows import smoke 覆盖全部 `scripts.core` 模块；删除只在导入阶段就会触发的 macOS/Linux 假设。

**Acceptance:** Windows 可导入并扫描虚构路径；锁和 archive/native 路径反例全部通过；现有备份路径合同无变化。

## Task 3：让模型成为未知客户端的位置适配器

**Files:** 创建 `scripts/core/location_input.py`、`tests/test_location_input.py`、`tests/test_unknown_client_flow.py`；修改 `SKILL.md`、`scripts/cli.py`、`scripts/scan.py`、`scripts/core/observations.py`、`scripts/core/models.py`、`scripts/report.py`、`data/client-locations.example.json`（若存在）。

**Inputs:**

```text
skill-keeper scan --root CLIENT=PATH
skill-keeper scan --locations-json FILE
skill-keeper scan --locations-json -
```

- [ ] 位置声明只允许 schema_version、client、observed_by、complete、roots[path/scope/load_state]；限制总输入 64 KiB、roots≤32、字符串≤4 KiB、UTF-8 和嵌套深度。
- [ ] 禁止 mutable、instance_id、tree_hash、命令、网络地址和秘密字段；解析时不打开声明中的任何 Skill 文件，只有后续现有扫描器按根目录读取。
- [ ] `--root` 和位置声明默认只在本次 scan 使用且只读，不单独保存；inventory 仅记录 home-relative display path、证据类型和完整度。
- [ ] 已有适配器/本地 client-locations 与临时声明按真实路径去重；本机事实优先。模型的 load_state 只显示“客户端自报”，不升级为 confirmed。
- [ ] 在根 `SKILL.md` 写通用流程：模型识别 OS/客户端，寻找根目录，调用上述命令；不知道就标 complete=false，禁止猜路径或修改配置。
- [ ] 未知根需要长期管理时，沿用本地 `client-locations.json`。只有用户本地明确登记 `mutable:true` 后，现有策略才可能开放 plan；临时声明永远不能写。
- [ ] 端到端 fixture：虚构新客户端不改任何适配器，通过 stdin 声明根目录后完成盘点、重复检测和报告；恶意声明不能越界、泄密或产生操作入口。

**阶段 A 门槛：**

```bash
python3 -m unittest tests.test_private_compatibility tests.test_cli_v4 \
  tests.test_packaging_install tests.test_platform_minimum \
  tests.test_windows_paths tests.test_location_input tests.test_unknown_client_flow
python3 scripts/verify.py
```

全部成功、0 skipped 后自动进入 Task 4。

## Task 4：在真实目标旁预检并复用现有安全事务

**Files:** 创建 `scripts/core/preflight.py`、`tests/test_cross_platform_preflight.py`；修改 `scripts/core/changes.py`、`scripts/core/backup.py`、`scripts/core/transactions.py`、`scripts/core/service.py`、`scripts/core/policy.py`。

- [ ] apply 在取得锁后、移动目标前，于目标同目录创建唯一工具标记，实际验证创建、同卷 rename/replace、fsync 能力并清理；预检失败不改变目标。
- [ ] 不建立持久 capability snapshot 或 OS 支持表。每次 apply 针对当前目标、当前文件系统重新验证，避免网络盘、权限变化和陈旧结论。
- [ ] 位置声明来源在 service 和 policy 两层拒绝 plan；只有当前 inventory 中本机扫描、mutable 且现有策略允许的 instance ID 可用。
- [ ] Windows 遇到占用文件、大小写冲突、无权创建 symlink 或无法验证权限往返时明确拒绝并回滚；普通文件树通过现有备份/事务流程。
- [ ] 保留 tree hash、位置根、known-sources、owner、观察完整性、archive digest、计划过期和候选安检全部前置条件。
- [ ] 故障测试覆盖锁竞争、预检失败、第二目标失败、rename 失败和进程中断恢复；现有 POSIX 中断测试继续通过。

**Acceptance:** 三个平台对普通虚构 Skill 完成 plan/apply/rollback，或在无法满足安全前提时于目标变化前拒绝；模型临时位置永远没有变更入口。

## Task 5：最小三平台 CI、文档和最终验收

**Files:** 创建 `.github/workflows/ci.yml`、`SECURITY.md`；修改 `scripts/verify.py`、`README.md`、`SKILL.md`、`docs/architecture.md`、`AGENTS.md`、`PROGRESS.md`、`BLOCKED.md`，必要时刷新虚构 example。

- [ ] CI 使用四个 job：Ubuntu/Python 3.8、Ubuntu/主力 Python、macOS/主力 Python、Windows/主力 Python。每个 job 执行 `pip install .` 和 `python -m scripts.verify` 或等价统一验收；0 skipped。
- [ ] 平台测试在对应真实 runner 执行。不得只 mock `sys.platform` 后宣称跨平台通过。
- [ ] `verify.py` 增加安装 smoke、位置声明恶意输入、模型输入不可写、个人路径/秘密模式和原测试 ID 不减少检查。
- [ ] README 只提供三种流程：直接扫描现有已知客户端、模型传入未知客户端根目录、把确认位置写入本地 client-locations。说明所有数据留在本机。
- [ ] `SECURITY.md` 写清位置声明不可信、真实变更边界、数据留存和漏洞报告方式；不新增 SUPPORT、客户端排行榜或遥测文档。
- [ ] 同步版本、命令、退出码和架构，清理陈旧 v2/测试数描述。确认 tracked 文件无真实清单、账号、秘密和个人绝对路径。
- [ ] 最终交付实际测试总数、四个 CI job、安装 smoke、恶意位置声明、模型输入不可变更、事务故障恢复、私人兼容快照和 Git 范围。push/tag/release 等待用户授权。

**最终门槛：**

```bash
python3 scripts/verify.py
python3 -m pip install .
git diff --check
git status --short
```

## 完成定义

1. Windows、macOS、Linux 都能安装并运行 doctor、scan、report 和现有管理命令。
2. 任意有本机 Skill 目录的未知客户端，可由模型提供根目录后直接完成盘点，无需项目增加适配器或收集用户数据。
3. 临时位置声明默认只读、不上传、不单独持久化，不能产生变更计划；本机内容由扫描器确认。
4. 用户确认并在本地配置为 mutable 的未知根，可复用现有计划、备份、事务、回滚和审计；任何不安全文件系统行为在目标变化前拒绝。
5. 私人版已有适配器、配置、inventory、报告、审查、更新、备份和历史数据零退化，旧运行态不自动迁移。
6. 全部原测试与新增测试 0 skipped，四个 CI job 通过；真实资产未改、无个人数据入 Git、无远端操作。
