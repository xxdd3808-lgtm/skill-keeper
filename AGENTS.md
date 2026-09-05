# AGENTS.md — skill-keeper

一句话定位:本地 Agent Skill 管家——盘点本机全部客户端(ZCode / Codex / Accio Work / WorkBuddy / Claude Code / Claude Code Haha / Cindy / Ego / 共享与工作区)的 agent skill:功能/来源/配套客户端、体检健康问题、第三方 Skill 价值审查(有 GitHub/市场证据与重复/替代分析)、安全变更闭环;本身以 skill 形态存在(触发词见 SKILL.md)。

## 怎么跑

```bash
python3 scripts/scan.py                    # 多客户端适配器发现 + 完整树指纹 → data/inventory.json(只读)
python3 scripts/report.py                  # v2 价值审查报告:data/report.md + report.html
python3 scripts/report.py --serve          # 交互报告:plan/apply 两阶段网页操作(仅 127.0.0.1+token)
./启动技能报告.command                      # macOS 双击启动同一交互服务(自动开浏览器)
python3 scripts/check_updates.py           # 完整树 vs 固定候选树比对(只读)→ 暂存候选 + data/updates.json
python3 scripts/value_review.py queue      # 生成第三方价值审查队列(只读)
python3 scripts/value_review.py record --file <review.json> --model <模型名>   # 审查记账
python3 scripts/manage.py plan remove/update/restore --json                   # 计划(与网页共用 service 层)
python3 scripts/manage.py apply <plan_id> --digest <digest> --confirm --json  # 确认执行(备份+事务+可恢复)
python3 scripts/manage.py status/recover <plan_id> --json                     # 事务状态 / 中断恢复
python3 scripts/make_sample_report.py      # 固定虚构 fixture → examples/report-sample.html
python3 scripts/verify.py                  # 验收入口:unittest 实际结果,0 失败 0 跳过才算过

当前版本 3.0.0(2026-09-05 可信性优化,F01–F11,详见 docs/architecture.md 与 PROGRESS.md)。
```

退出码约定:scan / report / check_updates 的 `--json` 模式,0=健康/无差异,1=有红色问题/有差异;remove_skill 旧式目录名用法一律退出 2 并打印迁移说明。

## 技术栈

纯 Python 3.8+ 标准库;可选 PyYAML(仅影响 frontmatter 的 yaml-validation 展示,不影响核心字段)、gh CLI(GitHub 证据与候选拉取,可缺席并降级)。无构建步骤、无第三方依赖。`unittest` 全量测试:`python3 -m unittest discover -s tests`(全部使用临时 HOME fixture)。

## 目录与约定

- 脚本用 `os.path.realpath(__file__)` 反推项目根,不依赖调用路径;项目实体可整体迁移,客户端发现靠符号链接(如 `~/.agents/skills/skill-keeper` → 项目根)。
- 个人配置(gitignore):`data/groups.json`、`data/self-built.txt`、`data/known-sources.json`、`data/ignore.json`、`data/workspace-locations.txt`、可选 `data/client-locations.json`(登记适配器未内置的客户端目录)。
- 运行时产物(含个人数据,永不入库):`data/inventory*.json`、`data/updates.json`、`data/reputation.json`、`data/review-queue.json`、`data/value-reviews.json`、`data/vetted*.json`、`data/report.*`、`data/actions.log`、`data/audit-v2.jsonl`、`data/change-plans/`、`data/migrations/`、`backups/`;候选暂存一律放系统缓存目录(macOS `~/Library/Caches/skill-keeper/staging`,`SKILL_KEEPER_STAGING` 可覆盖)——**绝不放仓库 `data/` 内**,否则会被 ZCode 技能面板经符号链接递归扫成重复技能。
- 铁律:扫描/报告/更新检查/队列只读;所有变更走不可变 ChangePlan → 用户确认 digest → 互斥锁 → 创建并验证备份 → 原子执行 → 验证(失败自动回滚)→ 审计(`data/audit-v2.jsonl`);变更目标只能是 inventory 里的稳定 instance ID;不修改客户端插件缓存;客户端配置按字段白名单读取,token/key/cookie/env 一律不碰;GitHub 星数只是热度参考,任何单一因素不能自动触发删除,系统永不自动删除;known-sources 登记 `builtin-app` 的应用内置 Skill 受保护,删除/更新计划直接拒绝,处置走所属客户端;条目可选登记 `owner`(所属客户端)——owner 位置的正本照旧拒绝,非所属位置的散布快捷方式允许走正规 plan/apply 删除收回(v3.1.1),update 不享受口子。
- 数据 schema:`schema_version=2`,JSON 原子写入(临时文件 + fsync + os.replace)。
- 提交前自查:`git grep` 不得出现真实 skill 清单、个人路径或个人配置内容。

