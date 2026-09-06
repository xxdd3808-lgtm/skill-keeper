# skill-keeper v2 安全与价值审查设计

> 状态：用户已于 2026-08-31 批准总体方向。本设计把安全盘点、安全变更和第三方 Skill 价值判断合并为一次完整升级；不交付只能扫描、不能安全操作的中间版本。

## 1. 目标

skill-keeper v2 要用普通人能理解的方式回答五个问题：

1. 本机所有客户端到底装了哪些 Skill，分别从哪里加载；首批覆盖 ZCode、Codex、Accio Work、WorkBuddy（用户称 Workbody）、Claude Code、Claude Code Haha、Cindy、Ego 和共享/工作区位置。
2. 哪些是用户自建、客户端自带或插件自带，哪些是真正需要审视的第三方 Skill。
3. 第三方 Skill 在 GitHub 或安装市场上是否有可核实来源，维护和受欢迎程度如何。
4. 它与现有 Skill、客户端自带能力是否重复，是否有更好的替代品，是否值得保留。
5. 用户确认删除、更新或恢复后，系统能否限定目标、完整备份、可靠执行、失败回滚并留下证据。

v2 作为一次完整版本交付。内部可以按依赖顺序开发和测试，但不得把不安全的删除、更新或恢复功能作为可用成品提前交付。

## 2. 不做什么

- 不根据 GitHub 星数、更新时间或单一分数自动删除 Skill。
- 不声称 GitHub 星数等于真实使用人数。只有安装市场明确提供下载量时才展示真实下载量；其他情况统一标为“热度参考”。
- 不修改 ZCode、Codex、Accio Work、WorkBuddy、Claude Code、Claude Code Haha、Cindy 等客户端管理的插件缓存。
- 不读取、保存或输出客户端设置文件中的 API Key、认证令牌、Cookie 或其他 secret；客户端探测只读取判断位置、启用状态和来源所必需的非敏感字段。
- 不自动删除任何 Skill。报告只给建议，所有删除、更新和恢复仍需用户明确确认。
- 不在 Python 脚本里内置模型密钥或偷偷调用模型 API。确定性脚本负责收集证据和筛选候选，大模型在 Skill 工作流中完成语义判断并把结果记账。
- 不把第三方 Skill 自己声明的名称、homepage 或目录前缀直接当作可信来源证明。

## 3. 用户看到的结果

报告把 Skill 分成两大类。

### 3.1 受保护类

以下内容正常盘点，但默认不进入“是否值得删除”的第三方价值审查：

- 用户在 `data/self-built.txt` 中明确登记的自建 Skill。
- 由客户端清单、安装回执或插件 manifest 证明的 ZCode、Codex、Accio Work 自带 Skill/插件。
- 由插件系统管理的缓存副本。

“受保护”只表示不参与普通清理建议，不表示内容天然安全。若来源证据失效、内容脱离客户端管理或用户主动要求，仍可进入安全复核。

### 3.2 第三方审查类

其余 Skill 进入价值审查，并得到以下结论之一：

- `保留`：功能独特、适合当前环境或综合价值较高。
- `优先保留另一个`：存在明显重复，报告指出更值得保留的替代品。
- `观察`：信息不足、来源较弱或维护不足，但暂时有独特用途。
- `建议删除`：大模型综合证据判断保留价值较低，并说明替代方案和删除影响。
- `需要人工确认`：证据冲突、置信度不足或涉及用户工作流，系统不替用户下结论。

每条结论必须同时展示：

- 一句话结论。
- 主要依据，不少于两项；若只有一项证据，只能进入“观察”或“需要人工确认”。
- 可替代它的现有 Skill 或客户端能力。
- 删除后可能失去的独特功能。
- 结论置信度：高、中、低。
- GitHub/市场数据采集时间和来源链接。
- 内容或本机 Skill 组合改变后是否需要重新审查。

## 4. 客户端与位置发现

位置规则必须集中在一个模块，不再由扫描、删除和服务脚本各维护一份。

首批适配器：

