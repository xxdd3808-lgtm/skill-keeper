# PROGRESS — skill-keeper v4 精简开源泛化(2026-09-05)

## 断点续跑须知(先读我)

- 计划来源:docs/superpowers/plans/2026-09-05-skill-keeper-open-source-upgrade.md(任务书:同目录 agent-brief;设计:docs/superpowers/specs/2026-09-05-skill-keeper-open-source-design.md)。按 Task 0–5 串行;每任务红测试→最小实现→相关测试→`python3 scripts/verify.py`→文档→小提交;阶段门槛全绿自动继续。
- 开工基线(实测):HEAD `f858d2ba89d82acc93bcd78e30f3bc6ac1b24b04`,Python 3.9.6,分支 case1,verify 233 项 0 失败 0 skipped;工作树原有三份计划文档(untracked,随 Task 0 入库)。
- 零退化合同:tests/fixtures/private-v311/(完全虚构,含 shared/Codex/WorkBuddy/Ego、自建、builtin owner、符号链接、重复加载、审查、备份、旧 CLI)+ tests/test_private_compatibility.py(9 项)。v4 各任务必须保持全绿。
- 测试入口:`python3 scripts/verify.py`(Task 0 后 242 项 / 0 失败 / 0 skipped)。
- BLOCKED.md:无。

## 任务状态

| 任务 | 状态 | 提交 |
|---|---|---|
| Task 0 冻结私人版零退化合同 | 完成 | 本提交(2026-09-05) |
| Task 1 统一安装、CLI 和运行态 | 未开始 | |
| Task 2 最小跨平台底座 | 未开始 | |
| Task 3 模型位置声明与未知客户端盘点 | 未开始 | |
| Task 4 apply 前真实目标预检 | 未开始 | |
| Task 5 CI、文档与最终验收 | 未开始 | |

## 历史计划一:可信性优化(2026-09-05,F01–F11,已交付)

### 断点续跑须知(先读我)

- 计划来源:docs/superpowers/plans/2026-09-05-skill-keeper-optimization.md(任务书:同目录 agent-brief)。按 Task 0–9 串行;每任务红→绿→全量 unittest→提交。
- 测试入口:`python3 -m unittest discover -s tests`(当前 203 项全绿,0 skipped)。
- 阶段门槛:A=Task 1–3(已完成);B=Task 7;C=Task 9。BLOCKED.md 当前"无"。
- 注意:tests/test_review_lifecycle.py 曾被我一次坏编辑合并过行,已修复;不要再对该文件做"删尾随换行"类编辑。

### 任务状态(可信性优化)

| 任务 | 状态 | 提交 |
|---|---|---|
| Task 0 基线 | 完成 | 3b8c33a(203 项时的基线=126) |
| Task 1 备份/恢复合同 F01 F02 | 完成 | 63fc239 |
| Task 2 执行策略与输入校验 F04+F07边界 | 完成 | 077e9c1 |
| Task 3 事务与中断恢复 F03(阶段A达成) | 完成 | 43d7155 |
| Task 4 观察完整性 F05 | 完成 | ca1bcc4 |
| Task 5 审查历史与有效性 F06 | 完成 | 979f2c1 |
| Task 6 完整候选与缓存生命周期 F07 | 完成 | 3220ee4 |
| Task 7 CLI/API/报告闭环 F08(阶段B) | 基本完成(浏览器实点未验) | 5ae20a8(runtime/service/manage/备份按钮/静态命令/快照发布已通;**未完**:报告 JS 的 update 分支、启动器 command 同步、groups.json 视图筛选、HTTP 边界负例测试、浏览器实点验收——**已完成**:JS update 分支+warning 二次确认+HTTP 边界负例+groups.json 分组列+启动器核验无需改;**唯一未验**:浏览器实际点击验收(本会话未跑,如实记录;CLI/API/静态链路均有测试覆盖)) |
| Task 8 去重计算与外部运行态 F09 F10 | 完成 | 2979709(读取12800→80,评分259120→3160,基线等价) |
| Task 9 验收入口与文档 F11(阶段C) | 完成 | 60bc560 + 230 项 verify 退出 0 |

### 各任务要点(后续任务要消费的事实)

