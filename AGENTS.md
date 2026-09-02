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
python3 scripts/remove_skill.py plan --instance-id <id> --reason <理由>        # 生成不可变删除计划
python3 scripts/remove_skill.py apply <plan_id> --digest <digest> --confirm   # 确认执行(先备份,失败回滚)
python3 scripts/make_sample_report.py      # 固定虚构 fixture → examples/report-sample.html
```

退出码约定:scan / report / check_updates 的 `--json` 模式,0=健康/无差异,1=有红色问题/有差异;remove_skill 旧式目录名用法一律退出 2 并打印迁移说明。

## 技术栈

纯 Python 3.8+ 标准库;可选 PyYAML(仅影响 frontmatter 的 yaml-validation 展示,不影响核心字段)、gh CLI(GitHub 证据与候选拉取,可缺席并降级)。无构建步骤、无第三方依赖。`unittest` 全量测试:`python3 -m unittest discover -s tests`(全部使用临时 HOME fixture)。

## 目录与约定

- 脚本用 `os.path.realpath(__file__)` 反推项目根,不依赖调用路径;项目实体可整体迁移,客户端发现靠符号链接(如 `~/.agents/skills/skill-keeper` → 项目根)。
- 个人配置(gitignore):`data/groups.json`、`data/self-built.txt`、`data/known-sources.json`、`data/ignore.json`、`data/workspace-locations.txt`、可选 `data/client-locations.json`(登记适配器未内置的客户端目录)。
- 运行时产物(含个人数据,永不入库):`data/inventory*.json`、`data/updates.json`、`data/reputation.json`、`data/review-queue.json`、`data/value-reviews.json`、`data/vetted*.json`、`data/report.*`、`data/actions.log`、`data/audit-v2.jsonl`、`data/change-plans/`、`data/staging/`、`data/migrations/`、`backups/`。
- 铁律:扫描/报告/更新检查/队列只读;所有变更走不可变 ChangePlan → 用户确认 digest → 互斥锁 → 创建并验证备份 → 原子执行 → 验证(失败自动回滚)→ 审计(`data/audit-v2.jsonl`);变更目标只能是 inventory 里的稳定 instance ID;不修改客户端插件缓存;客户端配置按字段白名单读取,token/key/cookie/env 一律不碰;GitHub 星数只是热度参考,任何单一因素不能自动触发删除,系统永不自动删除;known-sources 登记 `builtin-app` 的应用内置 Skill 受保护,删除/更新计划直接拒绝,处置走所属客户端。
- 数据 schema:`schema_version=2`,JSON 原子写入(临时文件 + fsync + os.replace)。
- 提交前自查:`git grep` 不得出现真实 skill 清单、个人路径或个人配置内容。

## 当前状态与下一步

- v2.0.0(2026-08-31):多客户端适配器发现(七类客户端 + 插件缓存 + 工作区 + `client-locations.json` 自定义;marketplace 商品目录不算已安装,Haha 复用 Claude/共享只加 alias,Cindy 投影只读,Accio 账号编号哈希化);完整目录树指纹(相对路径/类型/权限/链接目标/全部内容,辅助脚本变化也会让安检与审查过期);inventory v2(位置/实例/逻辑身份三层,重复加载与链接漂移结构化 findings,ignore 只翻标记不丢问题);来源核实分级(自建白名单/客户端回执 > known-sources > 自述候选,名称前缀不能骗取免检);GitHub 仓库证据缓存(仓库级热度口径,stale 保留旧数据);重复/替代候选 + 大模型价值审查队列(五种结论,建议删除必须带理由/替代/损失/证据/置信度);带 manifest 的可往返备份与安全恢复(恶意归档/冲突/损坏全部安全失败);事务式更新(固定候选 staging、先安检后激活、原子交换、失败回滚);本地服务两阶段 plan/apply API(64KiB 上限、严格 confirm、Origin 校验、CSP 按内容 hash 白名单);v1 数据迁移(旧安检一律 needs-recheck,旧备份只检视不恢复);示例报告改用固定虚构 fixture。
- v2.0.0 发布版(2026-09-01):替代品口径收紧——替代候选只认本机已安装逻辑 skill(≤8 个、可为零;同名孪生/同仓库版本差/多客户端副本不互为替代),「建议删除」记账必须指名本机已安装替代品的逻辑 ID,无 `benchmark:` 证据不得断言性能优势;known-sources 的 `builtin-app` 纳入受保护(见铁律);来源白名单真正接入审查队列(此前自建 skill 会误入第三方队列);热度缓存嵌套损坏可自愈,报告热度按各 Skill 自己的仓库归属;更新暂存保留 Git 可执行位,共享候选目录不再被"已最新"副本误删;36 个第三方 Skill 已全部价值审查记账(保留 19 / 观察 13,台账 `data/value-reviews.json`),WorkBuddy 的 neat-freak 漂移副本经用户确认删除,6 个候选更新已按 plan/apply 应用(Accio 与共享库的 pdf/skill-creator/skill-vetter 副本更新后合并为单一逻辑身份);全 Git 历史已清洗个人路径。
- v2.1.0(2026-09-02):**客户端加载拓扑模型**——重复加载检测从"只算 ZCode"改为按每个客户端真实读取的位置集合逐个判定(Codex 2026-08-25 起自动导入 `~/.agents/skills`,适配器补上 `~/.codex/skills/.system` 真实路径与三层插件市场布局);插件缓存同插件多版本只按最高版本计入加载,旧版本记 `stale-plugin-version` 缓存残留(info);新增 `builtin-app-spread`(应用内置技能进共享库会被所有客户端加载)、`wrapper-double-load`(Haha 双读聚合一条);inventory 新增 `client_load` 统计,报告顶部新增「各客户端加载上下文」总览;修复 HTML 报告备份区渲染崩溃(9-01 起网页版未刷新)。同日执行加载去重(用户授权,备份 `backups/manual-20260902-load-cleanup/`,审计 4 条):autoglm 五件套从共享库移入 `~/.zcode/skills`(仅 ZCode 加载)、ego-browser 收回 Ego/Accio/WorkBuddy、`~/.codex/skills` 与共享库重复的 5 份处理(cai-bao-v4.1 正本入共享库)、共享库 pdf/skill-creator 删除(各客户端用各自插件/内置版);结果 ZCode 55→52、Codex 83→70、Claude 48→40,三端重复 7/2/0 → 全部 0。
- 候选改进:更多客户端目录适配(可用 `data/client-locations.json` 登记,或增删 `scripts/core/clients/` 适配器)、报告主题、按需增量扫描、skills.sh 市场真实下载量接入、Haha 双载的镜像策略。
