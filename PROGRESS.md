# PROGRESS — skill-keeper v4 精简开源泛化(2026-09-05)

## 断点续跑须知(先读我)

- 计划来源:docs/superpowers/plans/2026-09-05-skill-keeper-open-source-upgrade.md(任务书:同目录 agent-brief;设计:docs/superpowers/specs/2026-09-05-skill-keeper-open-source-design.md)。按 Task 0–5 串行;每任务红测试→最小实现→相关测试→`python3 scripts/verify.py`→文档→小提交;阶段门槛全绿自动继续。
- 开工基线(实测):HEAD `f858d2ba89d82acc93bcd78e30f3bc6ac1b24b04`,Python 3.9.6,分支 case1,verify 233 项 0 失败 0 skipped;工作树原有三份计划文档(untracked,随 Task 0 入库)。
- 零退化合同:tests/fixtures/private-v311/(完全虚构,含 shared/Codex/WorkBuddy/Ego、自建、builtin owner、符号链接、重复加载、审查、备份、旧 CLI)+ tests/test_private_compatibility.py(9 项)。v4 各任务必须保持全绿。
- 测试入口:`python3 scripts/verify.py`(独立复核后 313 项 / 0 失败 / 0 skipped；v3.1.1 的 233 个冻结 ID 全在；`python -m scripts.verify` 同口径,CI 使用)。
- BLOCKED.md:无。
- Task 5 要点:.github/workflows/ci.yml 四 job(Ubuntu/Python 3.8、Ubuntu/主力、macOS/主力、Windows/主力,均 `pip install .` + `python -m scripts.verify`,真实 runner 不 mock 平台);SECURITY.md(位置声明不可信、变更边界、数据留存、私有漏洞报告);verify.py 增强(v3.1.1 原 233 项测试 ID 基线不减少 tests/fixtures/private-v311/v311-test-ids.json、安装 smoke=doctor JSON、恶意位置声明探针、模型输入不可写探针、tracked 文件个人路径/秘密模式扫描);README 重写为三种流程(已知客户端直扫/模型传根/登记 client-locations)+数据全留本机;AGENTS/architecture/SKILL 版本与命令同步(4.0.0);test_migrations_docs 的版本与 README 热度口径断言随文档同步更新。

## 独立复核记录(v4,2026-09-05)

- 发现原交付的 `verify.py` 在 unittest 运行后才收集测试 ID，导致 233 个基线 ID 全部误报缺失；同时附加检查失败后仍沿用旧 `passed` 返回 0。两处已修复并用故意失败反例锁定。
- 未知客户端声明已知共享根时，原实现为避免重复扫描把整条声明丢掉，报告因此不知道该客户端会读取共享库。现改为物理目录只扫描一次，同时用 `observation.reported_roots` 保留每个客户端→位置关系，未知客户端 `client_load` 与共享库视图均可见。
- 临时根现在必须严格位于当前用户 HOME 内，真实路径/符号链接越界拒绝；模型声明仍不可写。未知字段名与重复 JSON 键不再进入错误信息。
- 增加 `SKILL_KEEPER_HOME` 统一隔离 Windows/Unix 子进程，修复 Windows `expanduser("~")` 不服从测试 `HOME` 的风险；Windows checkout 若把 fixture symlink 物化为文本，测试会在临时 HOME 重建。
- FileLock 在后端异常和 PID 写入异常时都会解锁并关闭 fd。隐私门禁新增 Linux/Windows 用户路径与 tracked runtime 文件检查。
- 本地实测：macOS Python 3.9.6 与 3.12.13 均为 313 项、0 失败、0 skipped，附加五项检查全过；`git diff --check` 通过。Python 3.8/Linux/Windows 的真实 runner 仍需 push 后由 CI 给出最终证据。

## 原 Task 0–5 交付记录(v4,2026-09-05)

