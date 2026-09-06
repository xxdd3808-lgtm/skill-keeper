# skill-keeper 可信性与维护成本优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 使用当前环境可用的同名技能；没有该技能时直接按本计划串行执行并保留验收证据。不要求创建子 Agent。

**Goal:** 让现有盘点、建议、删除、更新、恢复形成一致且可验证的闭环，修复已复现的安全与失效状态问题，再降低重复计算成本。

**Architecture:** 保留现有模块和 Python 标准库，通过小范围增加路径/策略校验、事务状态、观察上下文、审查有效性和应用服务接口，收拢反复出现的共同规则。CLI 和网页复用应用服务，报告只消费派生视图。分三阶段交付，不整体重写，不自动操作真实 Skill。

**Tech Stack:** Python 3.8+ 标准库；可选 PyYAML、gh CLI；现有 unittest；macOS/POSIX 为本轮目标环境。

**Spec:** [演化复盘与优化依据](../specs/2026-09-05-skill-keeper-review.md)，尤其 F01–F11；同时阅读原始 [v2 设计](../specs/2026-08-31-skill-keeper-v2-design.md)。发生差异时，本计划明确指出的缺陷修复优先；其余保留既有语义。

## Global Constraints

- 纯 Python 3.8+ 标准库;可选 PyYAML、gh CLI，无必需第三方依赖。
- 扫描/报告/更新检查/队列不得修改已安装 Skill；只允许写指定运行态/缓存输出。缓存清理也必须限定为本工具所有的对象。
- 所有真实删除/更新/恢复必须走不可变 ChangePlan → 用户确认 digest → 互斥锁 → 创建并验证备份 → 执行 → 验证/回滚 → 审计；用户请求实现代码不等于批准真实资产变更。
- 不修改客户端插件缓存与客户端管理目录，不读取或输出 token/key/cookie/env。
- GitHub 星数只是仓库热度，任何单一因素不能自动触发删除，系统永不自动删除。
- 测试全部使用临时 HOME、虚构数据与隔离 data/staging/backup 路径。真实环境最多只读扫描，不重写已有报告与台账来掩盖问题。
- 不全局排除 `data/`、`backups/`、脚本、模板和参考文件以缩短指纹计算；普通第三方内容指纹规则保持完整。
- 兼容读取既有 v2 数据和备份。若旧对象无法证明安全，保留并标记不可应用/需重新计划，不删除、不改写成伪造的安全对象。
- 新增持久格式需要显式 schema/version 和迁移说明；旧计划读取兼容，缺少新增安全前提的旧计划不得继续执行。
- 整理文档不重写历史事实；保留当前本地未推送提交。不得 reset 到 origin/main，不自动 push、发布、改历史或操作远端。

## 默认决定与范围

以下是计划制定者在本次审视中采用的默认值，尚非用户逐项选定：

1. 优先级为防越界/可恢复 > 事实与状态可信 > 操作便利 > 速度；若用户主要想做界面改版，此计划会延后该工作。
2. 保留个人本地工具定位，不做通用插件商店/包管理平台，不增加数据库、模型 API 或后台守护进程。
3. 实施串行，三个阶段分别验收；不能以总测试绿为由跳过中间资产安全门槛。
4. 自建 Skill 默认免于普通删除/更新；客户端托管内容继续拒绝单独变更。本轮不增加任意 `--force` 绕过入口。用户直接通过外部工具操作不属于本引擎能够隔离的范围。
5. 自我扫描污染通过新增外部运行态路径与迁移预演处理；不在本轮自动迁移真实个人文件或更换共享库链接。
6. 保留当前 instance_id 和 logical_id 兼容字段；先补历史连接与有效性，不贸然进行全库身份重编号。

允许修改：`scripts/`、`tests/`、`examples/fixtures/`、生成的 `examples/report-sample.html`、本计划列出的 `docs/`、`README.md`、`SKILL.md`、`AGENTS.md`、`.gitignore`、`启动技能报告.command`，以及运行进度文件 `PROGRESS.md`、`BLOCKED.md`。`data/*.example.*` 只允许调整本计划新增字段的虚构示例。

其余路径只读，尤其真实个人 `data/`、`backups/` 和客户端目录。测试日志含路径时留在临时目录；仓库文档只记录虚构路径、提交 ID 和聚合数字。执行期可建立 `codex/` 分支并按任务提交；不得提交个人运行态。

## 执行与断点规则

- 首次开工先完成 Task 0，在 `PROGRESS.md` 写不超过 10 行回执：目标理解、阶段顺序、最大风险、已核验基线。
- 新会话先读本计划、`PROGRESS.md`、`BLOCKED.md` 和当前 Git diff，从最后一个未完成任务继续。
- 每完成一项立即记录测试输出摘要、实现提交、剩余风险。任务完成以行为证据为准，不能只改复选框。
- 同一验收连续失败三次，保留证据，转做不依赖它的任务；不得跳过失败门槛发布下一阶段。卡住项记录到 BLOCKED，不扩大范围找捷径。
- 现有测试的断言语义不可削弱；可以追加测试、修正 fixture、为新必填输入补值、替换被本计划明确纠正的错误旧预期。每一处旧预期变更要注明 F 编号和前后语义，保留对应风险验证。
- 不允许删测试、增加 skip、放宽安全阈值、`|| true`、只断言“有输出”，或 mock 掉正在验证的业务函数。故障注入可以替换 OS I/O、网络 runner、验证回调；观察次数应使用 wraps。
- 当前已实跑命令：全量 unittest、各现有 CLI 的 `--help`、只读 `build_inventory()`。下面新增文件/命令标为“本任务创建”，不是当前已存在能力。

## 阶段与依赖

```text
Task 0 基线
  → Task 1 备份/恢复合同
  → Task 2 执行时策略与输入合同
  → Task 3 事务与中断恢复             [阶段 A 安全门槛]
  → Task 4 观察完整性与加载上下文
  → Task 5 审查历史与有效性
  → Task 6 完整候选与缓存生命周期
  → Task 7 CLI/API/报告操作闭环         [阶段 B 工作流门槛]
  → Task 8 去除重复计算与外部运行态
  → Task 9 交付检查与能力文档           [阶段 C 最终门槛]
```

