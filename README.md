# skill-keeper · 本地 Agent Skill 管家(v2)

一个以「skill 形态」存在的本地 skill 管理工具:盘点 ZCode、Codex、Accio Work、WorkBuddy、Claude Code、Claude Code Haha、Cindy、Ego 及共享/工作区位置里的全部 agent skill——**功能 / 来源 / 配套客户端**一目了然;体检重复加载、链接漂移、损坏 frontmatter 等健康问题;为每个第三方 Skill 生成**有 GitHub 证据、重复与替代分析的价值审查结论**;所有删除/更新/恢复都走**计划 → 确认 → 备份 → 执行 → 验证**的安全闭环,系统永不自动删除。

适合装了多个 AI 客户端、skill 散落各处、说不清「装了什么、哪来的、值不值得留」的个人用户。

📊 **报告长什么样?** 用浏览器打开 [`examples/report-sample.html`](examples/report-sample.html)(由固定虚构数据生成,含受保护区、五种价值审查结论、热度口径说明与两阶段操作)。

## 它解决什么问题

- skill 装过就忘:来源、版本、用途、值不值得留,无从查起
- 同名 skill 散落在多个客户端目录,哪些真的被加载?哪些是白占上下文的重复副本?(盘点区分**发现 / 推断可加载(eligible)/ 确认加载(confirmed)**三层:位置在客户端读取集合内只是推断,没有运行时证据时不冒充"确认加载")
- 各客户端自带 / 插件自带 / 自建 / 第三方混在一起,不知道哪些真正需要审视
- 想删不敢删:没有可验证的备份,不知道删了会不会弄坏别的客户端
- 更新怕被坑:不知道上游改了什么,更怕"自动更新"装进来的不是你审过的内容

## 功能

### 盘点扫描 `scan.py`(只读)

- 客户端适配器发现:共享库、ZCode、Codex(个人/系统/插件缓存)、Accio Work(账号动态发现,账号编号哈希化)、WorkBuddy(connector/插件缓存;**marketplace 商品目录不算已安装**)、Claude Code(用户/插件缓存;marketplace checkout 不算)、Haha(只给 Claude/共享位置加复用标记,不重复计数)、Cindy(codex-home/系统/插件投影,只读)、Ego、工作区位置;适配器没覆盖的目录可在 `data/client-locations.json` 自行登记
- **完整目录树指纹**:相对路径、文件/目录/符号链接类型、权限、链接目标、全部文件内容都参与 SHA-256;任何辅助脚本、模板、参考文件变化都会改变指纹,旧安检与旧审查结论自动过期
- inventory v2 三层模型:位置(locations)/ 物理实例(instances,稳定 instance ID)/ 逻辑身份(logical_skills,按内容身份合并)
- 健康问题全部结构化:🔴 frontmatter 缺 name/description 或解析失败、悬空/循环链接、瘦身壳;🟡 ZCode 同名多份(双份占上下文)、链接漂移、依赖命令缺失、插件旧版本缓存;`data/ignore.json` 命中的问题只翻"已忽略"标记,绝不静默丢弃

### 价值审查(第三方 Skill)

- 来源核实分级:自建白名单(精确目录名)/ 客户端回执与 manifest > `known-sources.json` 已核实 > frontmatter 自述与 GitHub 搜索候选(**候选永远不能自动确认为来源;名称前缀不能骗取免检**)
- GitHub 仓库证据:stars、forks、是否归档、最近推送、贡献者规模、license、commit,**按仓库缓存**;网络失败保留旧数据并标"已过期"。**星数是仓库热度,不等于该 Skill 的真实使用人数**
- 重复/替代候选:完整指纹相同的精确副本、按 name/描述/正文/依赖/来源的可解释相似度、关键词重叠的替代候选(确定性代码只缩小范围,结论由大模型给出)
- **替代品口径**:替代 = **本机已安装、能覆盖主要用途、综合表现更好的另一个 Skill**。候选只从当前 inventory 的已安装逻辑 Skill 里产生(GitHub 上有但没装、marketplace 商品目录、staging 更新候选都不算);同一 Skill 的多客户端加载实例/符号链接孪生/同仓库版本差合并为同一逻辑身份,不互为替代;候选最多 8 个、可以为零,大模型必须比较核心功能、场景、兼容性、独特能力、维护、依赖与成本后才下结论——只覆盖部分功能给「观察」,没有 benchmark 不得断言性能优势
- 大模型逐项审查后给出五种结论之一:**💚建议保留 / 🔁优先保留另一个 / 👀观察 / 🗑️建议删除 / ❓需要人工确认**;「建议删除」必须携带理由、**本机已安装替代品的逻辑 ID**、删除后可能失去什么、置信度和至少两条可核实证据——**只有星数/热度不能构成删除依据**;内容一变结论即过期

