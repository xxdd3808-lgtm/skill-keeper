# PROGRESS — skill-keeper 可信性优化(2026-09-05)

## 断点续跑须知(先读我)

- 计划来源:docs/superpowers/plans/2026-09-05-skill-keeper-optimization.md(任务书:同目录 agent-brief)。按 Task 0–9 串行;每任务红→绿→全量 unittest→提交。
- 测试入口:`python3 -m unittest discover -s tests`(当前 203 项全绿,0 skipped)。
- 阶段门槛:A=Task 1–3(已完成);B=Task 7;C=Task 9。BLOCKED.md 当前"无"。
- 注意:tests/test_review_lifecycle.py 曾被我一次坏编辑合并过行,已修复;不要再对该文件做"删尾随换行"类编辑。

## 任务状态

| 任务 | 状态 | 提交 |
|---|---|---|
| Task 0 基线 | 完成 | 3b8c33a(203 项时的基线=126) |
| Task 1 备份/恢复合同 F01 F02 | 完成 | 63fc239 |
| Task 2 执行策略与输入校验 F04+F07边界 | 完成 | 077e9c1 |
| Task 3 事务与中断恢复 F03(阶段A达成) | 完成 | 43d7155 |
| Task 4 观察完整性 F05 | 完成 | ca1bcc4 |
| Task 5 审查历史与有效性 F06 | 完成 | 979f2c1 |
| Task 6 完整候选与缓存生命周期 F07 | 未开始 | |
| Task 7 CLI/API/报告闭环 F08(阶段B) | 未开始 | |
| Task 8 去重计算与外部运行态 F09 F10 | 未开始 | |
| Task 9 验收入口与文档 F11(阶段C) | 未开始 | |

## 各任务要点(后续任务要消费的事实)

- Task 1:scripts/core/paths.py(validate_relative_path/confined_destination);backup.py 严格 validate_backup_manifest+资源上限(MAX_MANIFEST 8MiB/ENTRIES 100k/FILE 512MiB/TOTAL 2GiB,常量可 patch 测试);create/verify/restore 全重写(原子发布、完整往返、按实际落地清单撤销);恢复计划绑定 archive_sha256+restore_targets;macOS 只读目录跨父 rename 会 EACCES→发布后再还原根权限。
- Task 2:policy.py(load_policy/check_action/validate_candidate_vet;自建默认拒删;配置损坏拒写;known_sources 参数只能加保护);staging.py(validate_staging_root/record_ownership/cleanup_staging;只清本工具登记且无引用的 cand-*/tmp-*);计划含 is_symlink/root_real 前置键;_load_plan 结构校验(前置键白名单 regex);ChangePlan 新增 reason/recommendation_id(digest 归一化剥默认值,旧计划兼容)。
- Task 3:transactions.py(状态文件 data/transactions/<plan>.json,phase 机,holding_path=.sk-txn-<plan尾8>-<iid前8>);删除=原子移入同目录保管;更新=旧版保管+候选物化再交换;恢复=restore_backup+哈希匹配撤销;recover_transaction 只回退不激活;重放 committed 返回 already_applied,rolled-back 拒绝;审计失败→audit_pending;resulting_hash=真实哈希 JSON;子进程 os._exit(77) 三窗口可恢复;_undo_remove 两遍(先全移回再校验,链接依赖正本)。.gitignore 加 data/transactions/。
- Task 4:fingerprint.py FingerprintError(OSError 子类)+collect_errors 参数+排除目录剪枝;scan.py parse_frontmatter_detailed(嵌套 requires.bins,unsupported 警告码)+实例 content_status+observation{complete,issues,observed_scope,rule_version,load_contexts}+scan --json need_vet 真实+退出码 0/1/2;load_rules.py(规则带来源/日期/范围,RULE_VERSION);observations.py(evaluate_load eligible≠confirmed/load_receipt_evidence 白名单);插件坐标加 marketplace;check_updates 输入缺失退出 2。
- Task 5:review_state.py(review_dependencies/evaluate_review,REVIEW_POLICY_VERSION);record_review 校验 safety∈safe|warning|danger、reviewer_model 非空、提交 hash 一致、生成 review_snapshot_id+alternatives_state(候选无条目行记 None→evaluate 按 alternative-unverified 过期);value_review record 台账 FileLock+损坏拒写。CONFIDENCE_LEVELS 是 高/中/低(测试别用 medium)。队列/报告共用 evaluate_review 的接线放到 Task 7 报告改造时做(计划允许,不另开任务)。

## 已知遗留/风险

- ResourceWarning 基线 24 条(Task 9 处理)。
- evaluate_review 尚未接入 report.py/queue 渲染(Task 7)。
- Task 8 冻结候选 gold fixture 未建(先于改算法)。