每个任务按“红测试 → 最小实现 → 绿测试 → 更新相关文档 → 小提交”完成。先写下面列出的行为测试，不先做无关拆文件或格式化。

### Task 0：冻结可复跑基线和验收约束

**Files:** 创建 `PROGRESS.md`、`BLOCKED.md`；后续测试复用 `tests/helpers.py`；本任务不改业务代码。

**Consumes:** 当前 Git 工作树、126 项 unittest、审视规格。

**Produces:** 可审计的基线记录；所有测试运行使用隔离目录。

- [ ] 运行并保留输出：

```bash
git status --short
git rev-parse HEAD
python3 --version
python3 -m unittest discover -s tests
```

期望：若代码仍为审视基线，应为 126 项成功、0 skipped；若已存在用户的新修改，记录差异，保留修改，重新建立真实基线。若只是端口受限，申请仅为本机临时 HTTP 测试所需权限；不把测试跳过或吞掉错误。

- [ ] 用 unittest discovery 记录原始测试 ID 清单和 skipped=0；新增用例不替换这组风险覆盖。记录当前测试资源警告。
- [ ] 记录允许修改路径、真实资产禁区和三个阶段门槛。BLOCKED 无问题也写“无”。
- [ ] 后续每个新回归先确认在未修实现上变红；证明测试确实会发现问题。只需对本任务相关缺陷制造失败，不随机破坏真实环境。

### Task 1：完整验证并安全恢复备份（F01、F02）

**Files:** 修改 `scripts/core/backup.py`、`scripts/core/fingerprint.py`、`scripts/core/changes.py`；创建 `scripts/core/paths.py`、`tests/test_backup_contract.py`；扩展 `tests/test_backup_restore.py`。

**Interfaces:**

- 新增 `validate_relative_path(value: str) -> tuple`：返回规范路径组件；拒绝空路径、绝对路径、`.`、`..`、反斜杠、空组件、控制字符。
- 新增 `confined_destination(root, relative) -> Path`：调用前述校验，拒绝中间父目录为 symlink，核对实际父目录归属；不得借由 `resolve()` 把越界输入变成合法路径。
- 新增 `manifest_hash(entries: list) -> str`：复用现有 `canonical_manifest_document()` 和哈希编码；输出与现有 tree_hash 同算法，先保持 MANIFEST_VERSION=2。
- 新增 `validate_backup_manifest(document: dict) -> dict`：返回验证后的规范对象；异常统一 BackupError。
- 保留 `create_backup()`、`verify_backup()`、`restore_backup()` 入口；verify 返回增加 `archive_sha256`，原有字段继续可用。

- [ ] 添加完整往返测试。以下放入 unittest 类中，引用现有 fixture；它当前应失败：

```python
from scripts.core.backup import create_backup, restore_backup, verify_backup
from scripts.core.fingerprint import tree_hash
from tests.test_change_remove import change_env
import os
import shutil

def test_roundtrip_nested_link_and_directory_modes(self):
    env = change_env(self)
    private = env.skill_path / "private"
    private.mkdir(mode=0o700)
    (private / "note").write_bytes(b"fixture")
    (env.skill_path / "alias.py").symlink_to("run.py")
    expected = tree_hash(env.skill_path)
    env.inventory["instances"][0]["tree_hash"] = expected
    saved = create_backup(env.remove_plan(), env.inventory,
                          env.context.backup_dir)
    self.assertTrue(verify_backup(saved["path"])["ok"])
    shutil.rmtree(env.skill_path)
    restore_backup(saved["path"], env.inventory["locations"])
    self.assertEqual(tree_hash(env.skill_path), expected)
    self.assertEqual(os.readlink(env.skill_path / "alias.py"), "run.py")
    self.assertEqual(private.stat().st_mode & 0o777, 0o700)
```

- [ ] 添加表驱动的 malicious manifest 测试：保持 payload 文件名合法，仅修改 original_relative_path 为 `../escape`、`/absolute-fixture`、`a/../../escape`；目录条目 path 为 `../escape`；重复/重叠目标；文件与目录同路径；文件父级是 symlink；缺 entries 或非法类型。verify 必须失败，整个临时根的落地快照无变化。
- [ ] 修正现有 tar 链接/设备测试的夹具：生成合法 gzip、完整有效 manifest，再仅注入非法成员，断言对应结构化原因。未压缩 tar 被 r:gz 拒绝不能算链接防护验收；增加合法对照组确保测试穿过打开归档步骤。
- [ ] 验证规范 manifest 的类型、权限范围、每条路径唯一性、SHA-256 格式、父子关系、payload 一一对应及整树哈希。限制读取资源：manifest ≤ 8 MiB、条目 ≤ 100,000、单文件 ≤ 512 MiB、总解包量 ≤ 2 GiB；超限明确拒绝，不跳过文件后宣称完整。这些是本轮保守默认，作为命名常量和错误信息提供；不得自动提高限制。
- [ ] 归档读写使用上下文管理和分块 I/O；成功归档使用临时文件写完、fsync 后原子发布，写入失败不留下可选择的成功备份。验证不解包到目标目录。
- [ ] 恢复先预检所有目标并物化所有可恢复内容。普通目录先建立，普通文件随后，内部 symlink 最后；不跟随链接写文件。还原目录权限放在子内容创建完成之后；根目录权限也还原。临时目录完成完整验证后才发布。
- [ ] 顶层 symlink 仅恢复链接本身：目标必须已经存在且匹配备份，或是本计划中先恢复的实体；目标缺失时拒绝，不能凭链接 payload 擅自写外部目标。添加 manifest 顺序颠倒的实体+别名测试，不依赖碰巧先恢复共享目录。
- [ ] 使用实际成功落地清单清理半成品，不能按全部计划逆序猜测。模拟第二个实体物化/发布/后验失败以及校验函数抛异常；本次已发布对象全部撤销，其他文件不变，拒绝覆盖现有目标。发布前再次核验冲突；对非协作外部并发修改安全失败，不删除陌生新文件。
- [ ] 恢复计划绑定 archive_sha256、manifest 中目标集合和目标位置快照。替换成另一个合法备份后 apply 也必须拒绝。旧计划缺这些字段提示重新计划，原备份仍保留。
- [ ] 全量 unittest 绿后提交。阶段交付前，用内部链接、相对顶层链接、0700 子目录、只读目录、损坏 manifest、多实体失败各验一次。

