# skill-keeper 现行架构与规则(2026-09-05 优化后)

历史事实见 [changes.md](changes.md);原始 v2 设计见
[../superpowers/specs/](superpowers/specs/) 下 2026-08-31 设计文档(其中"计划中"
的表述不代表已实现,以本文件为准)。

## 入口

| 入口 | 用途 |
|---|---|
| `python3 scripts/scan.py` | 只读盘点 → `data/inventory.json`(+ observation/content_status) |
| `python3 scripts/report.py [--serve]` | v2 价值报告 / 本地交互服务(127.0.0.1+token) |
| `python3 scripts/check_updates.py` | 只读:本地树 vs 固定 commit 候选树 → `data/updates.json` |
| `python3 scripts/value_review.py queue/record` | 第三方价值审查队列与记账(台账持锁) |
| `python3 scripts/manage.py plan/apply/status/recover/rescan` | 管理 CLI(与网页共用 service 层) |
| `python3 scripts/remove_skill.py` | 旧入口;目录名用法一律退出 2 |
| `python3 scripts/verify.py` | 验收入口:unittest 实际结果,0 失败 0 跳过才通过 |

## 路径解析(RuntimePaths)

优先级:**显式参数 > 环境变量(SKILL_KEEPER_DATA / SKILL_KEEPER_STAGING)> 兼容默认**。
CLI/API/报告共用同一解析;子进程环境被钉死(SKILL_KEEPER_DATA/STAGING/HOME),
禁止回到真实 HOME。默认数据目录 `项目/data`,备份 `项目/backups`(兼容既有布局);
候选暂存默认系统缓存(macOS `~/Library/Caches/skill-keeper/staging`),绝不放进任何
客户端会递归扫描的技能目录。

## 执行策略(唯一的许可判定)

- `policy.load_policy`:以 data 目录的 `known-sources.json` + `self-built.txt` 为权威;
  可选文件**不存在 ≠ 损坏**;已存在但损坏 → 拒绝一切写操作,不降级为空保护表。
- `policy.check_action`:计划与执行两个阶段各跑一次;自建默认拒绝删除/更新;
  builtin-app 等客户端托管身份给出处置建议后拒绝(builtin-app 条目可选登记 `owner`:
  owner 位置的正本照旧拒绝;非所属位置的散布快捷方式允许正规 remove 收回,
  update 不享受口子,位置缺失保守拒绝);调用方传入的 known_sources 只能叠加保护。
- `policy.validate_candidate_vet`:安检载入只认 safe|warning,证据必须非空字符串
  列表,plan_id + candidate_hash 双绑定。

## 变更事务(删除/更新/恢复)

不可变 ChangePlan(绑定 tree_hash/path/is_symlink/root_real/candidate/归档 sha256)
→ 用户确认 digest → 互斥锁(锁内重读 inventory 与策略)→ 创建并验证备份 →
持久事务状态(`data/transactions/<plan_id>.json`,phase 机)→ 原子移动到同目录
保管位 → 磁盘事实校验 + 可注入业务校验(异常与 False 同责,都不可绕过引擎自身
落盘哈希校验)→ **提交点** → 清理保管(失败进 cleanup_pending,不改已提交事实)
→ 审计(失败标 audit_pending,事实保留在事务文件)。

失败/中断:提交前异常自动回滚到原状并核对哈希;中断靠 `recover_transaction`
只撤销本事务落地的对象(按哈希匹配,陌生内容一律保留),冲突标 recovery-required
并阻止后续写;已提交计划重放返回已知结果(already_applied),已回滚计划拒绝重放。

## 备份与恢复合同

manifest 严格校验(schema/类型/权限/路径边界/唯一性/父子关系/整树哈希/SHA-256 格式);
资源上限 manifest 8MiB、条目 10 万、单文件 512MiB、总量 2GiB(超限明确拒绝);
归档临时文件 fsync 后原子发布;恢复预检全部目标,目录→文件→内部 symlink 顺序重建,
目录/根权限还原,顶层 symlink 只在目标已存在且匹配(或同事务先恢复)时落地;
失败只撤销实际落地清单;恢复计划绑定 archive_sha256 与目标集合。

## 观察与加载口径

- 发现(discovered)≠ 推断可加载(eligible,位置在客户端读取集合内)≠
  确认加载(confirmed,需直接运行时证据;没有就保持 unknown)。
- 加载规则集中在 `clients/load_rules.py`,每条带来源/核实日期/适用范围;
  工作区位置按各自项目上下文评估,不同项目的同名技能不是全局双载。
- inventory 带 `observation{complete, issues, observed_scope, rule_version, load_contexts}`;
  实例带 `content_status`;不完整观察的对象不提供完整指纹、变更入口停用。
- CLI 退出码:0=成功且无关注;1=成功但有约定红灯/差异;2=操作失败或观察不完整。

## 审查有效性

- 结论按稳定 instance ID + 内容版本连接历史;`evaluate_review` 判定
  current / needs-recheck(目标内容变化、替代品变化/消失/未核实、缺快照、政策变化),
  历史结论保留可见;过期 safe 不显示当前绿色安全徽章。
- 记账必须:verdict 五选一、confidence 高/中/低、safety 枚举、非空证据、
  reviewer_model 非空、提交 hash 与队列目标一致;台账读改写持文件锁,
  损坏台账拒绝写入(绝不重置为空)。

## 候选与缓存生命周期

上游树 truncated / submodule / 重复路径 / 链接父级冲突 / 缺根 SKILL.md /
无效 frontmatter → 明确拒绝;120000 只落地相对链接串;文件权限固定。
候选目录按内容哈希命名并登记所有权;GC 只清"本工具登记且无有效引用"的候选,
引用覆盖本轮/上次更新结果、未过期计划、活跃事务;无主历史目录保留待人工处置。

## 性能口径

审查队列一次读取全库正文、每对只评分一次(索引复用):80 个逻辑 Skill
正文读取 ≤80、评分 ≤3160(2026-09-05 实测 reads=80 / scores=3160 / 0.15s);
结果与冻结基线 `tests/fixtures/overlap-baseline.json` 等价。

## 锁与边界

- 变更互斥锁:`data/.change.lock`(apply 全程);审查台账 `.reviews.lock`;
  顺序:先状态锁后 staging 锁,不嵌套未知顺序的锁。
- 网页边界:仅 127.0.0.1 + token(字节级常量时间比较)+ Origin 校验 + 64KiB 上限
  + 白名单 CSP;错误不返回绝对路径或配置内容。
- 已知限制:本工具不能隔离拥有同一用户权限的任意程序;自我扫描时开发 checkout
  的 `.git` 会进入指纹(文档保留为限制,不用全局排除削弱第三方安检)。
