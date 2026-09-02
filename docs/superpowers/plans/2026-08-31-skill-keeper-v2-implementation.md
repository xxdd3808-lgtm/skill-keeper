# skill-keeper v2 Implementation Plan

> **状态：已完成（2026-09-02）。** 原计划中的实现、测试和真实环境只读验收已完成；后续报告导航与严格 CSP 兼容修复记录在后续提交中。现役命令和约束以项目根 `AGENTS.md`、`README.md`、`SKILL.md` 与当前代码为准，本文件保留为历史执行记录。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把现有盘点器一次升级为覆盖 ZCode、Codex、Accio Work、WorkBuddy（用户称 Workbody）、Claude Code、Claude Code Haha、Cindy 等客户端的安全 Skill 管家，并为所有非自建、非客户端自带的第三方 Skill 生成有 GitHub/市场证据、重复与替代分析的大模型价值建议。

**Architecture:** 将位置发现、完整指纹、来源证据、GitHub 数据、重复候选、价值审查、安全备份和变更执行拆成独立核心模块；现有 CLI 和 HTML 报告只调用这些模块。确定性代码收集事实并生成审查队列，大模型在 Skill 工作流中综合判断保留或删除价值；任何实际变更都经过不可变计划、确认、互斥锁、可验证备份、原子执行和失败回滚。

**Tech Stack:** Python 3.8+ 标准库、`unittest`、本机 `gh` CLI（可选联网能力）、HTML/CSS/原生 JavaScript；运行时不新增第三方依赖。

**Spec:** `docs/superpowers/specs/2026-08-31-skill-keeper-v2-design.md`

## Global Constraints

- 开工前完整阅读 `AGENTS.md`、本计划和设计规格；保留用户已有未提交改动，不重置或覆盖无关文件。
- 运行时保持 Python 3.8+ 标准库；PyYAML 可用于额外 YAML 合法性校验，但其存在与否不得改变 name、description、version、依赖命令等核心扫描结果。
- 所有自动测试使用临时 HOME；禁止对用户真实 Skill 执行删除、更新、恢复或安检记账。
- ZCode、Codex、Accio Work、WorkBuddy、Claude Code、Claude Code Haha、Cindy 等客户端管理的系统/插件缓存全部只读。
- 客户端配置只按字段白名单读取；任何输出、日志、快照和测试失败信息都不得出现 token、key、cookie、authorization、secret 或 env 值。
- GitHub 星数、fork 和活跃度只作为证据，不能冒充真实使用人数，也不能单独触发“建议删除”。
- 大模型可综合多方面作出“建议删除”，但必须保存理由、替代品、删除损失、证据和置信度；系统永不自动删除。
- 变更目标只能来自当前 inventory 的稳定 instance ID；所有写操作必须有不可变计划、用户确认、备份、审计和失败回滚。
- 所有 JSON 状态文件带 `schema_version` 并原子写入；运行时数据、个人路径、缓存、审查结果和备份继续保持 gitignored。
- 每个任务先写失败测试，再实现，再跑该任务测试和全量测试；每项独立提交，提交信息使用英文 Conventional Commit。
- 单个任务失败时停止在该任务，保留已通过测试的前序提交；不要用跳过测试、放宽断言或删除功能的方式让流水线变绿。

---

## File Map

新增核心模块：

- `scripts/core/models.py`：稳定数据模型、schema 版本和 JSON 转换。
- `scripts/core/io.py`：安全 JSON 读取、原子写入、字段白名单脱敏和文件锁。
- `scripts/core/fingerprint.py`：不跟随外部链接的完整目录 manifest 与 SHA-256。
- `scripts/core/clients/base.py`：客户端适配器接口、Location 和发现注册表。
- `scripts/core/clients/common.py`：共享、工作区、Claude Code 与 Haha 适配。
- `scripts/core/clients/zcode.py`、`codex.py`、`accio.py`、`workbuddy.py`、`cindy.py`：客户端专属发现和官方/插件证据。
- `scripts/core/provenance.py`：来源证据合并、置信度和第三方/受保护分类。
- `scripts/core/github.py`：GitHub 来源候选、repo 快照、commit 和缓存。
- `scripts/core/overlap.py`：精确重复和语义审查候选生成。
- `scripts/core/reviews.py`：价值审查队列、记录校验和过期判断。
- `scripts/core/backup.py`：带 manifest 的唯一归档、验证和安全恢复。
- `scripts/core/changes.py`：ChangePlan、路径约束、删除/恢复/更新事务和回滚。
- `scripts/core/audit.py`：成功、失败和回滚审计。
- `scripts/value_review.py`：供大模型工作流使用的审查队列/记账 CLI。

重构现有入口：

- `scripts/scan.py`：调用客户端适配器和 inventory v2 聚合。
- `scripts/check_updates.py`：固定来源快照，比较完整候选树。
- `scripts/remove_skill.py`：仅生成/执行安全 ChangePlan，废止任意目录直接删除。
- `scripts/serve.py`：两阶段 plan/apply API、严格确认和互斥操作。
- `scripts/report.py`：普通人价值报告、安全操作摘要和过期提示。
- `scripts/make_sample_report.py`：只读取固定虚构 fixture。

测试与文档：

- `tests/helpers.py` 和 `tests/test_*.py`：临时 HOME、客户端 fixture 和全链路测试。
- `examples/fixtures/inventory-v2.json`：固定虚构报告数据。
- `.gitignore`、`README.md`、`SKILL.md`、`AGENTS.md`、`data/*.example.*`：v2 行为和配置说明。

---

### Task 1: 建立安全 I/O、数据模型和测试底座

**Files:**
- Create: `scripts/core/__init__.py`
- Create: `scripts/core/models.py`
- Create: `scripts/core/io.py`
- Create: `tests/__init__.py`
- Create: `tests/helpers.py`
- Create: `tests/test_io_models.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `Location`, `SkillInstance`, `ChangePlan`, `SCHEMA_VERSION = 2`
- Produces: `load_json_checked(path, default)`, `atomic_write_json(path, value)`, `read_json_fields(path, allowed)`, `FileLock(path)`
- Consumes: Python 标准库 `dataclasses`, `json`, `pathlib`, `tempfile`, `os`

- [x] **Step 1: 写失败测试，固定原子写入、脱敏读取和模型往返要求**

```python
# tests/test_io_models.py
import json, tempfile, unittest
from pathlib import Path
from scripts.core.io import atomic_write_json, read_json_fields
from scripts.core.models import Location, SCHEMA_VERSION