实现要点示意（必须补上逐层父路径检查，不能只用字符串前缀）：

```python
def validate_relative_path(value):
    if not isinstance(value, str) or not value or value.startswith("/"):
        raise ValueError("invalid relative path")
    if "\\" in value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("invalid relative path")
    parts = tuple(value.split("/"))
    if any(p in ("", ".", "..") for p in parts):
        raise ValueError("invalid relative path")
    return parts
```

### Task 2：统一执行策略与输入校验（F04、F07 清理边界）

**Files:** 创建 `scripts/core/policy.py`、`scripts/core/staging.py`、`tests/test_change_policy.py`；修改 `scripts/core/changes.py`、`scripts/core/provenance.py`、`scripts/core/models.py`、`scripts/core/io.py`、`scripts/check_updates.py`。

**Interfaces:**

- 新增 `load_policy(data_dir) -> dict`：返回来源/自建保护、策略摘要、issues；缺少可选文件与已存在文件损坏分开。损坏策略拒绝写操作，不能 fallback 成空保护表。
- 新增 `check_action(action, target, location, policy) -> dict`：返回 `allowed: bool, reason_code: str, message: str, policy_hash: str`。
- 新增 `validate_candidate_vet(record, plan) -> dict`；只接受 safe/warning，danger、缺值、非法值均拒绝应用；证据为非空字符串列表，hash、plan_id 必须一致。
- plan 和 apply 都调用上述接口；保留旧 known_sources 参数作为兼容输入，但不能覆盖/削弱从权威 data_dir 加载的保护。

- [ ] 新增反向测试：计划后登记 builtin-app；计划后 mutable 变 false；根目录父级换成指向外部的链接；同内容但顶层实体类型/链接目标变化；配置文件损坏；传入非空其他 known_sources 试图绕过已有保护。每项 apply/plan 必须拒绝且目标不变。
- [ ] 新增非法候选安检测试，当前实现会错误放行：

```python
from scripts.core.changes import apply_plan, ChangeError, vet_path
from scripts.core.io import atomic_write_json
from scripts.core.fingerprint import tree_hash
from tests.test_change_update import update_env

def test_unknown_vet_verdict_never_activates_candidate(self):
    env = update_env(self)
    plan = env.create_plan()
    atomic_write_json(vet_path(plan.plan_id, env.plans_dir), {
        "plan_id": plan.plan_id,
        "candidate_hash": env.v2_hash,
        "verdict": "typo",
        "evidence": ["fixture"],
    })
    with self.assertRaises(ChangeError):
        apply_plan(plan.plan_id, plan.digest, True, env.context)
    self.assertEqual(tree_hash(env.skill_path), env.local_hash)
```

- [ ] 在 data_dir 锁内重读策略和 inventory，然后核对磁盘实体、根目录、链接目标与内容。计划保存的策略与实际动作影响范围变化时要求重新计划，不能只比较内容 hash。
- [ ] remove/update/restore 分别校验实例位置可变性；restore 不能绕过客户端托管限制。self-built、builtin-app、builtin、plugin-cache、client-managed 分开给出可理解原因。
- [ ] 校验 plan_id、backup_id、action、目标 ID 集合、digest、时间字段、preconditions 必填键和类型；拒绝未知 action、重复/重叠目标、未知字段中潜入的路径。把计划理由和推荐记录 ID 正式保存在计划中，摘要保留用户理由。
- [ ] 所有候选载入路径重新校验枚举和证据，即使记录不是经 CLI 写入也不能默认为 safe。warning 必须 `accept_warning is True`；不接受字符串真值。
- [ ] 提前阻断 F07 的目录误删：staging 根必须通过 Skill 树/用户根/符号链接边界检查，清理只处理本工具创建且有所有权记录的候选。不相关 sentinel 目录、无登记的旧目录、有效计划引用一律保留；完整引用与生命周期优化放 Task 6。不得通过给未知旧目录自动补所有权来规避这条约束。
- [ ] 通过测试后提交，并更新 SKILL 的保护规则，使 UI、工作流和引擎一致。输入校验不被描述为可阻止同 UID 任意修改程序的恶意 Agent。

### Task 3：把变更做成可恢复事务（F03、F04）

**Files:** 创建 `scripts/core/transactions.py`、`tests/test_change_faults.py`、`tests/test_transaction_recovery.py`；修改 `scripts/core/changes.py`、`scripts/core/audit.py`、`scripts/core/io.py`、`.gitignore`。

**Interfaces:**

- 新增 `read_transaction(plan_id, context) -> dict`、`recover_transaction(plan_id, context) -> dict`。
- 事务状态文件 `data/transactions/<plan_id>.json`，包含 schema_version、action、phase、prepared_targets、published_targets、original_hashes、candidate_hash、backup_id、受控临时路径和最终结果。
- phase 为 `prepared / mutating / committed / rolling-back / rolled-back / recovery-required`；不可变计划不改写，状态另存。
- `apply_plan()` 保留入口，返回扩展 `transaction_status`、`result_hashes`、`already_applied`；重复请求返回已知结果或明确已消费，不再执行。
- `recover_transaction()` 仅恢复该事务已批准的原状态，不激活新候选；对象冲突时保留实体并标 recovery-required，阻止后续相关写操作。

- [ ] 新增验证崩溃回归，必须看到红→绿：

```python
from scripts.core.changes import apply_plan, ChangeError
from scripts.core.fingerprint import tree_hash
from tests.test_change_remove import change_env

def test_remove_validator_exception_restores_original(self):
    env = change_env(self)
    expected = tree_hash(env.skill_path)
    plan = env.remove_plan()
    def fail_validation():
        raise RuntimeError("fixture validator crash")
    env.context.verify_after_apply = fail_validation
    with self.assertRaises(ChangeError):
        apply_plan(plan.plan_id, plan.digest, True, env.context)
    self.assertTrue(env.skill_path.is_dir())
    self.assertEqual(tree_hash(env.skill_path), expected)
    self.assertEqual(env.last_audit()["rollback_status"], "restored")
```

