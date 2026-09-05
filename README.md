# skill-keeper · 本地 Agent Skill 管家(v4)

一个以「skill 形态」存在的本地 skill 管理工具:盘点多个 Agent 客户端里的全部 skill——**功能 / 来源 / 配套客户端**一目了然;体检重复加载、链接漂移、损坏 frontmatter 等健康问题;为每个第三方 Skill 生成**有 GitHub 证据、重复与替代分析的价值审查结论**;所有删除/更新/恢复都走**计划 → 确认 → 备份 → 执行 → 验证**的安全闭环,系统永不自动删除。

Windows / macOS / Linux 都可以安装运行;已知客户端有精确适配器,**未知客户端由大模型直接提供根目录即可盘点**(无需改代码、无需上传任何数据)。

📊 **报告长什么样?** 用浏览器打开 [`examples/report-sample.html`](examples/report-sample.html)(由固定虚构数据生成)。

## 它解决什么问题

- skill 装过就忘:来源、版本、用途、值不值得留,无从查起
- 同名 skill 散落在多个客户端目录,哪些真的被加载?(盘点区分**发现 / 推断可加载 / 确认加载**三层:位置在客户端读取集合内只是推断,没有运行时证据时不冒充"确认加载")
- 想删不敢删:没有可验证的备份,不知道删了会不会弄坏别的客户端
- 更新怕被坑:更怕"自动更新"装进来的不是你审过的内容

## 三种使用流程

### 流程一:直接扫描已知客户端

内置 ZCode、Codex、Accio Work、WorkBuddy、Claude Code / Haha、Cindy、Ego、共享库(`~/.agents/skills`)与工作区位置的适配器(含插件缓存,只读):

```bash
python3 scripts/scan.py            # 只读盘点 → data/inventory.json
python3 scripts/report.py          # Markdown + 交互 HTML 报告
python3 scripts/check_updates.py   # 只读:完整树 vs 固定上游候选
```

### 流程二:未知客户端——让模型提供根目录

遇到任何没有适配器的客户端(任何操作系统),大模型就是"运行时位置适配器":找出该客户端实际读取 skill 的目录,交给 skill-keeper,盘点立即完成——不改任何代码,不上传任何数据:

```bash
# 单根直传(可重复)
python3 scripts/scan.py --root my-agent=~/.my-agent/skills

# 或声明文件 / stdin(字段白名单,64KiB/32根上限;load_state 只能是 reported)
python3 scripts/scan.py --locations-json declaration.json --json
python3 scripts/scan.py --locations-json - --json
```

声明 JSON 长这样(**其余字段一律被拒**——包括 mutable、instance_id、tree_hash、命令、网址、秘密字段):

```json
{"schema_version": 1, "client": "my-agent", "observed_by": "model",
 "complete": false,
 "roots": [{"path": "~/.my-agent/skills", "scope": "user", "load_state": "reported"}]}
```

- 不知道就说不知道:`complete` 保持 `false`;禁止猜路径、禁止改客户端配置;
- 临时声明**只读、仅本次扫描、不写入长期配置**:扫描生成的本地 inventory 会记录派生实例与路径，产生的实例永远不可变,没有任何删除/更新入口;
- 与已知位置重复时物理目录只扫描一次；系统仍保留“该客户端自报读取此目录”的关系，并在加载总览和共享库视图标注「自报」。
- 临时根必须严格位于当前用户 HOME 内；外置盘或其他受信目录请由用户登记进本地 `client-locations.json`。

### 流程三:把确认的位置写进本地配置长期管理

用户确认某个根要长期盘点后,登记进 `data/client-locations.json`(模板见 `data/client-locations.example.json`):

```json
{"locations": [{"location_id": "my-agent-skills", "client": "my-agent",
                "path": "~/.my-agent/skills", "kind": "user", "mutable": false}]}
```

- `mutable: false` = 永远只扫描;
- 只有用户自己写下 `mutable: true` 的位置,才可能进入 计划 → 确认 digest → 备份 → 事务 的变更闭环;
- 变更前,apply 会在**每个目标同目录**实际验证创建 / 同卷移动 / fsync 并清理,失败在改动任何目标前拒绝。

## 安全变更(需用户确认)

```bash
python3 scripts/manage.py plan remove --instance-id <instance_id> --reason <理由> --json
python3 scripts/manage.py apply <plan_id> --digest <digest> --confirm --json
python3 scripts/manage.py status/recover <plan_id> --json
python3 scripts/verify.py   # 全量验收(0 失败 0 跳过)
```

计划不可变、30 分钟过期;执行 = 互斥锁 → 目标指纹复核 → 真实目标预检 → 创建并验证备份(带 manifest)→ 精确移动 → 验证(失败自动回滚)→ 审计。恢复走两阶段计划,冲突不覆盖。自建白名单、应用内置(`builtin-app`,支持 `owner` 语义)、客户端自带/插件内容受保护。

## 安装