### 更新检查 `check_updates.py`(只读)

- 对比**完整目录树**:本地树 vs 固定上游 commit 的候选树(候选按内容哈希暂存到系统缓存目录(`~/Library/Caches/skill-keeper/staging`,绝不放仓库 data/ 内——ZCode 技能面板会递归扫描仓库),远端后续变化不影响它)
- 只有四种客观状态:有候选更新 / 需审查 / 疑似本地定制 / 无法核实——**不给任何"改动少就可以直接覆盖"式的背书**
- 应用更新必须:候选安检通过 → 生成绑定本地哈希、来源、commit、候选哈希的更新计划 → 确认 digest → 原子交换(旧目录自动保留回滚)

### 报告 `report.py`

- Markdown + 交互 HTML:顶部按「资产概况 / 需要关注 / 价值结论」分组,先给结论(受保护类、第三方待审、五种结论各多少、未审查多少、待更新/复核多少),再给受保护区 + 价值审查卡片 + 实例明细 + 备份区
- 顶部非零指标可点击直达对应区块;红/黄灯和待更新项显示可处理清单,每条问题可继续定位到具体安装实例;大表默认折叠,避免报告打开即被长表淹没。健康问题按安装实例计数,同一个逻辑 Skill 的多个物理实例可能各产生一条记录
- 每张第三方卡片展示:结论与理由、主要依据、更值得保留的替代、独特能力、**删除后可能失去什么**、置信度、审查时间与模型、仓库热度口径提示、安检状态、候选更新状态;过期结论显著标注
- **一键操作** `report.py --serve`(macOS 可双击 `启动技能报告.command`):本地服务(仅 127.0.0.1 + 随机 token + Origin 校验 + 请求体上限),交互脚本通过带 token 的同源 `/report.js` 加载,网页按钮走两阶段——先生成计划(展示摘要+digest),确认后执行;全程记入 `data/audit-v2.jsonl`。静态打开 report.html 时仍是单文件,按钮退化为复制等价的安全 plan 命令(只含 instance_id)

### 安全删除 / 更新 / 恢复

```bash
# 删除:两阶段,不接受目录名
python3 ~/skill-keeper/scripts/remove_skill.py plan --instance-id <instance_id> --reason <理由>
python3 ~/skill-keeper/scripts/remove_skill.py apply <plan_id> --digest <digest> --confirm
```

- 计划不可变、30 分钟过期;执行 = 互斥锁 → 目标指纹复核 → 创建并验证备份(带 manifest,含位置/链接/权限/完整树摘要)→ 精确删除 → 验证(失败自动从备份恢复)→ 审计
- 备份可逐位置、逐字节恢复;恢复也走两阶段计划,目标已存在则冲突失败、不覆盖;旧格式备份只检视、不自动恢复
- 自建白名单(`data/self-built.txt`)、known-sources 登记 `builtin-app` 的应用内置、客户端自带/插件内容受保护,不进入第三方审查;应用内置出问题建议更新或卸载所属客户端,不能单独删除

### 自动化接口

`scan.py --json`、`report.py --json`、`check_updates.py --json`、`value_review.py queue --json`,退出码 **0=健康/无差异,1=有红色问题/有差异**,可挂 cron / launchd 定期巡检。

## 安装

```bash
git clone https://github.com/xxdd3808-lgtm/skill-keeper.git ~/skill-keeper

# 让各客户端发现这个 skill:做个符号链接(按你的客户端 skill 目录调整)
mkdir -p ~/.agents/skills && ln -s ~/skill-keeper ~/.agents/skills/skill-keeper

# 初始化个人配置(已 gitignore,不会被提交)
cd ~/skill-keeper/data
cp groups.example.json groups.json
cp self-built.example.txt self-built.txt
cp known-sources.example.json known-sources.json

# 可选:工作区级 skill 扫描、自定义客户端目录、忽略规则
cp workspace-locations.example.txt workspace-locations.txt
cp client-locations.example.json client-locations.json
# ignore.json 格式见 SKILL.md「数据文件」表;没有它就不忽略任何问题

# 首次盘点 + 报告
python3 ~/skill-keeper/scripts/scan.py
python3 ~/skill-keeper/scripts/report.py && open ~/skill-keeper/data/report.html
```