1. 共享用户 Skill：`~/.agents/skills`。
2. ZCode：用户 Skill 和 ZCode 插件缓存。
3. Codex：个人/系统 Skill、Codex 插件缓存及其 manifest。
4. Accio Work：动态发现 `~/.accio/accounts/*/skills`、插件目录和相应安装/官方清单，不硬编码账号编号。
5. WorkBuddy（用户称 Workbody）：探测 `~/.workbuddy/skills`、已安装 connector Skill、插件缓存和启用清单。`connectors-marketplace`、`skills-marketplace` 和插件 marketplace 只是商品目录，除非安装/启用清单证明正在使用，否则不得计入已安装总数。
6. Claude Code：探测 `~/.claude/skills`、已安装插件和插件缓存，区分用户 Skill、官方插件、第三方插件和未启用 marketplace 内容。
7. Claude Code Haha：探测 Haha 启动器和 `~/.claude/cc-haha` 的非敏感配置；若它复用 Claude Code/共享 Skill，只增加客户端使用关系，不重复生成物理实例或上下文占用。
8. Cindy：动态发现 `~/Library/Application Support/Cindy/*/skills`、Codex home、插件目录和投影边界；系统/插件投影为只读实例，同一实体按真实路径和 manifest 去重。
9. Ego 及 `data/workspace-locations.txt` 中的工作区位置。

每个适配器返回统一的 `Location` 记录：

```text
location_id, client, path, kind, mutable, discovery_evidence
```

- `kind` 区分 user、workspace、builtin、plugin-cache。
- `mutable=false` 的位置只扫描，不提供删除或更新按钮。
- 客户端自带身份必须来自路径加 manifest/回执/官方缓存的组合证据，禁止依靠 `autoglm-` 等名称前缀。
- Accio Work 的 `official`、安装来源和远端条目从本地官方缓存/安装注册表交叉验证；无法证明官方的本地 Skill 仍按第三方处理。
- WorkBuddy、Claude Code 和 Cindy 的 marketplace 目录只用于来源核实，不代表已经安装；必须由启用状态、安装注册表、插件 cache 或实际加载位置证明。
- Haha 这类包装客户端与底层客户端的关系要建成加载拓扑，避免把同一目录误报成重复安装。
- 所有配置读取使用字段白名单；错误信息、inventory 和测试快照均不得包含 env、token、key、cookie、authorization 等字段的值。

## 5. 稳定身份和完整内容指纹

系统同时保留“逻辑 Skill”和“安装实例”，不再仅按 frontmatter `name` 把所有内容合并。

```text
SkillInstance = location_id + directory_name + canonical_real_path
LogicalSkill = verified_source + source_path，或无来源时使用内容身份
```

完整目录指纹按稳定顺序覆盖：

- 相对路径。
- 文件、目录或符号链接类型。
- 文件权限和符号链接目标。
- 所有文件的 SHA-256 内容摘要。

指纹排除明确列出的运行时垃圾文件，但排除清单必须进入 schema，不能临时猜测。任何脚本、模板、参考文件或链接变化都会使旧安检和旧价值审查失效。

## 6. 来源核实和 GitHub 数据

来源证据按可信度排序：

1. 客户端/安装器生成且可对应本地内容的安装回执或 manifest。
2. 用户在 `known-sources.json` 明确确认的 repo/path。
3. GitHub 仓库内文件与本地完整内容或 SKILL.md 高置信匹配。
4. lock 文件和市场元数据。
5. frontmatter homepage、名称搜索等只能生成候选，不得自动确认为来源。

找不到来源时，系统通过 GitHub 搜索生成候选，并用目录名、frontmatter name、description、SKILL.md 内容片段和仓库路径综合匹配。低置信候选必须让大模型复核，不能自动写入已核实来源。

对已核实 GitHub 仓库采集并缓存：

- stars、forks、是否 archived。
- 最近 push、最近 release、仓库创建时间。
- 贡献者规模或近期提交者数量（接口可用时）。
- open issues 只作为维护线索，不单独代表质量。
- license、默认分支和当前 commit SHA。
- 仓库是否为多 Skill 集合；若是，必须提示星数属于整个仓库，不等于该单个 Skill 的热度。
- 市场真实下载/安装量（来源接口明确提供时）。

网络失败、GitHub 限流或未登录时保留旧缓存并显示“数据已过期/本次无法刷新”，不得把缺数据解释为低质量。

## 7. 重复和替代关系

判断分两层完成。