- 原交付运行了 303 项 unittest 且全部通过；独立复核发现当时附加基线检查实际失败但退出码错误地为 0。修复后当前为 313 项，基线 233 项 ID 全部保留。
- 阶段门槛:Task 3 后阶段 A 七模块 58 项全绿;Task 5 最终门槛 verify + `pip install .`(venv 实装 metadata=skill_keeper-4.0.0,console script 离线运行 doctor/scan/report)+ `git diff --check` 干净。
- 安装 smoke:doctor --json 输出 version/python/layout/paths/lock_backend;打包测试在临时 venv 安装后从仓库外离线运行,并按布局断言(安装态=新默认 ~/.skill-keeper)。
- 恶意位置声明/模型不可写:verify 探针 + 20 项专项测试(白名单外字段整体拒绝、错误不回显值、临时声明零持久化、policy/service 两层拒绝、create_remove_plan 拒绝)。
- 事务故障恢复:既有 POSIX 中断窗口(os._exit(77) 三处)继续全绿;新增锁竞争、预检失败零副作用、第二目标失败、rename/replace 故障注入。
- 私人兼容快照:tests/fixtures/private-v311(完全虚构)+ 9 项冻结测试;私人部署 old-repo 布局/锁/暂存零退化。
- CI:四 job 已于 2026-09-05 实际全绿(push 后修复 7 轮:backup fsync 改 r+b 句柄、仓库布局冻结测试按 detect_repo_layout 分支、3.8 的 dict | 改 {**a, **b}、CI 级 UTF-8 环境变量、fixture 链接绝对目标重建(islink 自检;3.12 islink 不认 junction)、CPython 3.12 Windows mkdtemp 对 PermissionError 静默重试→注入改 patch tempfile.mkdtemp、verify 加 faulthandler 600s 看门狗、只读计划文件 replace_atomic 摘只读位重试)。已合并 main(f858d2b→166d1a9 快进),tag v4.0.0 并发布 GitHub Release(经用户授权)。
- Git 范围:scripts/ tests/ docs/ pyproject.toml .github/ SECURITY.md README/SKILL/AGENTS/.gitignore/PROGRESS/BLOCKED/data examples;真实 data/backups/客户端目录未动;分支 case1(f858d2b → 本提交,未 push)。
- Task 4 要点:scripts/core/preflight.py(preflight_target_directory:目标同目录唯一临时对象验证 mkdir/写+fsync/同目录 rename/文件 replace/父目录 fsync/清理,清理不干净也拒绝);changes._preflight_parents 接入 apply 三条路径(remove/update 在 targets 解析后、备份前;restore 在 state 就绪后);policy.check_action 对 evidence 含 model-declaration 的实例一律拒绝(reason_code=model-declared-location,restore 也不放行);service.plan_action 在 create_remove/update_plan 之前 _refuse_model_declared 二层拦截。**改既有故障测试的口径**:引擎级 rename 计数注入(test_change_faults 两处)与子进程中断窗口(test_transaction_recovery 两处)须先 patch preflight 为 no-op —— 它们测引擎回滚/中断,预检故障由 test_cross_platform_preflight 专项覆盖;restore 子进程窗口的 spy 带 dst 过滤不受影响。
- Task 3 要点:scripts/core/location_input.py(parse_declaration/parse_cli_roots,白名单 schema_version/client/observed_by/complete/roots[path/scope/load_state];64KiB/32根/4KiB字符串/6层嵌套/UTF-8;load_state 只认 reported;错误不回显值或白名单外键名);scan.py --root/--locations-json FILE|- 接线(拒绝发生在扫描与落盘之前;stdin 按 64KiB+1 读取);_model_locations 产 mutable=False Location,同一物理根只扫一次并以 observation.reported_roots 保留全部客户端读取关系;缺失根记黄灯 model-root-missing;_structural_findings 为自报客户端补 duplicate-load("等待本地确认"口径);report.py 标注"客户端自报";临时声明不写长期配置、零变更入口(测试锁定)。
- Task 2 要点:scripts/core/platform.py(lock_backend_name/try_lock_exclusive/unlock_fd/is_absolute_path,fcntl 与 msvcrt 延迟导入);io.FileLock 改走 platform(接口/非阻塞语义不变,锁文件绝不静默清除);paths.validate_relative_path 增加盘符组件拒绝(`C:` 即使相对外形也拒),validate_archive_member_path 是其显式别名(assertIs 锁死同一实现);scan._extra_locations 改 os.path.isabs 原生判断;锁竞争测试用真实双进程(clean 模式经 stdin 放行避免释放竞态;crash 模式 os._exit(3) 验证 OS 释放 + 锁文件保留);AST 检查全 scripts/ 无顶层无条件 fcntl/msvcrt 导入、backup/transactions/changes/staging 不碰 platform 辅助。
- Task 1 要点:scripts/cli.py 统一命令(scan/report/manage/doctor,手动分发直传参数——argparse REMAINDER 有吞参缺陷,勿改回子解析器);runtime.py 增 detect_repo_layout/default_layout_dirs/default_data_dir(优先级:显式参数>env>可识别旧仓库运行态>新默认 ~/.skill-keeper/{data,cache/staging,backups});scan/report/manage 主入口改 main(argv=None) 返回码;remove_skill/serve/check_updates/value_review 数据目录共用同一解析链;pyproject.toml PEP 621(version 动态读 scripts.__version__;本地 setuptools 58 不支持 PEP 621,构建隔离下安装正常);安装烟测走 python -m venv + pip install .(需网络取构建后端,装好完全离线)。

