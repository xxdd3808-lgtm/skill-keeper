# skill-keeper · 本地 Agent Skill 管家

一个以「skill 形态」存在的本地 skill 管理工具:一条命令盘点本机所有 agent skill 的**功能 / 来源 / 配套客户端**,体检重复加载、链接漂移、损坏 frontmatter 等健康问题,并生成 Markdown + 交互式 HTML 双格式报告。

适合装了多个 AI 客户端、skill 散落各处、说不清「装了什么、哪来的、谁在用」的个人用户。

📊 **报告长什么样?** 用浏览器打开 [`examples/report-sample.html`](examples/report-sample.html)(脱敏示例,含按分组折叠、红黄绿健康标色、各客户端加载开销卡片)。

## 它解决什么问题

- skill 装过就忘:来源、版本、用途无从查起
- 同名 skill 散落在多个客户端目录,哪些真的被加载?哪些是白占上下文的遮蔽副本?
- 各客户端目录 + 插件自带 skill,没有统一视图
- 想删不敢删:没有备份,不知道删了会不会弄坏别的客户端

## 功能

### 盘点扫描 `scan.py`(只读)

- 扫描 5 个本机位置 + ZCode 插件缓存(带版本感知,识别旧版本缓存)
- 来源自动推断,优先级:自建白名单 → 已知来源映射 → skills.sh 安装回执(`_meta.json`)→ skills 锁文件 → homepage → 标记「来源不明」
- 每个 skill 输出:功能(描述首句提炼)/ 来源 / 配套客户端 / 触发方式 / 常驻上下文体积 / 健康问题 / 依赖的外部命令
- 结果写 `data/inventory.json`,上一次自动轮转为 `inventory-last.json` 供 diff

### 健康体检

- 🔴 红色:frontmatter 解析失败或缺 name/description、悬空/循环符号链接、瘦身壳残留、非 skill 杂质
- 🟡 黄色:ZCode 同名多份(全部进加载列表,双份占上下文)、**链接漂移**(符号链接内容与主库不一致)、插件旧版本缓存、依赖命令缺失、各副本来源不一致

### 报告 `report.py`

- Markdown + 交互式 HTML(按分组折叠、红黄绿标色、客户端加载开销统计)
- **处理建议**:每条体检问题与上游差异都翻译成 🟢建议更新 / 🟡待你确认 / ℹ️提示,并附操作按钮
- **一键处理** `report.py --serve`(macOS 可直接双击 `启动技能报告.command`):本地起服务(仅 127.0.0.1 + 随机 token,防其他网页跨站调用),网页里直接 🔄更新 / 🔍看上游差异(页内红绿 diff) / 🗑️删除 / ✕忽略 / ♻️从备份恢复;所有动作先 tar 备份、成功后自动重扫重报,审计记入 `data/actions.log`。静态打开 report.html 时,按钮退化为复制等价命令
- 与上次盘点自动 diff(新增 / 移除 / 来源变更)
- 分组由 `data/groups.json` 配置,改完重跑即可;不想看的黄灯可写进 `data/ignore.json` 忽略

### 更新检查 `check_updates.py`(只读)

- GitHub 来源经 `gh api` 拉上游 SKILL.md 逐字比对;skills.sh 来源走 download API
- 结果缓存到 `data/updates.json`,并**自动给出结论**:🟢建议更新 / 🛡️建议保留 / 🟡需人工研判 + 一句人话理由(依据:版本号、改动是否只碰说明信息、上游最后改动时间 vs 本地改动时间、改动规模)——不用自己读 diff
- 只比对不更新——`npx skills check` 发现更新会**直接更新**,想「只看不动」就用本脚本;确认后在交互报告里一键更新

### 安全删除 `remove_skill.py`

- 备份(tar 整目录)→ 从所有位置删除 → 清理 skills 锁文件条目,一步完成
- 自建白名单(`data/self-built.txt`)里的 skill 受保护,删除需显式 `--force`

### 自动化接口