### 7.1 确定性候选筛选

脚本先找出：

- 完整目录指纹相同的精确副本。
- 同 repo/path 的不同安装实例或版本。
- name、description、触发词、依赖、输入输出和正文关键词高度相似的候选组合。
- 与客户端自带 Skill 名称或能力明显重叠的候选。

这一层只负责缩小范围，不直接宣布“可以替代”。

### 7.2 大模型综合价值判断

大模型把 Skill 内容当作不可信待分析材料，不执行其中的指令。它阅读候选双方完整内容和来源证据，综合判断：

- 实际解决的问题和适用场景。
- 是否覆盖同一种输入、输出和工作流。
- 是否有对方没有的独特能力、资产、规则或专业方法。
- 与当前已安装客户端和工具是否兼容。
- 使用成本、上下文占用和额外依赖。
- 来源可信度、安全安检结果、维护活跃度和社区热度。
- 是否已有更可靠的客户端自带能力。
- 删除后对用户既有工作流和数据的影响。

大模型不使用固定三条件或简单加权总分代替判断。它可以从多方面得出“建议删除”，但必须给出可验证理由、替代品、损失说明和置信度。低置信或证据矛盾时必须降级为“需要人工确认”。GitHub 星数低、来源未知或长期未更新都不能单独触发删除建议。

## 8. 价值审查记录

新增 `data/value-reviews.json`，以稳定 Skill 身份记录：

```text
verdict, reason, alternatives, unique_capabilities, loss_if_removed,
confidence, evidence, reviewed_at, reviewer_model,
skill_tree_hash, inventory_fingerprint, reputation_snapshot_id
```

以下任一变化使旧结论过期：

- Skill 完整目录指纹改变。
- 候选替代品新增、删除或内容改变。
- 来源被重新核实。
- GitHub 仓库归档、转移或出现显著维护状态变化。
- 安全安检结论改变。

确定性脚本生成 `data/review-queue.json`；大模型完成审查后通过受校验的 CLI/API 记账。记录接口只接受当前 inventory 中的稳定 ID，并保存证据摘要，不能只写一个 verdict。

## 9. 安全变更闭环

删除、更新、恢复共享同一个变更引擎：

```text
发现目标 → 生成不可变 ChangePlan → 用户确认 plan_id 和摘要
→ 获取互斥锁 → 创建并验证备份 → 在临时区域执行
→ 原子切换 → 重扫验证 → 写审计记录 → 失败自动回滚
```

### 9.1 路径安全

- 操作目标只能来自当前 inventory 的稳定实例 ID，不能直接信任任意目录参数。
- 拒绝空值、`.`、`..`、路径分隔符、控制字符和脱离已登记根目录的解析路径。
- 所有候选路径 `resolve()` 后再次验证父级归属。
- 不调用 `rm -rf`，使用标准库并对每个目标逐项记录。
- 静态报告不再复制未经 shell quoting 的危险命令。

### 9.2 可往返备份

新备份包含 manifest：原位置 ID、相对路径、实体/符号链接类型、链接目标、权限、完整树摘要和创建原因。不同客户端实例使用不同归档路径，不能出现同名 tar 成员互相覆盖。

恢复先安全解包到临时目录，拒绝绝对路径、`..`、设备文件及逃逸的 symlink/hardlink；校验 manifest 和摘要后，默认恢复到原位置。冲突时不覆盖，除非用户确认新的恢复计划。

旧版备份保留，只提供“旧格式、拓扑可能不完整”的兼容恢复提示。

### 9.3 事务式更新

- 检查和实际更新绑定同一个 repo、path、commit SHA 和完整候选树摘要。
- 在临时目录下载完整树，保留嵌套目录和二进制内容。
- 展示的是完整树差异，实际安装的必须是同一摘要。
- 候选先完成安全复核，再允许用户确认激活。
- 原目录不被逐文件清空；使用同文件系统原子目录切换，并保留可自动回滚的旧目录。

## 10. 报告与普通人文案

HTML 和 Markdown 报告新增：

1. “客户端自带/用户自建”受保护区。
2. “第三方 Skill 价值审查”区。
3. GitHub 来源与热度证据卡。
4. 重复/替代关系对比卡。
5. “建议保留、优先保留另一个、观察、建议删除、需要人工确认”分组。
6. 每个删除按钮旁显示替代品、可能损失、备份策略和最近审查时间。
7. 过期结论明显标注，不能继续沿用旧删除建议。