## 当前状态与下一步

- v2.0.0(2026-08-31):多客户端适配器发现(七类客户端 + 插件缓存 + 工作区 + `client-locations.json` 自定义;marketplace 商品目录不算已安装,Haha 复用 Claude/共享只加 alias,Cindy 投影只读,Accio 账号编号哈希化);完整目录树指纹(相对路径/类型/权限/链接目标/全部内容,辅助脚本变化也会让安检与审查过期);inventory v2(位置/实例/逻辑身份三层,重复加载与链接漂移结构化 findings,ignore 只翻标记不丢问题);来源核实分级(自建白名单/客户端回执 > known-sources > 自述候选,名称前缀不能骗取免检);GitHub 仓库证据缓存(仓库级热度口径,stale 保留旧数据);重复/替代候选 + 大模型价值审查队列(五种结论,建议删除必须带理由/替代/损失/证据/置信度);带 manifest 的可往返备份与安全恢复(恶意归档/冲突/损坏全部安全失败);事务式更新(固定候选 staging、先安检后激活、原子交换、失败回滚);本地服务两阶段 plan/apply API(64KiB 上限、严格 confirm、Origin 校验、CSP 按内容 hash 白名单);v1 数据迁移(旧安检一律 needs-recheck,旧备份只检视不恢复);示例报告改用固定虚构 fixture。
- v2.0.0 发布版(2026-09-01):替代品口径收紧——替代候选只认本机已安装逻辑 skill(≤8 个、可为零;同名孪生/同仓库版本差/多客户端副本不互为替代),「建议删除」记账必须指名本机已安装替代品的逻辑 ID,无 `benchmark:` 证据不得断言性能优势;known-sources 的 `builtin-app` 纳入受保护(见铁律);来源白名单真正接入审查队列(此前自建 skill 会误入第三方队列);热度缓存嵌套损坏可自愈,报告热度按各 Skill 自己的仓库归属;更新暂存保留 Git 可执行位,共享候选目录不再被"已最新"副本误删;36 个第三方 Skill 已全部价值审查记账(保留 19 / 观察 13,台账 `data/value-reviews.json`),WorkBuddy 的 neat-freak 漂移副本经用户确认删除,6 个候选更新已按 plan/apply 应用(Accio 与共享库的 pdf/skill-creator/skill-vetter 副本更新后合并为单一逻辑身份);全 Git 历史已清洗个人路径。
- v2.1.0(2026-09-02 上午):**客户端加载拓扑模型**——重复加载检测从"只算 ZCode"改为按每个客户端真实读取的位置集合逐个判定(Codex 2026-08-25 起自动导入 `~/.agents/skills`,适配器补上 `~/.codex/skills/.system` 真实路径与三层插件市场布局);插件缓存同插件多版本只按最高版本计入加载,旧版本记 `stale-plugin-version` 缓存残留(info);新增 `builtin-app-spread`(应用内置技能进共享库会被所有客户端加载)、`wrapper-double-load`(真双读包装器聚合一条);inventory 新增 `client_load` 统计,报告顶部新增「各客户端加载上下文」总览;修复 HTML 报告备份区渲染崩溃(9-01 起网页版未刷新)。同日执行加载去重(用户授权,备份 `backups/manual-20260902-load-cleanup/`,审计 4 条):autoglm 五件套从共享库移入 `~/.zcode/skills`(仅 ZCode 加载)、ego-browser 收回 Ego/Accio/WorkBuddy、`~/.codex/skills` 与共享库重复的 5 份处理(cai-bao-v4.1 正本入共享库)、共享库 pdf/skill-creator 删除(各客户端用各自插件/内置版);结果 ZCode 55→52、Codex 83→70、Claude 48→40,三端重复 7/2/0 → 全部 0。
- v2.1.1(2026-09-02 下午):**修复两处漏报/误报**——① ZCode「已安装技能」面板会顺着 `~/.agents/skills/skill-keeper` 符号链接递归扫描,把仓库 `data/staging` 里的历史候选树(aihot/brainstorming/neat-freak/skill-creator)当技能重复列出(用户看到 aihot/brainstorming 各两条);修复 = 清除残留(审计在册)+ 候选暂存迁至系统缓存目录 + check_updates 增加"历史残留清扫"(旧版只清本次暂存)+ 新增 `nested-skill-tree` 检测(单棵=info 疑似子技能设计,多棵=yellow;按真实路径去重)。② Haha 加载模型纠正:按 traces 核实 Haha 走 `~/.claude/skills` 镜像、不直接读共享库,撤销 shared→haha alias,消除 26 个幻影双载。Cindy(xdt-agents/xdt-codex 投影,2026-07-26 建)与 WorkBuddy(仅自有目录)复核无重复。同日傍晚用户卸载 Claude Code 只留 Haha:`~/.claude` 只剩 skills 镜像(28 链接有效),claude 插件缓存(minimax/mattpocock)随之消失;Haha 检测改为 cc-haha 标志或已装应用(仅真实 HOME 查 /Applications,不污染测试),报告把 claude-code 行标注"应用已卸载,目录实际由 Haha 读取";盘点降为 114 逻辑/164 实例,各端重复仍全部为 0。
- v2.1.2(2026-09-02 晚):**外部 Agent 删除的复核与防线加固**——用户经另一 AI Agent 清理 AutoClaw(gula00/autoclaw-skills)残留,删 18 个实例(autoglm×5 的 ZCode 副本与 `~/.openclaw-autoclaw/workspace/.opencode/skills/` 残留副本、automation-workflows×4、research-paper-writer×4),走了 plan/apply+备份+审计,4 个备份经 verify_backup 全部通过;复核确认后端 53699 端口无人监听(取 token 服务随 AutoClaw 消亡,删除无功能损失)。**发现的防线绕过**:autoglm 在 known-sources 登记为 builtin-app 本应"删除计划直接拒绝",但外部 Agent 直调 `create_remove_plan` 并省略 `known_sources` 参数(默认 None→不检查)绕过了保护;加固 = `create_remove_plan/create_update_plan` 在未传白名单时自动从 data 目录加载,显式传 falsy 也视同未提供(回归测试锁定)。残留:`~/.openclaw-autoclaw/`(46 个死技能及配置/日志,1126 文件/101MB)已于同日经用户确认整体清除(归档 `backups/manual-20260902-autoclaw-residual/`,审计在册);known-sources 里 6 条 autoglm builtin-app 登记已失效(实体不存在,留作防重装误判无害)。
- v2.1.3(2026-09-02):**报告与交互收尾**——`report.py` 顶部改为「资产概况 / 需要关注 / 价值结论」三组导航,非零指标可点击直达区块,红/黄灯和待更新项可定位到具体实例,大型明细默认折叠;健康问题按物理安装实例展示,同名不同实例不再串用问题。`report.py --serve` 将交互脚本改为带 token 的同源 `/report.js`,兼容严格 CSP;静态 `report.html` 仍保持单文件。示例报告与 126 项全量测试同步通过。
- v3.0.0(2026-09-05):**可信性与性能优化(F01–F11)**——备份/恢复合同测试与路径边界、执行两阶段策略复核(known_sources 损坏拒写、调用传参只增不减保护)、持结事务状态机(中断可恢复)、观察完整性(不完整即 exit 2 且禁变更)、审查结论有效性(内容/替代/政策变化自动过期)、暂存所有权与引用 GC、GitHub 树严格拉取、`manage.py` 统一 CLI、重叠索引复用(SKILL.md 读取 12800→80、相似对评分 259120→3160)、`verify.py` 验收入口;报告备份区改用 backup_id 恢复、静态命令仓库相对化。230 项测试。
- v3.1.0(2026-09-05):**报告共享库视图**——顶部「📂 共享库」指标直达专属区块,列出放在 `~/.agents/skills` 的全部逻辑 Skill 及其价值结论、其他占用客户端与 plan 入口(HTML/Markdown 双通道);安装实例明细客户端列改友好标签(shared→共享库)。用户反馈驱动:该事实此前只藏在明细表 client 列。231 项测试。
- v3.1.1(2026-09-05):**builtin-app 散布收回通道**——known-sources 的 builtin-app 条目可选登记 `owner`(所属客户端);策略按住址细分:owner 位置的正本删除/更新照旧拒绝,非所属位置的散布快捷方式允许走正规 remove(plan→确认→备份→事务→审计),update/位置缺失/未登记 owner 照旧拒绝。起因:ego-browser 共享库快捷方式正规收回被防线一刀切拒绝,只能手工修。233 项测试。
- 候选改进:更多客户端目录适配(可用 `data/client-locations.json` 登记,或增删 `scripts/core/clients/` 适配器)、报告主题、按需增量扫描、skills.sh 市场真实下载量接入、Haha 双载的镜像策略、扫描未知应用残留技能目录(如本次 `~/.openclaw-autoclaw` 在卸载后不可见,靠外部比对才发现)。