- [ ] 删除不再边 rmtree 边处理下一目标。验证备份后，将原实体移动到同文件系统的受控事务目录，逐步记录完成项；全部验证后提交状态，再清理本事务的旧实体。更新与恢复复用实际完成清单和同样的失败原则。
- [ ] 删除原实体前写入可恢复状态并 fsync，关键 rename 后持久记录并按平台能力同步父目录。移位中断后由事务状态、实体哈希和备份确定恢复，不猜目录名。旧目录清理失败归入清理待办，不把已提交动作谎报为未执行。
- [ ] 互斥范围包括 load_inventory、策略复核、备份、执行、验证和事务结果。状态文件 read-modify-write 用同一锁；日志写入处理短写、I/O 异常，关键信息从事务文件可恢复。
- [ ] 同时保留磁盘事实验证和可注入的业务验证：回调返回 True 不得绕过实际结果 hash 校验；False 或抛异常同样恢复。
- [ ] 明确文件事务的提交点。提交前异常恢复原状；提交后报告/通知/普通审计刷新失败报告“变更已完成，附属状态待修复”，禁止为了刷报告而盲目回滚。审计写不出时，durable transaction 必须保留事实与待补写状态。
- [ ] 填写真正的 expected/resulting hash 与用户 reason、recommendation_id、backup_id；resulting_hash 不能塞 backup ID。拒绝、失败和恢复都有可检索记录；不在日志里存密钥或第三方正文。
- [ ] 故障矩阵必须覆盖：第二个目标移动失败；第二次 rename 失败；备份失败；验证 false/异常；磁盘写入失败；锁冲突；审计失败；报告刷新失败；原计划重放。每种检查内容、权限、链接、非目标哨兵、事务状态和审计。
- [ ] 增加真实子进程中断测试：在 fixture 子进程执行关键 rename 后 `os._exit(77)`，父进程调用恢复接口。至少覆盖删除移走首个目标后、更新移走旧目录后、恢复发布首个实体后。异常注入只在测试边界实现，不引入环境变量形式的生产后门。
- [ ] 安全回退选择：无法实现可证明恢复的某种实体类型时，计划阶段明确拒绝该类对象；禁止先动手后给“手工恢复”提示。
- [ ] 完成阶段 A 验收并提交。不得在该阶段未通过时启用新增用户写操作入口。

**阶段 A 硬门槛：** F01–F04 的复现全部变为安全拒绝或完整恢复，F07 的不相关目录不再被 GC 删除；已通过自检的备份在所支持类型上能完整往返；所有中断 fixture 可恢复或明确阻止继续写入；原有回归语义保持。

### Task 4：让盘点完整性和加载范围可解释（F05）

**Files:** 创建 `scripts/core/observations.py`、`scripts/core/clients/load_rules.py`、`tests/test_observation_contract.py`；修改 `scripts/scan.py`、`scripts/core/fingerprint.py`、`scripts/core/clients/base.py`、`scripts/core/clients/common.py`、`scripts/core/clients/accio.py`、`scripts/core/provenance.py`、相关适配器和 `scripts/report.py`。

**Interfaces:**

- `build_inventory(home, data_dir, workspace=None)` 保留现有参数；新 workspace 默认 None，表示不选中任何工作区。
- inventory 增加 `observation = {complete, issues, observed_scope, rule_version}`；每个实例新增 `content_status`，为 complete/unreadable/changed-during-scan；不完整对象不能提供可用于变更的完整 hash。
- 新增 `evaluate_load(instances, locations, client, workspace=None) -> dict`，返回 discovered/eligible/confirmed/unknown 计数、重复集合及 rule_evidence；没有直接加载证据时 confirmed 为未知，不能把 eligible 复制过去。
- 新增 `load_receipt_evidence(home, locations) -> dict`，只返回白名单字段及对应实例证据；位置不明确或无法关联内容时保持候选，不自动升级为官方。
- CLI 统一：0=成功且无关注条件；1=成功但有原先约定的红灯/差异；2=操作失败或观察不完整。黄色是否返回 1 不在本轮扩张，保持原语义并文档化。

- [ ] 增加嵌套依赖用例，运行时不依赖 PyYAML 是否安装：

```python
from scripts.scan import parse_frontmatter, collect_bins

def test_requires_bins_are_not_silently_lost(self):
    text = ("---\nname: fixture\ndescription: fixture\n"
            "metadata:\n  requires:\n"
            "    bins: [definitely-missing-fixture-bin]\n---\nbody\n")
    fields, ok = parse_frontmatter(text)
    bins = []
    collect_bins(fields, bins)
    self.assertTrue(ok)
    self.assertEqual(bins, ["definitely-missing-fixture-bin"])
```

- [ ] 明确确定性 frontmatter 子集：顶层核心标量、多行 description、布尔值、metadata 下 requires.bins 的行内/块列表。合法但不支持的结构给 unsupported 警告，不能静默当作没有依赖；非法/截断 frontmatter 给具体问题。可选 PyYAML 仍只做合法性附加检查，不改变核心字段。
- [ ] 修复 tree_manifest 的排除目录遍历剪枝和 onerror；read/lstat 失败或扫描中文件消失必须有结构化错误，不得将部分树 hash 当完整树。build_inventory 捕获到实例/位置粒度，保留其余发现，同时 observation.complete=false、禁止受影响对象写操作。
- [ ] 将 JSON/文本配置错误上传到观察结果；明确“可选文件未创建”与“已有文件损坏”。`scan --json` 不再固定返回 `need_vet=[]`：从审查状态产生真实结果，未计算时用 null/明确状态。`check_updates` 输入缺失/损坏必须返回 2，不能覆盖已有成功结果为“全都无差异”。
- [ ] 把 load_rules 从 scan 中抽出。区分 global 与每个 workspace 上下文，不能把两个未同时打开项目的同名 Skill 计为全局双载；`.agents`、`.codex`、`.claude`、`.zcode` 分别映射。每条规则附来源标识、核实日期、适用范围；证据不足显示推断。
- [ ] 插件坐标包含 client、marketplace、plugin、version；优先消费可核实的启用/安装回执。没有运行时启用证据时保留现有最大版本启发式作为 eligible 推断，不标“已实际加载”。Haha 关系用 alias 表达，保留已纠正规则并去掉相反的旧注释。
- [ ] 把 Accio 已有安装回执函数接到观察/来源链路，只接受 name/id/official/version/oss 等白名单字段，加入虚构 secret 贯穿测试。相同源内容在受保护和第三方位置同时存在时分别保留实例管理属性，不由任一代表实例决定整个内容组的可变性。
- [ ] 验收矩阵：目录不可读、文件不可读、扫描中删除、损坏配置、无客户端、两个工作区同名、跨 marketplace 同名插件、未知客户端规则、缺 PyYAML、缺依赖命令。HTML/Markdown/JSON 均能看到 incomplete/unknown；不存在真实 Skill 列表写入 fixture。
- [ ] 同步 README 中“发现/推断可加载/确认加载”的口径后提交。此任务不增加全盘残留搜索。