## 任务状态

| 任务 | 状态 | 提交 |
|---|---|---|
| Task 0 冻结私人版零退化合同 | 完成 | 5bfb7fd |
| Task 1 统一安装、CLI 和运行态 | 完成 | 3095bc5 |
| Task 2 最小跨平台底座 | 完成 | 0113cef |
| Task 3 模型位置声明与未知客户端盘点(阶段 A 达成) | 完成 | af7f8a2 |
| Task 4 apply 前真实目标预检 | 完成 | 32a202b |
| Task 5 CI、文档与最终验收 | 完成 | 本提交(2026-09-05) |
| 独立复核与缺口修复 | 本地完成，CI 待跑 | 本次复核提交 |

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

## 全面审查修复·第一阶段(2026-09-06)

依据 docs/reviews/2026-09-06/README.md(F01–F13),本轮执行第一阶段「修通用户真正走的路径」:F01–F06、F08、F09。基线 9c5c5eb,分支 codex/1,本地提交(未 push,发布仍需用户授权)。

- **F01 报告 JS 语法错误**:交互脚本从 Python 三引号字符串迁出为真实资源 `scripts/assets/report.js`(更新分支 `\n` 转义不一致导致整段 JS 解析失败、所有按钮失效);pyproject package-data 随包分发;新增 tests/test_report_frontend.py:最终产物 node --check(CI 四平台有 node)+ 纯 Python「字符串字面量裸换行」扫描器兜底 + data-act→JS 分支合同 + 静态/服务共用同一资源。
- **F02 GitHub 下载器**:递归树合法 `type=tree` 目录条目放行(仍做路径安全/重复校验,submodule 继续拒绝);Base64 先剥离协议允许的空白再 validate=True 严格解码(GitHub 按 60 字符折行)。真实形状 fixture:带目录条目的树 + 折行 Base64。
- **F03 有效性统一**:报告 build_view 改按稳定 instance ID 连接历史(内容变化不再把旧结论当全新未审查对象),全部走 review_state.evaluate_review;「目标内容变化」与「替代品变化/消失」分开标注(安检失效 vs 删除建议失效,不再单一绿标);队列 build_review_queue 同样接入 evaluate_review 并回填 previous_review_reasons;record_review 支持 inventory 参数——受保护替代品的依赖快照从全量安装索引取,不再记成未知;value_review record CLI 自动附带 inventory。
- **F04 路径统一**:report.main/backups_list、serve.ServiceContext、check_updates.staging_root_for 全部只经 RuntimePaths 解析(旧仓库/新安装 ~/.skill-keeper/显式 env 三态一致),消除报告看不见备份、跨入口恢复找不到备份、staging 平台缓存漂移;render_html 的 groups.json 改由 ctx 传入。
- **F05 并发合同**:recover_transaction 全程持变更互斥锁;apply 前新增 _assert_no_conflicting_transactions——其他计划未完成事务占用相关路径(prepared/mutating/rolling-back/recovery-required,目标/保管/candidate 保管路径父子交集,Windows normcase)即拒绝并指出阻断计划;恢复目标解耦出 _restore_txn_targets(冲突检查先于状态落盘);scan 跳过 .sk-txn-* 保管目录不计入盘点。
- **F06 提交后刷新失败**:publish_snapshot 捕获 TimeoutExpired/OSError 返回结构化 stale;AppService.apply_action 再兜一层异常——响应永远 committed+stale,不谎报普通失败;serve 新增「🔄 刷新报告」按钮+refresh 分支,run_scan_report 继承 paths 环境、退出码 0/1 都算运行成功、超时/启动失败返回 False。
- **F08 保护配置**:known-sources.json 内部条目损坏(值非对象/缺非空 type)→ load_policy 判 unhealthy 并报出来源+条目名,plan/apply 一律拒绝;只读盘点(load_user_config)保持宽容。
- **F09 安检续办**:网页流程改为 计划→对本计划安检记账(/api/vet,plan_id+candidate_hash 绑定不变)→确认执行;计划公开字段带 candidate_hash/repo/commit_sha;CLI 新增 `manage.py vet <plan_id> --verdict safe|warning --evidence ...`;网页与服务层共用 AppService.vet_candidate。
- 验收:`python3 scripts/verify.py` 338 项 0 失败 0 跳过(233 冻结 ID 保留),`git diff --check` 通过。新增/强化回归:frontend 合同 4 项、GitHub 真实形状 3 项、政策损坏条目 1 项、审查生命周期集成 4 项、并发合同 3 项、路径一致/刷新失败 4 项、vet 流程 4 项、scan 保管目录 1 项、打包携带 JS 1 项。
- 未验证(如实):真实浏览器点击回归未跑(语法/合同层已覆盖);四平台 CI 需 push 后由 Actions 给出最终证据;F07/F10–F13 与报告视图分离属第二/三阶段,尚未执行。