三个只读脚本都支持 `--json`(输出机器可读摘要),退出码 **0=健康/无差异,1=有红色问题/有差异**,可直接挂 cron / launchd 定期巡检:

```bash
python3 ~/skill-keeper/scripts/scan.py --json
```

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

# 可选:工作区级 skill 扫描、忽略规则(不需要可跳过)
cp workspace-locations.example.txt workspace-locations.txt
# ignore.json 格式见 SKILL.md「数据文件」表;没有它就不忽略任何问题

# 首次盘点
python3 ~/skill-keeper/scripts/scan.py
python3 ~/skill-keeper/scripts/report.py && open ~/skill-keeper/data/report.html
```

依赖:Python 3.8+;可选 PyYAML(frontmatter 解析更稳)、gh CLI(GitHub 来源的更新检查)。

## 扫描位置与客户端

| 位置 | 归属客户端 |
|---|---|
| `~/.zcode/skills` | ZCode(优先级最高) |
| `~/.agents/skills` | 共享库(ZCode / Claude Code / Cursor 等) |
| `~/.claude/skills` | Claude Code |
| `~/.codex/skills` | Codex CLI |
| `~/.local/share/ego/ego-skills` | Ego 浏览器 |
| `~/.zcode/cli/plugins/cache/**` | ZCode 插件自带(由插件系统管理,本工具不修改) |

> 同名 skill 在不同路径会**全部进加载列表**(双份占上下文),但只加载发现顺序的第一个。跨工具共享的 skill 建议放 `~/.agents/skills`,客户端专属覆盖才放各自目录。

## 项目结构

```
skill-keeper/
├── SKILL.md                      # skill 定义(触发词、铁律、工作流)
├── scripts/
│   ├── scan.py                   # 全位置扫描 → data/inventory.json(只读)
│   ├── report.py                 # Markdown + 交互式 HTML 报告(含处理建议与操作按钮)
│   ├── serve.py                  # 本地交互服务:一键 更新/删除/忽略/恢复(127.0.0.1+token)
│   ├── check_updates.py          # 上游更新检查(只读)→ data/updates.json
│   ├── remove_skill.py           # 带备份的安全删除
│   └── make_sample_report.py     # 把个人盘点脱敏成可分享的示例报告
├── data/
│   ├── groups.example.json       # 分组配置模板(个人版已 gitignore)
│   ├── self-built.example.txt    # 自建白名单模板
│   ├── known-sources.example.json# 来源映射模板
│   ├── ignore.json               # 忽略规则(个人配置,可选,已 gitignore)
│   ├── inventory.json            # 盘点结果(运行时生成)
│   ├── updates.json              # 更新检查缓存(运行时生成)
│   ├── actions.log               # 一键操作审计(运行时生成)
│   └── report.md / report.html   # 报告(运行时生成)
├── backups/                      # 删除/更新前的 tar 备份(自动创建)
└── examples/report-sample.html   # 脱敏示例报告
```

## 安全设计(铁律)

1. 扫描与报告**只读**,直接执行;删除/更新/修复必须先给用户看清单、确认后再动手
2. 任何删除前强制 tar 备份到 `backups/`
3. 自建白名单里的 skill 受保护,删除需 `--force`(仍会先备份)
4. 不修改插件缓存(由插件系统管理)
5. 一切操作后重跑 `scan.py`,保证盘点与实际一致
6. 交互服务的按钮点击等同用户确认:服务只监听 127.0.0.1 并校验随机 token,动作仍需页面确认弹窗 + `confirm` 字段,全程留痕 `data/actions.log`

## 隐私说明

`data/` 下的个人配置与盘点结果、`backups/` 备份均已列入 `.gitignore`,不会被提交。想把你的盘点报告分享给别人时,用 `make_sample_report.py` 生成脱敏版。

## 已验证环境

macOS + ZCode / Claude Code / Codex CLI / Ego 浏览器,Python 3.9。目录约定不同的客户端可自行增删 `scan.py` 顶部的 `LOCATIONS`。

## License

[MIT](LICENSE)