### Task 5：修复审查历史与证据有效性（F06）

**Files:** 创建 `scripts/core/review_state.py`、`tests/test_review_lifecycle.py`；修改 `scripts/core/reviews.py`、`scripts/core/overlap.py`、`scripts/value_review.py`、`scripts/report.py`、`scripts/core/migrations.py`。

**Interfaces:**

- 新增 `review_dependencies(item, alternatives, inventory, policy, reputation) -> dict`：alternatives 为记账 payload 中实际引用的逻辑 ID 列表；只包含目标内容、这些替代品版本/位置适配、相关来源/安检/仓库维护状态及 review_policy_version。
- 新增 `evaluate_review(record, inventory, policy, reputation) -> dict`：返回 status=current/needs-recheck/unreviewed、reason_codes、previous_record；unknown 依赖不返回 current。
- `record_review()` 接受新增显式 payload 字段 `skill_tree_hash`、`review_snapshot_id`、`safety`；其余价值字段保持。新 queue 输出 review_snapshot_id；CLI 记账前核对当前目标磁盘 hash 和依赖快照。
- 兼容历史记录只读显示；缺依赖快照的旧记录可展示原结论但标 needs-recheck，不能自动补齐新字段后假装重新审过。

- [ ] 添加生命周期测试：首次记录 → 内容变更 → 队列和报告都呈现同一条历史结论及 needs-recheck；同名不同内容不串用；代表实例消失但同内容副本仍存在时能显示内容审查历史，同时复核位置适配。
- [ ] 新增错 hash、旧队列、非法 safety、空 reviewer_model、替代品自身/不存在、仅热度删除、无 benchmark 性能断言的拒绝测试。旧 tests 的 fixture 为新必填字段补值，保留原断言。
- [ ] 根据稳定实例 ID 连接版本历史；当前 logical_id 继续表示内容组。查不到当前逻辑 ID 的历史时按实例历史展示，不能只按 name 匹配；链接重定向导致 instance_id 改变时先标新对象，旧对象保留历史，未经证据不得自动合并。
- [ ] 把安全审查与价值判断分开失效：同一内容可复用安全阅读证据，但适配/替代结论必须复核相关环境；过期 safe 不显示当前绿色安全徽章。报告、队列和后续计划的推荐说明调用同一个 evaluate_review。
- [ ] 加入 A/B/C 依赖矩阵：A 的建议依赖 B；B 删除/换内容/适配不再成立 → A 过期；无关 C 内容变化 → A 不过期；来源重新核实与归档等实质维护变化 → 对应记录过期；普通抓取时间或 stars 小变化不触发全量过期。
- [ ] 保留完整旧记录而非覆盖历史。`value_review record` 的读-追加-写全过程持记录锁；并发两次记录都保留且 ID 唯一；读取损坏台账不得默认为空再覆盖。
- [ ] 证据维持可核查引用，若引入结构化字段只用少量类别：local-file、source、coverage、benchmark。校验结构不宣称证明语义正确；候选仍允许为零、不自动建议删除。
- [ ] 下面行为是本任务完成后必须能通过的接口合同，新增 fixture 自行构造，不能把 evaluate_review mock 掉：

```python
state = evaluate_review(record, changed_inventory, policy, reputation)
self.assertEqual(state["status"], "needs-recheck")
self.assertIn("target-content-changed", state["reason_codes"])
self.assertEqual(state["previous_record"]["review_id"], record["review_id"])
```

- [ ] 队列、报告和 CLI 端到端绿后提交，更新新旧记录迁移说明。

### Task 6：保证完整候选与可解释的缓存生命周期（F07）

**Files:** 修改 `scripts/core/github.py`、`scripts/check_updates.py`、`scripts/core/changes.py` 和 Task 2 创建的 `scripts/core/staging.py`；创建 `tests/test_candidate_contract.py`；扩展 `tests/test_check_updates.py`、`tests/test_provenance_github.py`。

**Interfaces:**

- `fetch_skill_tree()` 保持入口；成功额外返回 source_dir、tree_complete=True、source_tree_sha、materialization_version。失败不得返回可使用的 candidate_hash。
- 新增 `collect_staging_references(updates, plans, transactions) -> set`。
- 新增 `cleanup_staging(root, references, ownership, now) -> dict`；只清理本工具登记且无有效引用的候选，返回 kept/removed/refused/errors。
- candidate 与计划保留完整 repo/path/commit/tree hash，service 不得再拼造 `skills/<name>` 源路径。

