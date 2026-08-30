---
name: skill-keeper
description: 本地 agent skill 管家。对全部本地 skill 做全量盘点——每个 skill 的功能、来源(GitHub/skills.sh/国内注册表/随应用/自建)、配套客户端(ZCode/Claude Code/Codex/Ego/插件),检测重复加载、遮蔽副本、悬空链接、损坏 frontmatter 等健康问题,并在用户确认后执行带备份的更新/删除/修复。当用户说"梳理skill""skill体检""skill审计""skill报告""skill管家""哪些skill会加载""这个skill哪来的""skill删掉/更新"时使用。
version: 1.0.0
---

# skill-keeper · 本地 Skill 管家

管理 `~/.agents`、`~/.zcode`、`~/.claude`、`~/.codex`、`~/.local/share/ego` 及 ZCode 插件缓存里的全部 skill。

> 项目文件夹(实体)在 `~/skill-keeper/`;`~/.agents/skills/skill-keeper` 是指向它的符号链接,只为了让各客户端发现本 skill。下面的命令一律用 `~/skill-keeper` 路径,脚本内部会自行定位,不依赖调用路径。

## 铁律

1. **扫描和报告是只读的**,直接执行;**删除/更新/修复必须先给用户看清单,确认后再动手**。
2. **任何删除/更新前强制备份**:tar 整个 skill 目录到项目文件夹的 `backups/`(即 `~/skill-keeper/backups/removed-<目录名>-<YYYYMMDD-HHmmss>.tar.gz`,remove_skill.py 自动做)。
3. **自建 skill 受保护**:删除自建白名单(`data/self-built.txt`)里的 skill 时,先向用户特别确认。
4. 不修改插件缓存里的 skill(它们由插件系统管理)。
5. 一切操作后重跑 `scan.py` 刷新 `data/inventory.json`,保证盘点与实际一致。

## 数据文件

| 文件 | 用途 |
|---|---|
| `data/inventory.json` | 最新盘点结果(每 skill 一条记录,含分组) |
| `data/inventory-last.json` | 上一次盘点(scan.py 自动轮转),供 diff |
| `data/known-sources.json` | 已核实的上游来源映射(dir → repo + 路径),发现新来源时补充进来 |
| `data/self-built.txt` | 自建 skill 白名单(受保护清单),一行一个目录名 |
| `data/groups.json` | 分组配置(组名 → 目录名列表);用户想调整分组就改它,改完重扫 |

## 自动化接口(定时巡检/被其他工具消费)

三个脚本都支持 `--json`:输出机器可读摘要;退出码 **0=健康/无差异,1=有红色问题/有差异**。例:

```bash
python3 ~/skill-keeper/scripts/scan.py --json
python3 ~/skill-keeper/scripts/report.py --json
python3 ~/skill-keeper/scripts/check_updates.py --json
```

可挂 cron/launchd 定期巡检,红色问题自动提醒用户。

## 工作流

### 1. 盘点扫描(只读)

```bash
python3 ~/skill-keeper/scripts/scan.py
```

输出概要(总数、来源分布、健康问题、重复加载),详情写入 `data/inventory.json`。健康检查包含:
- frontmatter 完整性(YAML 可解析、name/description 必填)、瘦身壳残留
- 悬空/循环符号链接
- **链接漂移**:`~/.claude/skills` 等快捷方式指向的内容与主库(`~/.agents/skills`)是否一致
- **依赖命令**:skill 声明的外部程序(如 metadata 里的 requires.bins)在系统中是否存在,缺失则 🟡 提示
- 同名多份、ZCode 双份加载

### 2. 生成报告

```bash
python3 ~/skill-keeper/scripts/report.py
```

同时生成两个文件并打印 Markdown:
- `data/report.md` 纯文本版
- **`data/report.html` 交互式网页版**(按分组折叠、红黄绿标色,可直接让用户用浏览器打开)——给用户看报告时优先给这个

内容:总表(Skill | 分组 | 功能 | 来源 | 配套客户端 | 触发 | 健康)、各客户端加载开销、ZCode 重复加载、插件旧缓存、非 skill 杂质、体检问题、与上次盘点 diff。

### 3. 更新检查(只读)

```bash
python3 ~/skill-keeper/scripts/check_updates.py
```

对有 GitHub 来源的 skill 拉上游 SKILL.md 与本地比对;skills.sh 来源经 download API 比对。锁内 skill 也可用 `npx -y skills check`(注意:该命令发现更新会**直接更新**,只做检查时用本脚本)。输出「可更新」清单,报给用户确认。

### 4. 执行动作(需用户确认)

- **更新**:优先 `npx -y skills add <owner/repo>@<slug> -g -y`(会记入锁文件);GitHub 手动来源用 gh api 拉取覆盖。更新前备份。
- **删除**:
  ```bash
  python3 ~/skill-keeper/scripts/remove_skill.py <目录名> [更多目录名...]
  ```
  自动:备份 → 从所有位置(~/.agents、~/.zcode、~/.claude、~/.codex、ego)删除 → 清理 `~/.agents/.skill-lock.json` 条目。
- **修复**(YAML、符号链接):按报告里的具体指引手工修,修完重扫。

### 5. 汇报

给用户:操作结果 + 剩余总数 + 新发现的问题;**给用户看详情时优先打开 `data/report.html`**(网页版,按分组折叠)。

## 分组维护

分组定义在 `data/groups.json`(组名 → 目录名列表)。用户说"把 X 挪到 Y 组/建一个新组"时,编辑该文件后重跑 scan.py + report.py 即可。未匹配的 skill 自动归入"未分组",插件 skill 自动归入"ZCode 插件"。

## 来源分类口径

- `github`:手动从 GitHub 安装(khazix-skills、anthropics/skills、clawic/skills、obra/superpowers 等)
- `skills.sh`:经 skills.sh 市场安装(有 `_meta.json` 回执;锁文件 `~/.agents/.skill-lock.json` 里有来源仓库的可自动查更新)
- `registry-*`:国内注册表(火山 skills.volces.com、魔搭 modelscope.cn、鸿蒙 matrix.openharmony.cn、SkillHub)
- `builtin-app`:随应用自带(智谱 autoglm 六件套、ego-browser),不建议手动动
- `self-built`:用户自建(白名单内),**受保护**
- `plugin`:ZCode 插件自带,由插件系统管理
- `unknown`:来源不明,报告里标注待补

## 客户端加载规则(已按官方文档核实)

- ZCode 发现顺序:`~/.zcode/skills` → `~/.agents/skills` → 工作区 `.zcode/skills`/`.agents/skills` → 插件。**同名不同路径都会进加载列表**(双份占上下文),但只加载第一个,后面的是遮蔽副本。跨工具共享的 skill 应放 `~/.agents/skills`,ZCode 专属覆盖才放 `~/.zcode/skills`。
- Claude Code 读 `~/.claude/skills`(现为指向 .agents 的符号链接);Codex CLI 读 `~/.codex/skills`;Ego 读 `~/.local/share/ego/ego-skills`。
- 每个 skill 常驻上下文的是 name+description;SKILL.md 全文在触发时才加载。