```bash
git clone https://github.com/xxdd3808-lgtm/skill-keeper.git ~/skill-keeper
pip install ~/skill-keeper            # 或在目录内 pip install .;装完有统一命令 skill-keeper

# 仓库直接跑(不用 pip)也可以:
python3 ~/skill-keeper/scripts/scan.py
skill-keeper doctor --json            # 版本 / Python / 运行目录 / 锁后端 / 已登记位置
```

- 运行态目录:新安装统一 `~/.skill-keeper/{data,cache,backups}`;可识别的旧仓库运行态(仓库 `data/` 里有 v2/v3 数据)继续用仓库 `data/` + `backups/`,**不自动迁移**;环境变量 `SKILL_KEEPER_DATA` / `SKILL_KEEPER_STAGING` 始终可显式覆盖。
- 依赖:运行时仅 Python 3.8+ 标准库;可选 PyYAML(只影响 frontmatter 校验展示)、gh CLI(GitHub 证据与候选拉取,缺席时降级)。构建依赖只在 `pip install` 时使用。
- 让各客户端发现这个 skill:做个符号链接(按你的客户端 skill 目录调整),例如 `ln -s ~/skill-keeper ~/.agents/skills/skill-keeper`。
- 个人配置模板:`data/*.example.*` → 复制为同名真实文件(已 gitignore)。

**所有数据都留在本机**:默认不联网、不上传、无遥测;盘点、报告、台账、备份、审计全是本地文件。唯一可选的网络功能是查询 GitHub 仓库证据(失败时降级并标注)。详见 [SECURITY.md](SECURITY.md)。

## 自动化接口

`scan.py --json`、`report.py --json`、`check_updates.py --json`、`value_review.py queue --json`,退出码 **0=健康/无差异,1=有红色问题/有差异,2=运行失败或观察不完整**,可挂 cron / launchd 定期巡检。

## 项目结构

```
skill-keeper/
├── SKILL.md                      # skill 定义(触发词、铁律、工作流、未知客户端通用流程)
├── scripts/
│   ├── cli.py                    # 统一 CLI(scan/report/manage/doctor)
│   ├── scan.py                   # 多客户端发现 + 完整指纹 + 模型位置声明 → inventory(只读)
│   ├── report.py / serve.py      # 报告与两阶段交互服务
│   ├── check_updates.py          # 完整树更新检查(只读,暂存固定候选)
│   ├── value_review.py           # 审查队列 queue / show / record
│   ├── manage.py / remove_skill.py
│   ├── verify.py                 # 验收入口(unittest + 反作弊检查)
│   └── core/                     # io/模型/指纹/平台/位置声明/预检/客户端适配器/来源/备份/变更/事务/审计
├── tests/                        # unittest 全量测试(临时 HOME + 完全虚构 fixture)
├── .github/workflows/ci.yml      # Ubuntu 3.8 / Ubuntu / macOS / Windows 四个 CI job
├── data/                         # 个人配置(.example 模板)+ 运行时产物(已 gitignore)
└── backups/                      # 带 manifest 的备份(自动创建,已 gitignore)
```

## 安全设计(铁律)

1. 扫描、报告、更新检查、审查队列**只读**;删除/更新/恢复必须先 plan、用户确认 digest 后再 apply;**系统永不自动删除**
2. 任何变更前强制创建并验证备份;验证失败自动回滚;恢复走两阶段、冲突不覆盖;apply 前在真实目标同目录做预检
3. 自建白名单与客户端自带/插件内容受保护;名称前缀、frontmatter 自述、模型位置声明都不能换取免检
4. 不修改客户端插件缓存;客户端配置只按字段白名单读取,token/key/cookie/env 一律不碰、不输出
5. 变更目标只能是 inventory 里本机扫描、mutable 且策略允许的稳定 instance ID;任意路径、`.`、`..`、逃逸符号链接一律拒绝
6. 所有 JSON 状态带 `schema_version` 并原子写入;互斥锁防两个窗口同时变更(POSIX flock / Windows msvcrt);成功、失败、回滚全部写审计
7. GitHub 星数只是**仓库热度**参考;热度、维护、来源任何单一因素都不能自动触发删除结论

## 隐私说明

`data/` 下的个人配置与运行时产物、`backups/` 备份均已列入 `.gitignore`,不会被提交。示例报告由固定虚构 fixture 生成(`make_sample_report.py`),不读取真实盘点。Accio 账号编号等敏感标识在扫描阶段即哈希化。inventory 为支持本地复核与安全操作会记录本机绝对路径和内容指纹；报告把 HOME 缩写为 `~`。原始位置声明不写入长期配置，全部文件都只留本机。

## 已验证环境

- macOS(Python 3.9)+ ZCode / Codex / WorkBuddy / Ego / 共享库(私人部署长期使用)
- CI 工作流：Ubuntu(Python 3.8 与主力版)、macOS、Windows 四个 job 验证安装、扫描、报告、锁、路径、预检与事务；是否通过以 push 后 GitHub Actions 的实际结果为准
- 其他客户端:模型位置声明即可盘点,或登记 `data/client-locations.json`,或在 `scripts/core/clients/` 增加适配器

## License

[MIT](LICENSE)