- [ ] fake runner 返回 truncated=true 时必须失败或逐子树补齐；一期默认明确失败最简单，若实现补齐必须覆盖分页/子树错误并证明没有缺项。不得用缺字段或网络失败跳过内容。
- [ ] 明确支持 Git 100644、100755、120000。symlink blob 保存链接目标字符串，物化时不跟随写入；源树 submodule(160000)、非法路径、重复路径、链接父级冲突、缺根 SKILL.md、无有效 frontmatter 明确拒绝。标准文件权限固定，结果不依赖进程 umask。
- [ ] 明确 source_dir 的归一化：来源为仓库根 SKILL.md 时用空字符串表示根目录，不拼出 `/` 前缀漏掉全部成员；子目录保留精确路径。加入根目录 Skill 和多层目录 Skill 的成功对照。
- [ ] 严格 base64 解码并校验 blob 大小/标识（使用返回协议可验证字段）；限制候选文件数和大小，沿用 Task 1 的保守限额。失败后清理仅本次临时目录，不影响正在使用的固定候选。
- [ ] 对已有同名 cand 目录复核完整 hash，不能仅相信截断 hash 目录名。损坏目录保留故障证据并以独立临时对象重新物化；不得覆盖被计划使用中的对象。
- [ ] 锁住本工具 staging 写入/清理；检查根目录不能位于发现的 Skill 树、项目 data 或安装位置内；拒绝 root/symlink 根和不明所有权目录。默认仍用系统缓存，不能通过环境覆盖把用户技能树当 GC 区域。
- [ ] GC 引用包含本轮/最近结果、未过期计划、执行中/待恢复事务。有效计划引用存活；没有所有权记录的历史目录保留为待清理，不能扫描到一个目录就直接 rmtree。现有“无引用候选清扫”测试补所有权 fixture；不能删掉该行为验证。
- [ ] 缓存状态区分 fetched_at、last_attempt_at、refresh_status；网络失败时保存旧数据与 stale/error 的新状态，报告必须看到失败。一次运行相同 repo/commit/source_dir 只请求/物化一次，不在本任务添加持久 TTL 复杂逻辑。
- [ ] 本地版本号更高只作为提示。没有上次已安装上游基线或明确本地定制证据时状态为 needs-review，不宣称一定是本地定制。展示内容/路径/权限的差异，plan 前复核对应的 local_hash。
- [ ] 协议测试的核心断言：

```python
result = fetch_skill_tree("fixture/demo", "skills/demo", "fixed-sha",
                          destination, truncated_runner)
self.assertFalse(result["ok"])
self.assertNotIn("candidate_hash", result)
```

该 fake runner 只替换网络边界。再用完整 fixture 验证 100755 和 120000 原样落地、候选 hash 与安装后 hash 一致。

- [ ] 验证“两份本地版本共享候选”“网络失败保留旧缓存但标陈旧”“并发检查不会删执行中候选”“恶意 staging 根被拒绝”后提交。

### Task 7：贯通 CLI、API 和报告的实际操作链路（F08、F11）

**Files:** 创建 `scripts/core/runtime.py`、`scripts/core/service.py`、`scripts/manage.py`、`tests/test_manage_cli.py`、`tests/test_workflow_contract.py`；修改 `scripts/serve.py`、`scripts/remove_skill.py`、`scripts/report.py`、`scripts/value_review.py`、`启动技能报告.command`。

**Interfaces:**

- 新增 `RuntimePaths`，显式包含 home、config_dir、data_dir、staging_dir、backup_dir；所有入口从同一解析函数获得，不从全局 BASE 偷换某一项。
- 新增 `publish_snapshot(paths) -> dict`：在同一状态锁下生成 inventory 与报告，提供 snapshot_id 和完整性状态；配置/报告错误保留旧快照并显式标旧，不发布半套新数据。
- 新增 `plan_action(action, payload, paths) -> dict`、`apply_action(plan_id, digest, confirm, paths, accept_warning=False) -> dict`。调用 Task 1–3，引擎是唯一的资产写入口。返回 transaction_status、snapshot_status、snapshot_id、backup_id、message。
- backup 行统一 `{backup_id, filename, path, verification_status}`；对外 API 使用 backup_id，不传文件名冒充 ID。
- 新增 CLI（本任务创建，当前不存在）：`manage.py plan remove/update/restore`、`manage.py vet`、`manage.py apply`、`manage.py status`、`manage.py recover`、`manage.py rescan`。每项支持 `--json` 与显式 runtime 路径；旧 remove_skill 命令作为兼容包装器保留。

- [ ] 先加入报告生成→读取真实按钮字段→API 的 restore 测试，禁止测试手写另一个正确 backup_id 绕过实际报告。当前 backup 文件名重复拼接错误必须复现为红。
- [ ] 加入 API remove→返回→GET report 端到端测试：目标行消失/标移除，备份列表可选，snapshot_id 改变；若报告生成失败，响应必须是“事务已提交，报告未更新”的可区分状态。不得声称物理变更失败，也不能返回普通成功后静默展示旧报告。
- [ ] 实现共享 RuntimePaths 与 service；CLI 默认行为兼容，设置 SKILL_KEEPER_DATA 时 CLI/API/报告/备份必须落到一致隔离路径。rescan 使用注入的 home/config/data，禁止子进程回到真实 HOME。保留缺省配置路径的兼容读取，避免数据迁移先于代码支持。
- [ ] CLI 的具体合同：

```text
manage.py plan remove --instance-id ID --reason TEXT --json
manage.py plan update --instance-id ID --json
manage.py plan restore --backup-id ID --json
manage.py vet --plan-id ID --candidate-hash HASH --file EVIDENCE_JSON --json
manage.py apply PLAN_ID --digest HASH --confirm [--accept-warning] --json
manage.py status PLAN_ID --json
manage.py recover PLAN_ID --json
manage.py rescan --json
```

`vet` 的 evidence JSON 包含 verdict、evidence、reviewer_model；只有 Agent/用户明确提交才记账，网页点击不能自动产生 safe。recover 只恢复已授权事务的原状态，显示计划和恢复结果，不能改变目标或激活候选。