依赖:Python 3.8+;可选 PyYAML(只影响 frontmatter 校验展示,不影响核心扫描结果)、gh CLI(GitHub 证据与候选拉取,缺席时自动降级并明确标注)。

## 扫描位置与客户端

| 位置 | 归属客户端 |
|---|---|
| `~/.zcode/skills` + `~/.zcode/cli/plugins/cache/**` | ZCode(缓存只读,识别旧版本) |
| `~/.agents/skills` | 共享库(Haha 复用时只加标记,不重复计数) |
| `~/.claude/skills` + `~/.claude/plugins/cache/**` | Claude Code(Haha 启动器存在时同一位置标注复用) |
| `~/.codex/skills`、`~/.codex/skills/.system`(兼容旧布局 `~/.codex/.system/skills`)、`~/.codex/plugins/cache/**` | Codex CLI(系统/缓存只读) |
| `~/.accio/accounts/*/skills` | Accio Work(账号编号哈希化,绝不输出原始账号) |
| `~/.workbuddy/skills`、`connectors/skills`、`plugins/cache/**` | WorkBuddy(marketplace 商品目录不算已安装) |
| `~/Library/Application Support/Cindy/...` | Cindy(投影只读,同一实体按真实路径去重) |
| `~/.local/share/ego/ego-skills` | Ego 浏览器 |
| `data/workspace-locations.txt` 登记的项目内目录 | 工作区 skill |
| `data/client-locations.json` 登记的目录 | 自定义客户端 |

> 同名 skill 在 ZCode 发现集内会**全部进加载列表**(双份占上下文),但只加载第一个。跨工具共享的 skill 建议放 `~/.agents/skills`。

## 项目结构

```
skill-keeper/
├── SKILL.md                      # skill 定义(触发词、铁律、工作流)
├── scripts/
│   ├── scan.py                   # 多客户端发现 + 完整指纹 → data/inventory.json(只读)
│   ├── report.py                 # v2 价值审查报告(Markdown + HTML)
│   ├── serve.py                  # 两阶段 plan/apply 本地服务(127.0.0.1+token)
│   ├── check_updates.py          # 完整树更新检查(只读,暂存固定候选)
│   ├── value_review.py           # 审查队列 queue / show / record
│   ├── remove_skill.py           # plan / apply 两阶段删除
│   ├── make_sample_report.py     # 固定虚构 fixture → 示例报告
│   └── core/                     # io/模型/指纹/客户端适配器/来源/热度/重复/审查/备份/变更/审计/迁移
├── tests/                        # unittest 全量测试(全部使用临时 HOME fixture)
├── data/                         # 个人配置(.example 模板)+ 运行时产物(已 gitignore)
├── backups/                      # 带 manifest 的备份(自动创建,已 gitignore)
└── examples/report-sample.html   # 固定虚构数据生成的示例报告
```

## 安全设计(铁律)

1. 扫描、报告、更新检查、审查队列**只读**;删除/更新/恢复必须先 plan、用户确认 digest 后再 apply;**系统永不自动删除**
2. 任何变更前强制创建并验证备份;验证失败自动回滚;恢复走两阶段、冲突不覆盖
3. 自建白名单与客户端自带/插件内容受保护;名称前缀、frontmatter 自述不能换取免检
4. 不修改客户端插件缓存;客户端配置只按字段白名单读取,token/key/cookie/env 一律不碰、不输出
5. 变更目标只能是 inventory 里的稳定 instance ID;任意路径、`.`、`..`、逃逸符号链接一律拒绝
6. 所有 JSON 状态带 `schema_version` 并原子写入;互斥锁防两个窗口同时变更;成功、失败、回滚全部写审计
7. GitHub 星数只是热度参考;热度、维护、来源任何单一因素都不能自动触发删除结论

## 隐私说明

`data/` 下的个人配置与运行时产物、`backups/` 备份均已列入 `.gitignore`,不会被提交。示例报告由固定虚构 fixture 生成(`make_sample_report.py`),不读取你的真实盘点。Accio 账号编号等敏感标识在扫描阶段即哈希化,报告与日志不出现原始值。

## 已验证环境

macOS + ZCode / Claude Code / Codex CLI / Ego,Python 3.9。其他客户端目录可在 `data/client-locations.json` 登记,或在 `scripts/core/clients/` 增加适配器。

## License

[MIT](LICENSE)