## 全面审查修复·收尾轮开工回执(2026-09-06,任务书驱动)

- 基线实测:codex/1 @ aadcdba 干净;verify 338 项 0 失败 0 跳过(⛔ 行是 test_verify_acceptance 故意注入的反向探针,非真失败);benchmark 80=0.149s / 200=2.714s / 400=38.044s(RSS 149.1MiB),与审查记录同量级。
- 顺序:任务1 三个安全缺口(网页安检不得用 confirm 造证据/其他事务损坏 fail-closed/record_review 保存并比对政策版本)+真实浏览器全链路 → 任务2 F10–F13 数据口径统一 → 任务3 F07 等定点优化(400 项 ≥8 倍且 ≤5s,800 项 ≤30s,语义零差异) → 任务4 CLI/SKILL.md 精简 → 任务5 本地收尾;每阶段独立 commit,不 push。
- 最大风险:①F03 语义收紧(政策版本不可证明=needs-recheck)会让存量记录批量过期,报告语义变化需与"结论正确>性能"取舍一致;②400→800 项性能目标依赖语料密集度,若 800 项超 30s 需按"结果比基线差则回滚该项"处理;③浏览器验收受 confirm() 原生对话框自动化限制。

### 任务1 完成(2026-09-06):封住第一阶段的假绿