- [ ] 报告提供 update 分支：展示目标实例、影响的客户端/链接、来源和固定 commit、完整树差异摘要、候选安检、用户理由、备份策略和过期时间；用户看完计划后一次明确确认，warning 额外一次风险确认。保留严格布尔、token、Origin、64 KiB 限制，不添加“生成计划前”无信息的重复确认。
- [ ] 静态模式生成实际可运行的命令：运行时用解析后的绝对项目路径配 shlex.join；公开虚构示例采用显式占位说明，不能冒充可直接执行。需要可移植时先 shell 展开路径再引号保护，不输出被整体引号包住的 `~`。restore/update 都有对应非空命令。
- [ ] 报告当前/历史/未审查/无法检查/更新候选过期状态来自 Task 4–6 的统一状态，不复制判断逻辑。保留资产/关注/价值三组导航，展示受影响实例列表，按逻辑内容查看但按实例管理。
- [ ] groups.json 恢复为用户分类筛选维度，不改变价值结论和安全状态。给未知分类默认组；分组修改只读配置并重建视图，不修改 Skill。
- [ ] 统一读改写锁：review store、ignore、快照发布各自明确归属；不要持有未知顺序的嵌套锁。建议同 data_dir 一个状态锁，staging 单独锁，顺序固定为状态锁→staging 锁；网络准备可在锁外，发布前重验引用。忽略规则不删除 finding，只改 ignored。
- [ ] HTTP 边界补负 Content-Length、非 JSON 对象、未知路由、Unicode 非法 token、请求超时与服务关闭测试；使用常量时间字节比较，错误不泄露路径/密钥。CSP 若保留 hash fallback，使用 base64 SHA-256，不用 hex；外置脚本主链路继续保留。
- [ ] 用一个临时安装树跑 CLI 删除/恢复、API 删除/恢复、CLI 候选安检→API 更新，以及 warning 拒绝/二次确认。每条比较实体状态、审计、计划参数和新报告，不只检查 HTTP 200。
- [ ] 如果执行环境有浏览器能力，用虚构报告实际点击静态复制、锚点、更新/恢复、取消和失败提示；遵循本机 browser skill。若无浏览器，记录未验的 UI 项，不以“检查 JS 字符串”声称浏览器验证通过。
- [ ] 阶段 B 验收通过后提交，重写 README/SKILL 操作示例为实际存在的入口。

**阶段 B 硬门槛：** 三种操作在 CLI/API 合同下均可跑通，页面参数真实传到后端；操作状态与页面快照一致；未知与失败可见；审查历史和失效原因一致。真实 HOME 与现有个人配置没有写入。

### Task 8：去除重复计算，提供外部运行态路径（F09、F10）

**Files:** 修改 `scripts/core/overlap.py`、`scripts/core/reviews.py`、`scripts/scan.py`、`scripts/core/runtime.py`、`scripts/core/migrations.py`；创建 `tests/test_overlap_cost.py`、`tests/test_runtime_paths.py`、`tests/fixtures/overlap-baseline.json`。

**Interfaces:**

- 新增 `build_overlap_index(inventory) -> dict`：包含一次生成的 status_map、tokens_by_logical、document_frequency、pairs_by_ids、duplicates_by_hash。
- `candidate_pairs(inventory, min_similarity=0.32, index=None)`、`alternative_candidates(inventory, target_logical_id, min_similarity=0.32, max_candidates=8, index=None)` 保留默认行为并接受复用 index；queue 构建一次传入所有消费者。
- 新增 `plan_runtime_migration(old_paths, new_paths) -> dict`：只输出文件清单、hash、冲突和建议，不复制/删除真实数据；包含 schema_version。
- RuntimePaths 增加明确外部配置/运行态支持；沿用 SKILL_KEEPER_DATA、SKILL_KEEPER_STAGING，并补显式 config/backup 参数，统一优先级为 CLI > 环境 > 兼容默认。

- [ ] 在改算法前，用固定虚构 A/B/C 等内容冻结候选结果：包含相似度分项、排序、稀有词门槛、同源版本排除、最多 8 个与零候选。冻结文件不能含机器路径/时间，记录生成所用基线。后续不得修改该 gold fixture 使优化“等价”。
- [ ] 新增结构性性能测试，不只测试秒数：

```python
from unittest.mock import patch
from scripts.core import overlap
from scripts.core.reviews import build_review_queue

# inv 是本测试用 write_skill + build_inventory 创建的 80 个独立虚构 Skill。
with patch.object(overlap, "read_head", wraps=overlap.read_head) as reads:
    with patch.object(overlap, "pair_breakdown",
                      wraps=overlap.pair_breakdown) as pairs:
        queue = build_review_queue(inv)
self.assertLessEqual(reads.call_count, 80)
self.assertLessEqual(pairs.call_count, 80 * 79 // 2)
self.assertEqual(len(queue["items"]), 80)
```

该断言对当前实现为红（12,800 次读取、259,120 次打分）。后续代码若换了计数点，必须保留测量实际读取和实际打分的观测，不得通过删函数调用/返回空候选作弊。

- [ ] 用一次语料读取、一次词频统计、每对一次评分替换循环中重建；目标候选仅从索引筛选。代表实例、来源规则与排序保持等价，不趁机调整权重/阈值。
- [ ] 同一扫描周期可以按真实路径复用内容读取，但要核对 inode/mtime/size 等观察稳定性，出现变化标 changed-during-scan。计划/apply 不信任扫描缓存，仍复算完整目标和候选 hash。禁止加跨进程持久 hash 缓存来绕过内容安检。
- [ ] 记录 20/40/80 和一个更大虚构样本的次数与耗时；强制门槛是读取 O(N)、打分 O(N²) 和结果等价，壁钟时间辅助报告，不把 0.64 秒真实扫描当必须优化的瓶颈。
- [ ] 外部运行态方案仅增加可选路径与新安装建议，不自动迁移已有用户。fixture 验证配置/快照/台账/备份写到 Skill 树外，连续扫描和报告生成不会仅因运行态输出改变被扫描 Skill 的 tree_hash。
- [ ] 为旧 data_dir 提供迁移预演：逐文件列相对名、旧/新目录标识、hash、冲突；已有目标不覆盖；返回整体可迁移性和下一步说明。用户真实迁移仍须新计划与确认，不在本优化任务执行。
- [ ] 不用全局排除 data/backups/.git 修性能。`.git` 元数据进入本项目开发 checkout 指纹的问题在文档保留为限制；普通第三方完整指纹不变。本任务不创建通用打包协议或改共享库链接。
- [ ] 全量测试与 frozen gold 通过后提交，记录资源读取下降的实测结果。

### Task 9：固化验收入口与当前能力文档（F11）

**Files:** 创建 `scripts/verify.py`、`docs/architecture.md`、`docs/changes.md`；修改 `.gitignore`、`README.md`、`SKILL.md`、`AGENTS.md`、`tests/helpers.py`、`tests/test_serve_api.py`、`tests/test_migrations_docs.py`；重新生成固定虚构示例。

**Interfaces:**