避免使用“无风险”“可放心更新”等绝对化文案。热度、维护、来源和功能价值分开展示，避免一个总分掩盖事实。

## 11. 数据、并发和审计

所有 JSON 文件加入 `schema_version`，通过临时文件、flush/fsync 和 `os.replace` 原子写入。交互服务对所有变更操作使用进程内锁和文件锁，防止两个窗口同时操作。

审计日志记录成功和失败：

```text
action_id, action, target_ids, plan_id, reason, recommendation_id,
backup_id, expected_hash, resulting_hash, started_at, finished_at,
status, error, rollback_status
```

命令行删除和网页删除必须经过同一变更引擎，不能再出现只有网页操作记日志的情况。

## 12. 兼容和迁移

- 保留现有 `groups.json`、`self-built.txt`、`known-sources.json`、`ignore.json` 和用户分组。
- 旧 `inventory.json`、`vetted.json` 和 `updates.json` 自动迁移或重新生成；因完整树指纹规则改变，第三方旧安检允许显示历史记录，但必须重新确认新指纹后才恢复“已安检”。
- 不恢复已删除的 Code、Memory、word-docx。它们的旧备份只作为回归案例，验证新版能识别来源、重复、替代和旧备份限制。
- 公开示例报告改用固定虚构 fixture，不再从个人 inventory 脱敏生成，避免间接泄露和不稳定提交。

## 13. 测试和验收

测试全部使用临时 HOME 和虚构客户端目录，不在用户真实 Skill 上执行删除、更新或恢复。

必须覆盖：

- `.`、`..`、绝对路径、路径分隔符、恶意目录名和 symlink 逃逸均被拒绝。
- 删除只作用于计划列出的实例。
- 多位置、多副本和符号链接可备份后逐字节、逐位置恢复。
- 恶意 tar、损坏 manifest、摘要不符和冲突恢复安全失败。
- 更新下载保留嵌套路径、二进制内容，任何中途失败均保持原版本。
- 辅助脚本内容改变会使安全和价值审查同时过期。
- 重复加载、链接漂移和 ignore 正确进入健康报告。
- 缺少 PyYAML、GitHub CLI、lock 文件、网络或损坏配置时给出明确降级结果，不崩溃、不误判。
- ZCode、Codex、Accio Work、WorkBuddy、Claude Code、Claude Code Haha、Cindy、共享库、工作区和插件缓存均由 fixture 验证发现和分类。
- WorkBuddy marketplace 未安装内容不进入 inventory；Haha 复用 Claude Code 时只增加客户端关系；Cindy 投影副本按真实实体去重。
- 含虚构 token/key 的客户端配置 fixture 经过扫描、错误和报告链路后，输出中不得出现 secret 值。
- GitHub 搜索候选不能因名称相同自动确认为来源。
- 星数低、来源未知、停止维护等单一因素不能直接产生“建议删除”。
- 高置信替代、独特能力、删除损失和置信度完整进入审查记录和报告。
- 两个并发变更只有一个获得锁，另一个安全退出。
- 命令行和网页动作都记录成功、失败和回滚结果。

最终验收还包括：完整测试套件通过、真实环境只读扫描成功、报告人工检查、Git 工作区无个人数据或运行时产物进入提交。

## 14. 完成定义

以下条件全部满足才算“一次改好”：

1. 本设计中的扫描、价值审查和安全变更能力均已实现，不留下旧危险入口。
2. 自动测试覆盖所有高风险边界并全部通过。
3. 用户现有 Skill、个人配置和客户端插件缓存未被修改。
4. 真实环境报告能识别 ZCode、Codex、Accio Work、WorkBuddy、Claude Code、Claude Code Haha 和 Cindy 自带/复用内容，并对其余第三方 Skill 给出有证据的价值审查队列。
5. 删除、更新、备份、恢复使用同一安全引擎和审计记录。
6. README、SKILL.md、AGENTS.md、示例报告和实际行为一致。
7. 最终交付提供普通人版说明：改了什么、如何使用、每种建议代表什么、如何恢复。