class IoModelTests(unittest.TestCase):
    def test_atomic_json_and_field_allowlist(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "settings.json"
            p.write_text(json.dumps({"enabled": True, "token": "DO-NOT-LEAK"}), encoding="utf-8")
            self.assertEqual(read_json_fields(p, {"enabled"}), {"enabled": True})
            atomic_write_json(p, {"schema_version": SCHEMA_VERSION, "ok": True})
            self.assertEqual(json.loads(p.read_text(encoding="utf-8"))["schema_version"], 2)
            self.assertNotIn("DO-NOT-LEAK", p.read_text(encoding="utf-8"))

    def test_location_round_trip(self):
        loc = Location("shared", "shared", "/tmp/home/.agents/skills", "user", True, ("configured",), ("haha",))
        self.assertEqual(Location.from_dict(loc.to_dict()), loc)
```

- [x] **Step 2: 运行测试并确认因核心模块不存在而失败**

Run: `python3 -m unittest tests.test_io_models -v`

Expected: `ModuleNotFoundError: No module named 'scripts.core'`

- [x] **Step 3: 实现明确的数据模型、原子 JSON 写和白名单读取**

```python
# scripts/core/models.py 的公共接口
from dataclasses import asdict, dataclass

SCHEMA_VERSION = 2

@dataclass(frozen=True)
class Location:
    location_id: str
    client: str
    path: str
    kind: str
    mutable: bool
    evidence: tuple
    aliases: tuple = ()
    def to_dict(self):
        row = asdict(self)
        row["evidence"] = list(self.evidence)
        row["aliases"] = list(self.aliases)
        return row
    @classmethod
    def from_dict(cls, row):
        data = dict(row)
        data["evidence"] = tuple(data.get("evidence", ()))
        data["aliases"] = tuple(data.get("aliases", ()))
        return cls(**data)

@dataclass(frozen=True)
class SkillInstance:
    instance_id: str
    location_id: str
    directory_name: str
    path: str
    real_path: str
    logical_name: str
    tree_hash: str
    mutable: bool
    client: str
    kind: str
    evidence: tuple

@dataclass(frozen=True)
class ChangePlan:
    plan_id: str
    action: str
    target_ids: tuple
    preconditions: tuple
    summary: str
    digest: str
    created_at: str
    expires_at: str
```

`atomic_write_json` 必须在目标同目录创建临时文件，写入后 `flush()`、`os.fsync()`，最后 `os.replace()`；异常时清理临时文件。`read_json_fields` 只返回 allowed 集合中的顶层字段，禁止把未知值放进错误信息。`load_json_checked` 返回 `(value, issues)`，损坏文件产生结构化 issue 而不是静默使用空值。

- [x] **Step 4: 运行任务测试和全量测试**

Run: `python3 -m unittest tests.test_io_models -v && python3 -m unittest discover -s tests -v`

Expected: 所有测试 `OK`，项目文件中不存在临时 `.tmp` 文件。

- [x] **Step 5: 更新 gitignore 并提交**

在 `.gitignore` 增加：

```gitignore
data/reputation.json
data/review-queue.json
data/value-reviews.json
data/change-plans/
data/staging/
data/.change.lock
data/audit-v2.jsonl
data/migrations/
```

Run:

```bash
git add .gitignore scripts/core tests
git commit -m "feat: add v2 core models and safe state IO"
```

---

### Task 2: 实现完整目录指纹和稳定实例身份

**Files:**
- Create: `scripts/core/fingerprint.py`
- Create: `tests/test_fingerprint.py`

**Interfaces:**
- Produces: `tree_manifest(root: Path) -> list[dict]`
- Produces: `tree_hash(root: Path) -> str`
- Produces: `instance_id(location_id: str, directory_name: str, real_path: str) -> str`
- Consumes: `atomic_write_json` 仅用于测试快照，不在指纹函数中写状态

- [x] **Step 1: 写失败测试，证明辅助脚本和链接变化都会改变指纹**

```python
# tests/test_fingerprint.py
import os, tempfile, unittest
from pathlib import Path
from scripts.core.fingerprint import tree_hash, tree_manifest

class FingerprintTests(unittest.TestCase):
    def test_auxiliary_content_changes_hash(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); (root / "SKILL.md").write_text("---\nname: x\ndescription: x\n---\n", encoding="utf-8")
            (root / "run.py").write_text("safe", encoding="utf-8")
            before = tree_hash(root)
            (root / "run.py").write_text("changed", encoding="utf-8")
            self.assertNotEqual(before, tree_hash(root))

    def test_symlink_is_hashed_without_following_target(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "skill"; outside = Path(td) / "outside"
            root.mkdir(); outside.write_text("secret", encoding="utf-8")
            os.symlink(outside, root / "link")
            rows = tree_manifest(root)
            self.assertEqual(rows[0]["type"], "symlink")
            self.assertNotIn("secret", str(rows))
```

- [x] **Step 2: 运行测试确认旧 `sk_signature` 无法满足要求**

Run: `python3 -m unittest tests.test_fingerprint -v`

Expected: import 或断言失败；不得修改现有 `scan.py` 来绕过测试。

- [x] **Step 3: 实现确定性 tree manifest**

`tree_manifest` 按 UTF-8 字节序排序相对路径，记录 `path/type/mode/sha256`；符号链接只记录 `target`，不读取目标内容；拒绝 root 不是目录。默认只排除 `.DS_Store`、`__pycache__`、`*.pyc`，排除列表作为模块常量写入 manifest 版本。`tree_hash` 对规范化 JSON 使用 SHA-256，不截断摘要。`instance_id` 对三个输入的规范化字符串 SHA-256，显示前 20 位但保存完整输入证据。

- [x] **Step 4: 运行指纹和全量测试**

Run: `python3 -m unittest tests.test_fingerprint -v && python3 -m unittest discover -s tests -v`

Expected: 内容、权限、相对路径或链接目标变化均改变摘要；外部链接目标内容不被读取。

- [x] **Step 5: 提交**

```bash
git add scripts/core/fingerprint.py tests/test_fingerprint.py
git commit -m "feat: fingerprint complete skill trees"
```

---

### Task 3: 建立多客户端发现适配器和加载拓扑

**Files:**
- Create: `scripts/core/clients/__init__.py`
- Create: `scripts/core/clients/base.py`
- Create: `scripts/core/clients/common.py`
- Create: `scripts/core/clients/zcode.py`
- Create: `scripts/core/clients/codex.py`
- Create: `scripts/core/clients/accio.py`
- Create: `scripts/core/clients/workbuddy.py`
- Create: `scripts/core/clients/cindy.py`
- Create: `tests/test_client_discovery.py`

**Interfaces:**
- Produces: `discover_locations(home: Path, data_dir: Path) -> list[Location]`
- Produces: `discover_skill_roots(location: Location) -> list[Path]`
- Produces: `client_load_aliases(home: Path) -> Mapping[str, Sequence[str]]`
- Consumes: `Location`, `read_json_fields`

- [x] **Step 1: 写包含七类客户端、市场目录和虚构 secret 的失败 fixture 测试**

```python
# tests/test_client_discovery.py（核心断言）
class ClientDiscoveryTests(unittest.TestCase):
    def test_clients_marketplaces_aliases_and_secrets(self):
        home = build_multi_client_home(self)
        rows = discover_locations(home, home / "project-data")
        ids = {x.location_id for x in rows}
        self.assertTrue({"shared", "claude-user", "codex-user", "accio-account-a", "workbuddy-user", "cindy-codex-home"} <= ids)
        self.assertFalse(any("skills-marketplace" in x.path or "connectors-marketplace" in x.path for x in rows if x.kind == "user"))
        haha = next(x for x in rows if x.location_id == "claude-user")
        self.assertIn("haha", haha.aliases)
        rendered = json.dumps([x.to_dict() for x in rows])
        self.assertNotIn("FAKE-SECRET-123", rendered)

    def test_builtin_and_plugin_cache_locations_are_immutable(self):
        rows = discover_locations(build_multi_client_home(self), Path("/tmp/data"))
        self.assertTrue(all(not x.mutable for x in rows if x.kind in {"builtin", "plugin-cache"}))
```

`tests/helpers.py` 新增 `write_skill(root, name, description)` 和 `build_multi_client_home(testcase)`；fixture 必须覆盖：ZCode plugin cache、Codex `.system`/plugin cache、Accio account official cache、WorkBuddy user/connector/plugin/marketplace、Claude user/plugin marketplace、Haha wrapper、Cindy codex-home/system/plugin projection。

- [x] **Step 2: 运行测试确认现有硬编码 LOCATIONS 无法发现这些客户端**

Run: `python3 -m unittest tests.test_client_discovery -v`

Expected: `discover_locations` 缺失或多项 location ID 缺失。

- [x] **Step 3: 实现适配器注册表和严格的“已安装”判断**

```python
# scripts/core/clients/base.py
class ClientAdapter:
    name = ""
    def discover(self, home: Path, data_dir: Path):
        raise NotImplementedError

ADAPTERS = (CommonAdapter(), ZCodeAdapter(), CodexAdapter(), AccioAdapter(), WorkBuddyAdapter(), CindyAdapter())

def discover_locations(home, data_dir):
    rows = [row for adapter in ADAPTERS for row in adapter.discover(Path(home), Path(data_dir))]
    return sorted(dedupe_locations(rows), key=lambda x: x.location_id)
```

具体规则：

- WorkBuddy 只把 `~/.workbuddy/skills`、`connectors/skills` 和已安装 plugin cache 作为加载位置；marketplace 只生成来源证据，不生成已安装实例。
- Claude Code 扫描 `~/.claude/skills` 和已安装 plugin cache，不把 marketplace checkout 当加载实例。
- Haha 存在启动器或 `~/.claude/cc-haha` 时，为 Claude/shared Location 添加 alias `haha`，不创建重复物理位置；只读取配置文件是否存在，不读取 env。
- Cindy 扫描 `~/Library/Application Support/Cindy/codex-home/skills` 和已安装 plugins；`.system` 与投影缓存标为不可变，真实路径相同则去重。
- Accio 遍历 `~/.accio/accounts/*/skills`，账号目录名只用于生成哈希 ID，不输出原账号值；读取 remote cache 仅允许 `name/id/official/version/oss`。
- 所有 adapter 不读取设置中的 `env/token/key/cookie/authorization` 字段。

- [x] **Step 4: 运行客户端发现和 secret 泄漏测试**

Run: `python3 -m unittest tests.test_client_discovery -v && python3 -m unittest discover -s tests -v`

Expected: 七类客户端均被识别，marketplace 未安装内容不计数，Haha 不重复计数，输出不含 `FAKE-SECRET-123`。

- [x] **Step 5: 提交**

```bash
git add scripts/core/clients tests/helpers.py tests/test_client_discovery.py
git commit -m "feat: discover skills across supported clients"
```

---

### Task 4: 重构扫描器为 inventory v2 并修复健康漏报

**Files:**
- Modify: `scripts/scan.py`
- Create: `tests/test_scan_v2.py`
- Create: `data/client-locations.example.json`

**Interfaces:**
- Consumes: `discover_locations`, `tree_hash`, `instance_id`, `load_json_checked`, `atomic_write_json`
- Produces: `build_inventory(home: Path, data_dir: Path) -> dict`
- Produces: inventory `schema_version=2`、`locations`、`instances`、`logical_skills`、`config_issues`

- [x] **Step 1: 写失败测试，覆盖重复/漂移、工作区、稳定 ID 和可变性**

```python
# tests/test_scan_v2.py
class ScanV2Tests(unittest.TestCase):
    def test_duplicate_and_drift_are_health_findings(self):
        home, data = build_duplicate_home(self)
        inv = build_inventory(home, data)
        findings = [x["code"] for x in inv["findings"]]
        self.assertIn("duplicate-load", findings)
        self.assertIn("link-drift", findings)

    def test_auxiliary_change_invalidates_instance_hash(self):
        home, data = build_one_skill_home(self)
        before = build_inventory(home, data)["instances"][0]["tree_hash"]
        (home / ".agents/skills/demo/run.py").write_text("changed", encoding="utf-8")
        after = build_inventory(home, data)["instances"][0]["tree_hash"]
        self.assertNotEqual(before, after)

    def test_client_cache_instances_are_not_mutable(self):
        inv = build_inventory(*build_multi_client_paths(self))
        self.assertTrue(all(not x["mutable"] for x in inv["instances"] if x["kind"] in {"builtin", "plugin-cache"}))
```

- [x] **Step 2: 运行测试确认旧聚合器按 name 合并且漏掉追加问题**

Run: `python3 -m unittest tests.test_scan_v2 -v`

Expected: inventory schema、findings code 或完整 tree hash 断言失败。

- [x] **Step 3: 实现 v2 聚合并保留 CLI 兼容输出**

`build_inventory` 必须先构建 Location，再构建 SkillInstance，再依据已核实来源或内容身份构建 logical skill。重复加载和链接漂移先生成结构化 finding，再统一应用 ignore 规则；不得在 ignore 分类后追加丢失。frontmatter 核心字段始终由项目自带确定性解析器提取，PyYAML 只追加 `yaml-validation` finding。CLI 从 `SKILL_KEEPER_DATA` 读取可选数据目录覆盖，未设置时才使用项目 `data/`；测试覆盖目录不得反向写入真实项目数据。

写 `inventory.json` 前验证所有 `instance_id` 唯一、每个 mutable instance 的 path 属于已登记 mutable location、所有输出通过 secret key/value 过滤。`--json` 保留退出码含义，但加入 `operational_ok` 和 `health_status`，供服务区分“运行失败”和“发现红灯”。

- [x] **Step 4: 运行扫描测试和临时 HOME CLI 测试**

Run:

```bash
python3 -m unittest tests.test_scan_v2 -v
env HOME=/tmp/skill-keeper-v2-empty-home SKILL_KEEPER_DATA=/tmp/skill-keeper-v2-empty-data python3 scripts/scan.py --json
python3 -m unittest discover -s tests -v
```

Expected: 空 HOME 生成 `total=0` 且 `operational_ok=true`；全量测试通过。

- [x] **Step 5: 提交**

```bash
git add scripts/scan.py tests/test_scan_v2.py data/client-locations.example.json
git commit -m "feat: build schema v2 skill inventory"
```

---

### Task 5: 实现可信来源、GitHub 热度和不可变快照

**Files:**
- Create: `scripts/core/provenance.py`
- Create: `scripts/core/github.py`
- Modify: `scripts/check_updates.py`
- Create: `tests/test_provenance_github.py`
- Create: `tests/fixtures/inventory-value.json`

**Interfaces:**
- Produces: `classify_provenance(instance, receipts, known_sources) -> dict`
- Produces: `search_source_candidates(skill, gh_runner) -> list[dict]`
- Produces: `repo_snapshot(repo, gh_runner) -> dict`
- Produces: `fetch_skill_tree(repo, source_dir, commit_sha, dest, gh_runner) -> dict`
- Consumes: inventory v2、完整 tree hash、原子 JSON I/O

- [x] **Step 1: 写失败测试，禁止名称伪装和单文件/文本模式更新**

```python
# tests/test_provenance_github.py
class ProvenanceGithubTests(unittest.TestCase):
    def test_prefix_and_frontmatter_name_do_not_grant_builtin_or_self_built(self):
        row = fake_instance(directory="autoglm-untrusted", logical_name="trusted self skill")
        result = classify_provenance(row, receipts={}, known_sources={"trusted-dir": {"type": "self-built"}})
        self.assertEqual(result["class"], "third-party")

    def test_repo_snapshot_labels_repo_level_popularity(self):
        snap = repo_snapshot("owner/multi-skills", FakeGh.multi_skill_repo())
        self.assertEqual(snap["stars"], 1200)
        self.assertEqual(snap["popularity_scope"], "repository")
        self.assertNotIn("users", snap)

    def test_binary_and_nested_files_are_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            result = fetch_skill_tree("o/r", "skills/demo", "abc123", Path(td), FakeGh.tree_with_binary())
            self.assertEqual((Path(td) / "scripts/run.py").read_bytes(), b"print('ok')\n")
            self.assertEqual((Path(td) / "assets/icon.bin").read_bytes(), b"\x00\xff")
            self.assertEqual(result["commit_sha"], "abc123")
```

- [x] **Step 2: 运行测试确认旧 `_meta` 短路、名称前缀和 text 模式不满足要求**

Run: `python3 -m unittest tests.test_provenance_github -v`

Expected: 来源误分类或嵌套/二进制文件断言失败。

- [x] **Step 3: 实现证据优先级、GitHub 缓存和完整树快照**

来源结果必须包含 `class/type/repo/path/confidence/evidence`；`self-built` 只接受 `self-built.txt` 中精确 directory 或稳定 ID，客户端 builtin/plugin 只接受适配器 manifest/receipt 证据。homepage 和 GitHub 搜索只产生 candidate。

`repo_snapshot` 保存 stars、forks、archived、pushed_at、release、license、contributors、commit_sha、fetched_at、popularity_scope；不生成 `users`。`fetch_skill_tree` 使用 Git tree/blob API 的固定 commit SHA，blob 按 bytes 写入，source_dir 外文件拒绝，路径经过纯相对路径校验。网络失败返回结构化 `stale/error`，不把旧缓存清空。

重写 `check_updates.py`：不再无条件打开 `.skill-lock.json`；比较本地完整 tree hash 和固定候选 tree hash；把 `commit_sha/candidate_hash/local_hash/full_diff_summary` 写入 updates。删除“改动少所以放心更新”等结论，只输出“有候选更新/需审查/本地定制/无法核实”。CLI 支持 `--inventory <inventory.json> --output <updates.json>`，测试和其他 Agent 可以完全绕开项目运行时数据。

创建 `tests/fixtures/inventory-value.json`，只包含一个受保护客户端文档 Skill、一个有 GitHub 来源的第三方文档 Skill和一个来源未知 Skill；路径全部使用 `/fixture/home`，repo 使用 `example/*`，供 Task 5 和 Task 6 CLI 测试共享。

- [x] **Step 4: 运行来源、空 HOME、无 gh 和全量测试**

Run:

```bash
python3 -m unittest tests.test_provenance_github -v
env HOME=/tmp/skill-keeper-v2-empty-home python3 scripts/check_updates.py --inventory tests/fixtures/inventory-value.json --output /tmp/skill-keeper-v2-updates.json --json
python3 -m unittest discover -s tests -v
```

Expected: 缺 lock 或 gh 时不 traceback；JSON 显示 skipped/stale 原因；全量测试通过。

- [x] **Step 5: 提交**

```bash
git add scripts/core/provenance.py scripts/core/github.py scripts/check_updates.py tests/test_provenance_github.py tests/fixtures/inventory-value.json
git commit -m "feat: verify sources and cache repository evidence"
```

---

### Task 6: 生成重复/替代候选和大模型价值审查队列

**Files:**
- Create: `scripts/core/overlap.py`
- Create: `scripts/core/reviews.py`
- Create: `scripts/value_review.py`
- Create: `tests/test_value_reviews.py`
- Modify: `SKILL.md`

**Interfaces:**
- Produces: `exact_duplicate_groups(inventory) -> list[dict]`
- Produces: `candidate_pairs(inventory, min_similarity=0.32) -> list[dict]`
- Produces: `build_review_queue(inventory, reputation, existing_reviews) -> dict`
- Produces: `record_review(queue, review_payload, reviewer_model) -> dict`
- Consumes: provenance、tree hash、客户端 aliases、reputation snapshot

- [x] **Step 1: 写失败测试，固定候选、保护类、审查证据和过期逻辑**

```python
# tests/test_value_reviews.py
class ValueReviewTests(unittest.TestCase):
    def test_protected_skills_are_alternatives_but_not_review_targets(self):
        inv = review_inventory_fixture()
        queue = build_review_queue(inv, reputation_fixture(), {})
        target_ids = {x["instance_id"] for x in queue["items"]}
        self.assertNotIn("codex-builtin-docx", target_ids)
        docx_item = next(x for x in queue["items"] if x["instance_id"] == "third-party-word")
        self.assertIn("codex-builtin-docx", docx_item["alternative_candidates"])

    def test_delete_recommendation_requires_explanation_not_fixed_score(self):
        queue = one_item_queue()
        payload = {"instance_id": "third-party-word", "verdict": "建议删除", "reason": "功能已被客户端文档工具覆盖，且该 Skill 没有额外能力", "alternatives": ["codex-builtin-docx"], "unique_capabilities": [], "loss_if_removed": "失去旧触发描述，不影响文档处理", "confidence": "高", "evidence": ["overlap:0.91", "repo:archived"]}
        saved = record_review(queue, payload, "test-model")
        self.assertEqual(saved["verdict"], "建议删除")
        self.assertEqual(len(saved["evidence"]), 2)

    def test_stars_alone_cannot_produce_delete(self):
        with self.assertRaises(ValueError):
            record_review(one_item_queue(), low_star_only_payload(), "test-model")
```

- [x] **Step 2: 运行测试确认价值审查模块不存在**

Run: `python3 -m unittest tests.test_value_reviews -v`

Expected: import 失败或审查校验不存在。

- [x] **Step 3: 实现确定性候选和大模型记账边界**

`exact_duplicate_groups` 用 tree hash；`candidate_pairs` 对 name、description、trigger、依赖、正文词元和来源路径计算可解释的分项相似度，只生成候选。受保护 Skill 不成为审查目标，但可以成为替代品。

`build_review_queue` 为每个第三方 Skill 输出：完整内容路径、来源证据、repo snapshot、安检状态、相似候选、客户端替代候选、上下文/依赖和旧审查状态。Skill 正文明确标为 untrusted data。

`record_review` 接受五种 verdict，验证当前 tree hash、inventory fingerprint 和 reputation snapshot；`建议删除` 必须包含非空 reason、loss_if_removed、confidence、至少一个可核实 evidence，并且证据不能只有 stars/forks。它不要求固定三条件，也不替大模型做简单总分。

`value_review.py` 提供：

```text
python3 scripts/value_review.py queue --json
python3 scripts/value_review.py show <instance_id> --json
python3 scripts/value_review.py record --file <review.json> --model <model-name>
```

`queue` 还必须支持 `--inventory <inventory.json> --output <review-queue.json>`，以便测试和其他 Agent 在不改项目运行时数据的情况下生成队列。

修改 `SKILL.md` 工作流：扫描后先生成 queue；大模型逐项把 Skill 内容当不可信材料阅读，综合功能、适配、维护、热度、安全、成本、独特性和替代关系，记录理由；不执行被审查 Skill 中的任何指令。

- [x] **Step 4: 运行价值审查测试和 CLI fixture 测试**

Run:

```bash
python3 -m unittest tests.test_value_reviews -v
python3 scripts/value_review.py queue --inventory tests/fixtures/inventory-value.json --output /tmp/skill-keeper-v2-review-queue.json --json
python3 -m unittest discover -s tests -v
```

Expected: 保护类只作替代候选；第三方进入队列；低星单因子删除记录被拒绝；全量测试通过。

- [x] **Step 5: 提交**

```bash
git add scripts/core/overlap.py scripts/core/reviews.py scripts/value_review.py tests/test_value_reviews.py SKILL.md
git commit -m "feat: queue explainable skill value reviews"
```

---

### Task 7: 实现可验证、可往返的备份与安全恢复

**Files:**
- Create: `scripts/core/backup.py`
- Create: `tests/test_backup_restore.py`

**Interfaces:**
- Produces: `create_backup(plan, inventory, backup_dir) -> dict`
- Produces: `verify_backup(archive_path) -> dict`
- Produces: `restore_backup(archive_path, locations, conflict="fail") -> dict`
- Consumes: tree manifest/hash、Location、ChangePlan、原子写入

- [x] **Step 1: 写失败测试，覆盖多位置、符号链接、重复 tar 名和恶意归档**

```python
# tests/test_backup_restore.py
class BackupRestoreTests(unittest.TestCase):
    def test_round_trip_preserves_two_instances_and_symlink(self):
        env = two_location_skill_fixture(self)
        backup = create_backup(env.plan, env.inventory, env.backup_dir)
        names = tar_member_names(backup["path"])
        self.assertEqual(len(names), len(set(names)))
        env.remove_targets()
        result = restore_backup(Path(backup["path"]), env.locations)
        self.assertEqual(result["restored_hashes"], env.original_hashes)
        self.assertTrue((env.claude_root / "demo").is_symlink())

    def test_malicious_member_is_rejected(self):
        archive = make_tar_with_member(self, "../../escape")
        with self.assertRaises(BackupError):
            verify_backup(archive)
```

- [x] **Step 2: 运行测试确认旧归档包含重复 member 且恢复位置丢失**

Run: `python3 -m unittest tests.test_backup_restore -v`

Expected: round-trip 或恶意归档测试失败。

- [x] **Step 3: 实现 manifest-first 归档和不使用 extractall 的恢复**

归档内使用 `payload/<instance_id>/<relative-path>` 唯一路径；普通文件作为 regular member，目录、权限和符号链接只记录在 `manifest.json`，不创建 tar symlink/hardlink。manifest 保存 schema、backup_id、plan_id、location_id、original_relative_path、tree manifest/hash、created_at 和 reason。

`verify_backup` 逐 member 拒绝绝对路径、`..`、重复名、设备文件、tar symlink/hardlink 和 manifest 外成员，验证所有 payload SHA-256。`restore_backup` 先在每个目标父目录的临时目录重建并校验，再原子移动；冲突默认失败，不覆盖。旧格式只通过单独 `inspect_legacy_backup` 展示，不进入自动恢复。

- [x] **Step 4: 运行备份测试和故障注入测试**

Run: `python3 -m unittest tests.test_backup_restore -v && python3 -m unittest discover -s tests -v`

Expected: 多实例逐位置恢复；恶意、重复、损坏或冲突归档安全失败；全量测试通过。

- [x] **Step 5: 提交**

```bash
git add scripts/core/backup.py tests/test_backup_restore.py
git commit -m "feat: add verified round-trip backups"
```

---

### Task 8: 实现不可变 ChangePlan、安全删除和统一审计

**Files:**
- Create: `scripts/core/audit.py`
- Create: `scripts/core/changes.py`
- Modify: `scripts/remove_skill.py`
- Create: `tests/test_change_remove.py`

**Interfaces:**
- Produces: `create_remove_plan(instance_ids, inventory, reason, plans_dir) -> ChangePlan`
- Produces: `apply_plan(plan_id, digest, confirm, context) -> dict`
- Produces: `append_audit(event, audit_path)`
- Consumes: backup engine、FileLock、atomic JSON、stable instance ID

- [x] **Step 1: 写失败测试，覆盖 `..`、路径逃逸、过期计划、并发和回滚**

```python
# tests/test_change_remove.py
class ChangeRemoveTests(unittest.TestCase):
    def test_arbitrary_names_cannot_be_removed(self):
        env = change_env(self)
        for raw in (".", "..", "/tmp/x", "a/b", "x;touch-pwn"):
            with self.assertRaises(ChangeError):
                create_remove_plan([raw], env.inventory, "test", env.plans_dir)
        self.assertTrue(env.agents_root.exists())

    def test_apply_requires_exact_digest_and_rolls_back_on_verify_failure(self):
        env = change_env(self); plan = env.remove_plan()
        with self.assertRaises(ChangeError):
            apply_plan(plan.plan_id, "wrong", True, env.context)
        env.context.verify_after_apply = lambda: False
        with self.assertRaises(ChangeError):
            apply_plan(plan.plan_id, plan.digest, True, env.context)
        self.assertTrue(env.skill_path.exists())
        self.assertEqual(env.last_audit()["rollback_status"], "restored")
```

- [x] **Step 2: 运行测试并确认旧 CLI 仍能把 `..` 拼到根目录**

Run: `python3 -m unittest tests.test_change_remove -v`

Expected: 安全计划接口不存在或危险输入未拒绝；绝不实际调用旧 CLI 的 `..`。

- [x] **Step 3: 实现计划、路径约束、文件锁和统一 CLI**

`create_remove_plan` 只接受 inventory 中 mutable instance ID，读取当前 tree hash 作为 precondition；plan JSON 规范化后计算 digest，默认 30 分钟过期。`apply_plan` 要求 `confirm is True`、digest 完全一致、计划未过期、目标 hash 未变；获取 `data/.change.lock` 后创建并验证备份，再删除精确目标。删除使用 `unlink`/`shutil.rmtree`，每个 resolved target 必须位于对应 Location 根目录下一层或计划声明的相对路径。

验证失败时立即从新格式备份恢复；成功和失败都写 audit v2。CLI 改为：

```text
python3 scripts/remove_skill.py plan --instance-id <instance_id> --reason <reason>
python3 scripts/remove_skill.py apply <plan_id> --digest <digest> --confirm
```

`--instance-id` 为可重复参数；多个目标时再次写一个 `--instance-id <instance_id>`。

旧的 `remove_skill.py <目录名>` 只打印迁移说明并退出 2，绝不删除。

- [x] **Step 4: 运行路径、并发、回滚和全量测试**

Run: `python3 -m unittest tests.test_change_remove -v && python3 -m unittest discover -s tests -v`

Expected: 任意字符串无法成为目标；只有精确 plan 可执行；第二个并发申请安全失败；故障自动恢复。

- [x] **Step 5: 提交**

```bash
git add scripts/core/audit.py scripts/core/changes.py scripts/remove_skill.py tests/test_change_remove.py
git commit -m "feat: require immutable plans for destructive changes"
```

---

### Task 9: 实现固定候选、先审查后激活的事务式更新

**Files:**
- Modify: `scripts/core/changes.py`
- Modify: `scripts/check_updates.py`
- Create: `tests/test_change_update.py`

**Interfaces:**
- Produces: `create_update_plan(instance_id, candidate_snapshot, inventory, plans_dir) -> ChangePlan`
- Produces: `record_candidate_vet(plan_id, candidate_hash, verdict, evidence) -> dict`
- Extends: `apply_plan` 支持 action `update`
- Consumes: GitHub/skills.sh staged candidate、完整 tree diff、backup、audit

- [x] **Step 1: 写失败测试，覆盖 TOCTOU、嵌套/二进制、未安检和交换失败**

```python
# tests/test_change_update.py
class ChangeUpdateTests(unittest.TestCase):
    def test_apply_uses_reviewed_staged_hash_not_refetched_head(self):
        env = update_env(self); plan = env.create_plan(candidate="v2")
        env.remote_head = "v3-malicious"
        record_candidate_vet(plan.plan_id, env.v2_hash, "safe", ["fixture-review"])
        apply_plan(plan.plan_id, plan.digest, True, env.context)
        self.assertEqual(tree_hash(env.skill_path), env.v2_hash)

    def test_unvetted_or_changed_candidate_is_rejected(self):
        env = update_env(self); plan = env.create_plan(candidate="v2")
        with self.assertRaises(ChangeError):
            apply_plan(plan.plan_id, plan.digest, True, env.context)
        (env.staging / "run.py").write_text("changed", encoding="utf-8")
        record_candidate_vet(plan.plan_id, env.v2_hash, "safe", ["fixture-review"])
        with self.assertRaises(ChangeError):
            apply_plan(plan.plan_id, plan.digest, True, env.context)
```

- [x] **Step 2: 运行测试确认旧更新会重取 HEAD 并逐文件清空原目录**

Run: `python3 -m unittest tests.test_change_update -v`

Expected: staged snapshot、candidate vet 或原子交换接口缺失。

- [x] **Step 3: 实现 staging、完整 diff、候选安检绑定和原子交换**

检查更新时把固定 commit/市场响应的完整候选放入 `data/staging/<plan_id>`，保存 candidate tree hash 和逐文件 add/remove/modify 摘要。更新计划 precondition 同时绑定 local hash、source、commit SHA、candidate hash 和 staging path。

`record_candidate_vet` 只接受当前 staging 的 hash；`apply_plan(update)` 在锁内再次计算 local/candidate hash，要求 vet verdict 为 safe 或用户对 warning 做第二次明确确认。把旧目录原子 rename 为同父目录 rollback 路径，再把 staging rename 到目标；扫描验证失败则反向 rename 回滚。成功后才删除 rollback 临时目录，并让旧安检/价值审查因 tree hash 变化自动过期。

skills.sh 没有 commit SHA 时，候选本身的完整文件集和 hash 是不可变对象；应用阶段不得重新调用 `npx skills add`。

- [x] **Step 4: 运行更新故障注入和全量测试**

Run: `python3 -m unittest tests.test_change_update -v && python3 -m unittest discover -s tests -v`

Expected: 远端 HEAD 改变不影响已审查候选；未安检、staging 被改、交换失败和验证失败均拒绝或回滚。

- [x] **Step 5: 提交**

```bash
git add scripts/core/changes.py scripts/check_updates.py tests/test_change_update.py
git commit -m "feat: stage and atomically apply reviewed updates"
```

---

### Task 10: 重写交互服务为 plan/apply API 并加固本地 Web 边界

**Files:**
- Modify: `scripts/serve.py`
- Create: `tests/test_serve_api.py`

**Interfaces:**
- Produces: `POST /api/plan`, `POST /api/apply`, `POST /api/restore-plan`, `GET /api/plan/<id>`
- Consumes: change engine、value review、audit、inventory v2
- Removes: 直接 `/api/remove`、直接覆盖式 `/api/update` 和直接 `extractall` 恢复

- [x] **Step 1: 写失败 HTTP 测试，固定确认布尔值、请求大小、token 和串行变更**

```python
# tests/test_serve_api.py（使用临时 ThreadingHTTPServer）
class ServeApiTests(unittest.TestCase):
    def test_false_string_is_not_confirmation(self):
        r = self.post("/api/apply", {"plan_id": "p", "digest": "d", "confirm": "false"})
        self.assertEqual(r.status, 400)

    def test_missing_token_and_oversized_body_are_rejected(self):
        self.assertEqual(self.post_without_token("/api/plan", {}).status, 403)
        self.assertEqual(self.post_raw("/api/plan", b"x" * 70000).status, 413)

    def test_security_headers_are_present(self):
        r = self.get_report()
        self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(r.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(r.headers["X-Frame-Options"], "DENY")
```

- [x] **Step 2: 运行测试确认旧 API 可直接执行且 `bool("false")` 为真**

Run: `python3 -m unittest tests.test_serve_api -v`

Expected: 路由、严格确认、413 或安全 header 断言失败。

- [x] **Step 3: 实现两阶段 API、严格请求解析和统一错误响应**

请求体最大 64 KiB；`confirm` 必须 `is True`；token 使用 `secrets.compare_digest`；POST 校验 `Origin` 为空或精确为当前 localhost origin。所有响应添加 `nosniff/no-referrer/DENY/Permissions-Policy`。CSP 的 `default-src`、`connect-src`、`img-src` 仅允许本地资源；交互报告脚本通过带 token 的同源 `/report.js` 加载，不使用 `unsafe-inline`/`unsafe-eval` 脚本权限；静态 `report.html` 仍可单文件打开。服务不把异常 repr、绝对用户路径或配置内容返回浏览器。

`/api/plan` 只生成计划并返回普通人摘要、digest、备份策略和影响；`/api/apply` 调用统一 change engine。进程内锁避免两个 HTTP handler 同时进入变更引擎，文件锁处理跨进程竞争。失败动作也写 audit。

- [x] **Step 4: 运行 API、并发和全量测试**

Run: `python3 -m unittest tests.test_serve_api -v && python3 -m unittest discover -s tests -v`

Expected: 未授权、伪确认、超大请求和并发变更被拒绝；合法计划可在临时 HOME 完成。

- [x] **Step 5: 提交**

```bash
git add scripts/serve.py tests/test_serve_api.py
git commit -m "feat: use two-phase plans in local report service"
```

---

### Task 11: 升级 Markdown/HTML 报告为普通人价值审查面板

**Files:**
- Modify: `scripts/report.py`
- Create: `tests/test_report_v2.py`
- Create: `examples/fixtures/inventory-v2.json`
- Modify: `scripts/make_sample_report.py`
- Modify: `examples/report-sample.html`

**Interfaces:**
- Consumes: inventory v2、reputation、review queue、value reviews、updates、backups、audit
- Produces: `data/report.md`、`data/report.html`、`report.py --json`
- Produces: 固定 fixture 生成的脱敏示例报告

- [x] **Step 1: 写失败测试，固定五种结论、热度口径、替代说明和静态命令安全**

```python
# tests/test_report_v2.py
class ReportV2Tests(unittest.TestCase):
    def test_value_sections_and_repo_scope_are_explained(self):
        html = render_html(v2_report_fixture())
        for label in ("建议保留", "优先保留另一个", "观察", "建议删除", "需要人工确认"):
            self.assertIn(label, html)
        self.assertIn("仓库热度，不等于该 Skill 的真实使用人数", html)
        self.assertIn("删除后可能失去", html)

    def test_untrusted_names_are_escaped_and_static_action_has_no_shell_command(self):
        html = render_html(v2_report_fixture(name='x;touch /tmp/pwn<script>'))
        self.assertNotIn("<script>touch", html)
        self.assertNotIn("remove_skill.py x;touch", html)
```

- [x] **Step 2: 运行测试确认旧报告没有价值审查和完整 plan 摘要**

Run: `python3 -m unittest tests.test_report_v2 -v`

Expected: 新分区、口径或安全静态操作断言失败。

- [x] **Step 3: 实现 v2 报告和固定公开 fixture**

报告顶部先展示：受保护类数量、第三方待审、建议保留、重复二选一、观察、建议删除、需确认。每张第三方卡片展示来源置信度、GitHub/市场证据时间、仓库级热度提示、替代候选、独特能力、删除损失、置信度和过期状态。

静态模式不复制包含目录名的 shell 删除命令，只复制安全的 `instance_id` plan 命令并用 `shlex.join` 生成。服务模式点击操作先显示服务器返回的 plan 摘要，再让用户确认 digest。

`make_sample_report.py` 只读取 `examples/fixtures/inventory-v2.json`；fixture 全部使用虚构名称、repo、路径、日期和数字，不读取 `data/inventory.json`。

- [x] **Step 4: 生成示例、运行 HTML 测试并人工检查**

Run:

```bash
python3 -m unittest tests.test_report_v2 -v
python3 scripts/make_sample_report.py
shasum -a 256 examples/report-sample.html > /tmp/skill-keeper-sample-before.sha
python3 scripts/make_sample_report.py
shasum -a 256 examples/report-sample.html > /tmp/skill-keeper-sample-after.sha
diff -u /tmp/skill-keeper-sample-before.sha /tmp/skill-keeper-sample-after.sha
python3 scripts/report.py --json
python3 -m unittest discover -s tests -v
```

Expected: 两次示例摘要完全一致；报告不含未转义 HTML 或危险命令。

- [x] **Step 5: 提交**

```bash
git add scripts/report.py scripts/make_sample_report.py tests/test_report_v2.py examples/fixtures/inventory-v2.json examples/report-sample.html
git commit -m "feat: report explainable keep and remove guidance"
```

---

### Task 12: 数据迁移、历史删除案例和文档一致性

**Files:**
- Create: `scripts/core/migrations.py`
- Create: `tests/test_migrations_docs.py`
- Modify: `README.md`
- Modify: `SKILL.md`
- Modify: `AGENTS.md`
- Modify: `data/known-sources.example.json`
- Modify: `data/groups.example.json`
- Modify: `data/workspace-locations.example.txt`

**Interfaces:**
- Produces: `migrate_runtime_state(data_dir, inventory) -> dict`
- Produces: legacy backup inspection result，不自动恢复旧备份
- Consumes: v1 inventory/vetted/updates、v2 schema、三份历史备份只读元数据

- [x] **Step 1: 写失败测试，固定用户配置保留、旧记录降级和三份历史案例**

```python
# tests/test_migrations_docs.py
class MigrationDocsTests(unittest.TestCase):
    def test_personal_config_is_preserved_and_old_vetting_needs_recheck(self):
        data = v1_state_fixture(self)
        before = (data / "groups.json").read_bytes()
        result = migrate_runtime_state(data, v2_inventory_fixture())
        self.assertEqual((data / "groups.json").read_bytes(), before)
        self.assertEqual(result["vetting"]["demo"]["status"], "needs-recheck")

    def test_legacy_removed_examples_are_inspected_not_restored(self):
        result = inspect_legacy_cases(["code-1.0.4", "memory", "word-docx"], fixture_backup_dir())
        self.assertEqual({x["name"] for x in result}, {"code-1.0.4", "memory", "word-docx"})
        self.assertTrue(all(x["restored"] is False for x in result))
```

- [x] **Step 2: 运行测试确认当前状态没有 schema 迁移**

Run: `python3 -m unittest tests.test_migrations_docs -v`

Expected: migration/legacy inspection 接口缺失。

- [x] **Step 3: 实现迁移并把所有文档改成同一套 v2 事实**

迁移前原子备份旧 JSON 到 `data/migrations/`，但不提交；groups/self-built/known-sources/ignore/workspace-locations 原样保留。旧 vetted 记录保留历史 note/date，但状态设为 `needs-recheck`，直到完整 tree hash 重新安检。旧 updates 失效重建。旧备份仅列出重复 member、缺少位置 manifest 等限制，绝不自动恢复。

README、SKILL、AGENTS 必须统一写明：支持的客户端、GitHub 热度口径、第三方价值审查、大模型综合判断、禁止自动删除、plan/apply 操作、安全备份恢复和新命令。删除“只改几行可放心更新”“所有插件天然免检”等旧承诺。SKILL version 和服务版本同步升级到 `2.0.0`。

- [x] **Step 4: 运行迁移、文档命令和个人数据泄漏检查**

Run:

```bash
python3 -m unittest tests.test_migrations_docs -v
python3 -m unittest discover -s tests -v
git grep -nE 'FAKE-SECRET-123|ANTHROPIC_AUTH_TOKEN|sk-[A-Za-z0-9]{12,}|/Users/[^/]+/.accio/accounts/[0-9]+' -- ':!docs/superpowers/plans/*'
```

Expected: 全量测试通过；grep 无输出；示例和文档不含真实账号编号、个人 inventory 或 secret。

- [x] **Step 5: 提交**

```bash
git add scripts/core/migrations.py tests/test_migrations_docs.py README.md SKILL.md AGENTS.md data/*.example.*
git commit -m "docs: document v2 migration and review workflow"
```

---

### Task 13: 全链路验收、真实只读扫描和交付复核

**Files:**
- Create: `tests/test_end_to_end.py`
- Modify: `scripts/scan.py`
- Modify: `scripts/check_updates.py`
- Modify: `scripts/value_review.py`
- Modify: `scripts/remove_skill.py`
- Modify: `scripts/report.py`
- Modify: `scripts/serve.py`
- Modify: `scripts/core/*.py`

以上 Modify 项只在端到端失败能定位到对应缺陷时修改；不得借验收任务增加规格外功能。

**Interfaces:**
- Consumes: 全部 v2 CLI、核心模块、fixture 和报告
- Produces: 可重复的测试结果、真实只读 inventory/report、最终 Git 提交

- [x] **Step 1: 写端到端测试，模拟“扫描→GitHub证据→审查→计划→备份→删除→恢复”**

```python
# tests/test_end_to_end.py
class EndToEndTests(unittest.TestCase):
    def test_full_review_remove_restore_flow(self):
        env = full_v2_fixture(self)
        inv = env.scan()
        queue = env.build_queue(inv)
        review = env.record_remove_recommendation(queue)
        plan = env.plan_remove(review["instance_id"], review["review_id"])
        removed = env.apply(plan)
        self.assertFalse(env.target.exists())
        restored = env.restore(removed["backup_id"])
        self.assertTrue(env.target.exists())
        self.assertEqual(tree_hash(env.target), env.original_hash)
        self.assertEqual([x["status"] for x in env.audit()], ["success", "success"])
```

- [x] **Step 2: 运行全量测试、编译和 CLI help**

Run:

```bash
python3 -m compileall -q scripts tests
python3 -m unittest discover -s tests -v
python3 scripts/scan.py --help
python3 scripts/check_updates.py --help
python3 scripts/value_review.py --help
python3 scripts/remove_skill.py --help
python3 scripts/report.py --help
```

Expected: 全部成功；危险入口没有旧式任意目录删除用法。

- [x] **Step 3: 在真实 HOME 只运行盘点、GitHub证据刷新和报告，不执行写操作**

Run:

```bash
python3 scripts/scan.py --json
python3 scripts/check_updates.py --json
python3 scripts/value_review.py queue --json
python3 scripts/report.py
```

Expected: 报告识别 ZCode、Codex、Accio Work、WorkBuddy、Claude Code、Haha、Cindy；marketplace 未安装内容不膨胀总数；无 traceback。`check_updates.py` 退出 1 仅表示发现差异时，应人工检查 JSON 的 `operational_ok` 为 true。

- [x] **Step 4: 人工查看 HTML、审计敏感信息和 Git diff**

Run:

```bash
open data/report.html
git diff --check
git status --short
git grep -nE '(AUTH_TOKEN|API_KEY|SECRET|COOKIE).{0,40}[=:].{4,}' -- ':!tests/*' ':!docs/superpowers/plans/*'
git grep -nE '/Users/[^/]+/' -- ':!AGENTS.md' ':!docs/superpowers/specs/*' ':!docs/superpowers/plans/*'
```

人工确认：报告中的客户端关系、受保护/第三方分类、GitHub 热度口径、替代建议和操作按钮与规格一致；报告页面不显示真实 token、账号编号或客户端私密配置。运行时 data/backups 不进入 git status。

- [x] **Step 5: 修复验收发现的问题后重复全量测试并提交最终验收**

Run:

```bash
python3 -m compileall -q scripts tests
python3 -m unittest discover -s tests -v
git diff --check
git status --short
git add scripts tests README.md SKILL.md AGENTS.md data/*.example.* examples .gitignore
git commit -m "release: complete skill-keeper v2 safety and value review"
```

Expected: 工作区只剩用户原有的无关改动；最终提交不包含 `data/inventory*.json`、个人配置、审查记录、备份或 secrets。

---

## Final Acceptance Checklist

- [x] 全部 `unittest` 通过，测试数量不为 0。
- [x] 任意路径、`.`、`..`、链接逃逸和未登记目录不能成为变更目标。
- [x] 多客户端多副本备份可以逐位置、逐字节恢复。
- [x] 更新安装的是已查看、已安检的固定 candidate hash，远端后续变化不影响它。
- [x] ZCode、Codex、Accio Work、WorkBuddy、Claude Code、Haha、Cindy 均被正确发现；marketplace 商品目录不算已安装。
- [x] 用户自建和有证据的客户端自带内容受保护；名称前缀或自报 name 不能骗取免检。
- [x] 所有第三方 Skill 有来源状态、热度证据、重复/替代候选和大模型审查队列。
- [x] “建议删除”有理由、替代品、删除损失、证据和置信度，且不会自动执行。
- [x] CLI 和网页共用 plan/apply、备份、锁、回滚和审计引擎。
- [x] 报告和日志不含客户端 secret、真实账号编号或未脱敏个人数据。
- [x] README、SKILL、AGENTS、示例报告和实际行为一致。
- [x] Code、Memory、word-docx 历史备份只读验证完成，没有被恢复或再次删除。