- Task 1:scripts/core/paths.py(validate_relative_path/confined_destination);backup.py 严格 validate_backup_manifest+资源上限(MAX_MANIFEST 8MiB/ENTRIES 100k/FILE 512MiB/TOTAL 2GiB,常量可 patch 测试);create/verify/restore 全重写(原子发布、完整往返、按实际落地清单撤销);恢复计划绑定 archive_sha256+restore_targets;macOS 只读目录跨父 rename 会 EACCES→发布后再还原根权限。
- Task 2:policy.py(load_policy/check_action/validate_candidate_vet;自建默认拒删;配置损坏拒写;known_sources 参数只能加保护);staging.py(validate_staging_root/record_ownership/cleanup_staging;只清本工具登记且无引用的 cand-*/tmp-*);计划含 is_symlink/root_real 前置键;_load_plan 结构校验(前置键白名单 regex);ChangePlan 新增 reason/recommendation_id(digest 归一化剥默认值,旧计划兼容)。
- Task 3:transactions.py(状态文件 data/transactions/<plan>.json,phase 机,holding_path=.sk-txn-<plan尾8>-<iid前8>);删除=原子移入同目录保管;更新=旧版保管+候选物化再交换;恢复=restore_backup+哈希匹配撤销;recover_transaction 只回退不激活;重放 committed 返回 already_applied,rolled-back 拒绝;审计失败→audit_pending;resulting_hash=真实哈希 JSON;子进程 os._exit(77) 三窗口可恢复;_undo_remove 两遍(先全移回再校验,链接依赖正本)。.gitignore 加 data/transactions/。
- Task 4:fingerprint.py FingerprintError(OSError 子类)+collect_errors 参数+排除目录剪枝;scan.py parse_frontmatter_detailed(嵌套 requires.bins,unsupported 警告码)+实例 content_status+observation{complete,issues,observed_scope,rule_version,load_contexts}+scan --json need_vet 真实+退出码 0/1/2;load_rules.py(规则带来源/日期/范围,RULE_VERSION);observations.py(evaluate_load eligible≠confirmed/load_receipt_evidence 白名单);插件坐标加 marketplace;check_updates 输入缺失退出 2。
- Task 5:review_state.py(review_dependencies/evaluate_review,REVIEW_POLICY_VERSION);record_review 校验 safety∈safe|warning|danger、reviewer_model 非空、提交 hash 一致、生成 review_snapshot_id+alternatives_state(候选无条目行记 None→evaluate 按 alternative-unverified 过期);value_review record 台账 FileLock+损坏拒写。CONFIDENCE_LEVELS 是 高/中/低(测试别用 medium)。队列/报告共用 evaluate_review 的接线放到 Task 7 报告改造时做(计划允许,不另开任务)。
- Task 6:github.fetch_skill_tree 重写(truncated/160000/重复路径/链接父级冲突/缺根 SKILL.md/无效 frontmatter 一律拒绝;120000 只落地相对链接串,绝对目标拒绝;100755/100644 权限固定;blob base64 strict+size 校验;source_dir 空串=仓库根;成功返回 source_dir/tree_complete/source_tree_sha/materialization_version);stage_candidate 同名 cand 按完整哈希复核,损坏旁路重物化为 cand-<hash>-<rand>(不覆盖);staging.collect_staging_references(updates+未过期计划 staging_path+活跃事务 candidate_holding)+load_reference_inputs(data 目录读三源);check() GC 引用接上计划/事务;cached_repo_snapshot 加 refresh_status/last_attempt_at(stale 分支)。测试夹具注意:候选树的 SKILL.md 必须有合法 frontmatter(test_provenance_github b3 已修)。
- Task 7(部分):runtime.py RuntimePaths(参数>env>默认;subprocess_env 钉死 HOME/DATA/STAGING)+publish_snapshot(snapshot_id=inventory mtime-size,失败标 stale);service.py AppService(plan_action/apply_action;apply 后发布快照,committed+snapshot_status=fresh|stale 区分;load_inventory 允许 instances 为空——删光后恢复必需);serve.py _handle_apply 走 service(带 accept_warning/snapshot 字段);report.py backups_list 行={backup_id,filename,path,kb,ts,verification_status},恢复按钮带 backup_id(修复双重前后缀),静态命令用仓库相对 scripts/manage.py(修复 ~/ 被引号包死;示例报告不再泄漏 /Users/ 路径),static_restore_cmd 补恢复命令;check_updates differs 行加 source_dir;manage.py CLI(rescan/plan/apply/status/recover,--json)。测试:tests/test_workflow_contract.py、tests/test_manage_cli.py。**Task 7 剩余**:JS update 分支(生成更新计划+accept_warning 二次确认 UI)、启动技能报告.command 同步、groups.json 分类筛选恢复、HTTP 边界负例(负 Content-Length/非对象/未知路由/Unicode token/超时/关停)、CSP hex→base64、浏览器实点(无浏览器则如实记录未验)。

### 最终交付记录(2026-09-05)

- 全量:230 项测试 0 失败 0 跳过(python3 scripts/verify.py 退出 0);基线 126 项语义未削弱。
- 关键红→绿:Task1 恶意 manifest/上限/原子发布 13 红→绿;Task2 apply 期保护翻转 6 项先放行后拒绝;Task3 校验异常丢实体/子进程 77 中断→可恢复;Task6 truncated 候选先放行后拒绝;Task8 计数先 12800/259120 后 80/3160;Task9 verify 反向(故意失败/skip-only/空目录)均非 0。
- 中断恢复证据:tests/test_transaction_recovery.py 三个 os._exit(77) 窗口(删除首目标移走/更新旧目录移走/恢复首实体发布)。
- 性能前后:见 tests/test_overlap_cost.py 输出与 docs/architecture.md 性能口径。
- 未验证(如实):浏览器实际点击验收(本会话未执行;CLI/API/静态链路有测试);真实 GitHub 全量下载、真实业务断电恢复未测(协议 fixture 覆盖)。
- Git 范围:scripts/ tests/ docs/ README/SKILL/AGENTS/.gitignore/PROGRESS/BLOCKED/examples/fixtures;真实 data/backups/客户端目录未动。

### 已知遗留/风险

- ResourceWarning 基线 24 条(Task 9 处理)。
- evaluate_review 尚未接入 report.py/queue 渲染(Task 7)。
- Task 8 冻结候选 gold fixture 未建(先于改算法)。