- 新增 `python3 scripts/verify.py`（本任务创建）：使用 unittest discovery、检查发现测试非零且不少于基线、skipped=0、报告实际失败，退出码 0/非0。不包装成永远返回成功的 shell 脚本。
- verifier 支持 `--test-dir DIR` 供虚构测试集反向验证；默认 tests，验收不能把默认值改到空目录。输出 test_count、skipped、failure/error 数和实际执行状态。
- docs/architecture 记录现行规则及入口，docs/changes 保存历史；AGENTS 保留必要规则和文档链接，避免每次任务加载完整个人运维流水。

- [ ] 修补 `.gitignore`：至少覆盖 `data/client-locations.json`、`data/inventory*.json`、新增 transactions/状态临时产物；现有 example 配置和虚构 fixture 仍可跟踪。不要把整个 data/ 忽略掉导致示例也被遮蔽。
- [ ] 关闭 check_updates 未关闭文件句柄，给 HTTP 测试的 shutdown 配套 server_close 和 thread join；生产服务退出也关闭 socket。保留每个安全响应验证，不能以取消测试消除警告。
- [ ] verifier 直接使用 unittest 实际结果。反向验收在临时测试目录放一个会失败的 TestCase：verify 返回非0；改为通过再返回0。自定义目录结果标记 is_smoke=true，仅验证失败传播，不计作最终验收；默认库才执行126项基线门槛。不得加入降低默认门槛的参数。
- [ ] 另一个反向验收使用只有 skipped 用例的临时目录，必须非0；空目录也非0。保留主基线 126 个测试 ID 的语义覆盖，记录被计划允许调整的 fixture/预期及原因。
- [ ] 创建当前架构说明：路径解析、发现/加载规则、策略矩阵、审查依赖、事务提交点、恢复与残留处理、JSON 退出码、锁顺序、候选清理边界。历史事实迁入 changes 并链接，不覆盖原始设计文档为“全已实现”。
- [ ] README/SKILL 只写可运行入口，更新准确版本字段与依赖/平台范围。分开受保护、安全、价值结论、是否可更新；删除“网页已更新”但无实现的说法。保持用户用语简洁，不在按钮上显示无助决策的内部类名。
- [ ] 用固定虚构 fixture 重新生成 examples/report-sample.html；检查资产/关注/价值总数一致，嵌套导航、空状态、过期状态、备份列表和命令入口能解释。不得从个人 inventory 脱敏生成例子。
- [ ] 提交前运行新 verifier、全量 unittest、现有/新增 CLI help，并检查 Git diff 与个人路径泄漏。只读真实扫描使用临时输出，报告结果差异有原因说明；不以实测本机总数固定为测试标准。
- [ ] 在 PROGRESS 写最终摘要、所有验收输出位置、未验证环境，BLOCKED 无则写“无”。功能任务未通过时不以文档完成宣告全项目完成。

## 最终验收矩阵

| 承诺 | 必须观测的结果 | 主要测试 |
|---|---|---|
| 路径受限 | manifest/父链接/候选越界输入被拒，登记根外无新文件 | test_backup_contract、test_change_policy、test_candidate_contract |
| 备份可往返 | 正文、二进制、内部/顶层链接、根/子目录权限、实体集合一致 | test_backup_contract、test_backup_restore |
| 失败可恢复 | 失败注入与子进程中断后回到原状或明确 recovery-required，不留“成功”假状态 | test_change_faults、test_transaction_recovery |
| 确认对象一致 | 归档/候选/策略/路径变化拒绝旧计划，计划重放无第二次物理变更 | test_change_policy、test_transaction_recovery |
| 事实有边界 | 部分扫描、损坏配置、未知加载规则、缺依赖可见且非假绿 | test_observation_contract |
| 建议仍有效 | 内容/替代依赖变化使相关结论过期，历史保留，无关变化不拖累全库 | test_review_lifecycle |
| 实际操作闭环 | 页面真实字段→API→磁盘→审计→新快照一致，CLI 使用相同上下文 | test_workflow_contract、test_manage_cli、test_serve_api |
| 性能有证据 | frozen gold 等价；80 个独立 Skill 正文读取≤80、打分≤3,160 | test_overlap_cost |
| 不污染加载树 | 外部运行态 fixture 连续生成不改变 Skill 内容 hash | test_runtime_paths |
| 验收器可信 | 故意失败、skip、空发现均返回非0；正常全库通过 | verify 自验证 + unittest |

新增测试文件名在相应任务创建，完整测试入口仍是：

```bash
python3 -m unittest discover -s tests
```

完成后才运行本计划新增入口：

```bash
python3 scripts/verify.py
python3 scripts/manage.py --help
```

## 完成条件

1. **结果指标：** F01–F08 的每个已复现失败转为安全结果，阶段 A/B/C 所需行为都有实际输出证据；F09 算法次数与结果等价达标，F10 完成隔离路径及迁移预演，F11 文档/忽略/自验证闭环。不能只按修复数量宣布达标。
2. **边界指标：** 原有 126 项风险覆盖不退化、0 skipped；真实 Skill、插件缓存、个人配置/台账/备份没有被本任务修改；无个人数据入提交、无未经授权的外部操作。新增功能不可验证则列明，不包装成全完成。

交付时贴实际测试摘要、至少一组关键红→绿反向证据、事务中断恢复证据、性能计数前后值、Git 变更范围和 BLOCKED。只说“所有测试通过”不足以验收。若某个目标被环境阻挡，清楚报告哪些已完成、哪些还未达标，不以预算或轮数耗尽代替完成。

## 计划自检记录（制定时）

- F01/F02 → Task 1；F04/F07清理边界 → Task 2；F03 → Task 3；F05 → Task 4；F06 → Task 5；F07协议与生命周期 → Task 6；F08 → Task 7；F09/F10 → Task 8；F11 → Task 7/9。
- 没有要求更换现有技术栈、擅自迁移真实数据或执行真实 Skill 删除。
- 区分了当前已实跑命令、未来新增命令、协议 fixture 与真实环境测量。
- 现有测试允许新增和补齐输入，不允许削弱风险语义；明确列出了与旧错误预期冲突时的调整规则。
- 本计划只定义工作，不宣称这些函数、测试或命令目前存在。