- 缺口1(网页安检):/api/vet 空 evidence 一律 400,删除"网页确认=自动造证据"逻辑;JS 更新流程改为 计划建立→暂停展示安检面板(可复制的正式 `manage.py vet` 命令+可续办执行命令)→「我已完成安检,继续执行」按钮,网页脚本不再携带 verdict 调 /api/vet。红→绿:test_serve_vet_endpoint(空证据 400)、test_update_flow_pauses_for_formal_vetting 先 5 连红后全绿。
- 缺口2(fail-closed):其他事务状态文件损坏 → 新资产写操作拒绝并指出损坏文件(此前静默跳过)。红→绿:test_corrupt_other_transaction_blocks_apply_fail_closed。
- 缺口3(政策版本):record_review 保存实际 review_policy_version;evaluate_review 缺版本=policy-version-unproven、不一致=policy-changed,均 needs-recheck;当前政策版本可显式传入比对。红→绿:test_record_saves_actual_policy_version/test_unprovable_policy_version_expires。旧测试夹具补齐版本字段(语义更严,非放宽)。
- 浏览器验收(IAB 真实点击,临时 HOME/DATA/STAGING,confirm 用记录式替身模拟用户点确定——IAB 后端会自动取消原生对话框,已如实记录):①顶部指标跳转 hash=#verdict-unreviewed+区块展开在视口;②「复制安检命令」剪贴板捕获完整 vet 命令;③更新续办:面板展示 plan-20260906-161351-de58f985+正式命令→未安检点继续被拒("❌ 尚未安检")→CLI 带 evidence 安检→点继续→v2 落盘+事务 committed;④删除 demo→两段 confirm→committed+刷新失败诚实 toast→点刷新报"❌ 重扫失败"→修复可读性后再刷新成功→报告出现备份行;⑤恢复按钮→demo 回原位(demo body v1 落盘)。
- verify 341 项 0 失败 0 跳过(基线 233 保留)。

### 任务2 完成(2026-09-06):F10–F13 数据口径统一

- **F10 加载模型唯一化**:插件多版本"只有最高版本参与加载"的判定移入 observations.effective_loaded,evaluate_load 统一应用;scan._client_load_stats 改为从 evaluate_load(全局上下文) 派生(entries=eligible、重复=评估内 duplicates),未知客户端经 eligible_location_ids 显式覆盖走同一评估核;duplicate-load 发现同步改为评估派生 + 新增"工作区内同名"口径(标明工作区,不再冒充全局启动占用),跨项目同名不再误报。红→绿:test_cross_project_same_name_not_global_duplicate(病灶:2 条+重复告警)、test_client_load_and_load_contexts_agree_everything。
- **F11 观察不完整如实报错**:client-locations.json 损坏 → observation.issues + complete=false(scan 退出 2),config_issues 保留解析细节;report --json 的 operational_ok 反映 observation.complete,不完整退出 2(此前硬编码 True);check_updates 输入缺失/损坏时不再覆盖已有 updates.json(仅在无历史输出时落盘)。
- **F12 回滚基准**:apply_plan 事务的 original_hash 取计划前置哈希(执行前已验证与磁盘一致),不再取可能过期的 inventory tree_hash。红→绿:test_update_rollback_with_stale_inventory_is_rolled_back(病灶复现:recovery-required 假告警)。
- **F13 指纹与身份**:同轮扫描按真实内容根复用指纹(_scan_entry hash_cache,同 Skill 多客户端入口只哈希一次);读不出指纹的实例按稳定实例身份隔离成独立逻辑条目,绝不按空哈希合并;tree_hash 文档写明"根目录自身元数据不在指纹内,由 root_real/is_symlink 前置+预检另行校验"的边界合同。红→绿:test_tree_hash_computed_once_per_real_root(2 次→1 次)、test_unreadable_instances_are_isolated_not_merged。
- **view 分离**:本机应用存在性等环境探测移到 main 输入收集(ctx.claude_app_present),render_html/render_md 只消费 view(反向验证:夹具触发探测分支时旧实现确实变红)。
- **双布局完整入口回归**:显式数据目录(env)布局 CLI 全链路(doctor→scan→report→plan→apply→备份→restore)一次通过;新默认 ~/.skill-keeper 布局回归放进 test_packaging_install 真安装态执行(仓库 checkout 自身是 old-repo 布局,从仓库内无环境变量运行 CLI 会指向真实运行态——产品行为如此,测试绝不那样跑,已如实记录)。真实 data/ 零改动已核实(inventory mtime 未变)。
- verify 356 项 0 失败 0 跳过。
