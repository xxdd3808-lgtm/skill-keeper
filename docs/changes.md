# skill-keeper 历史运维记事(事实记录,不代表当前能力)

现行能力以 [architecture.md](architecture.md) 为准;原始设计见
`docs/superpowers/specs/2026-08-31-skill-keeper-v2-design.md`。

## 版本史(要点)

- **v1.0–v1.2(2026-08-30)**:扫描/报告/更新检查/备份删除成形;网页操作引入;
  安检台账与 SKILL.md 指纹(后被完整树指纹取代)。
- **v2.0.0(2026-08-31)**:多客户端适配器发现、位置/实例/逻辑身份三层、完整树指纹、
  来源分级、GitHub 缓存、替代候选、价值台账、manifest 备份、不可变计划、两阶段 API、
  v1 迁移。9-01 发布修补收紧替代品口径、builtin-app 保护、热度缓存自愈。
- **v2.1.x(2026-09-02)**:客户端加载拓扑模型(重复按真实读取位置判定);
  候选暂存迁出技能树(ZCode 面板泄漏修复);Haha 读共享库假设撤销(26 幻影双载);
  外部 Agent 删除复核——known_sources 省略绕过 builtin-app 保护的口子堵上;
  报告三组导航与定位。
- **2026-09-02 运维记事**:AutoClaw 残留清理(18 实例,plan/apply+备份+审计);
  `~/.openclaw-autoclaw` 残留整体清除(用户确认,归档在册);Claude Code 卸载后
  只留 Haha,claude 插件缓存随之消失。
- **2026-09-05 可信性优化(F01–F11)**:备份/恢复合同与路径边界、统一执行策略、
  事务与中断恢复、观察完整性、审查有效性、候选/缓存生命周期、CLI/API/报告闭环、
  队列索引复用(读取 12800→80、评分 259120→3160)、外部运行态、验收入口。
  详见 PROGRESS.md 与提交历史(63fc239 起)。
- **v3.1.0(2026-09-05)**:报告新增共享库视图——顶部「📂 共享库」指标直达专属区块,
  列出放在 `~/.agents/skills` 的全部逻辑 Skill 及其价值结论、其他占用客户端与
  plan 入口(HTML/Markdown 双通道);安装实例明细的客户端列改为友好标签
  (shared→共享库)。用户反馈驱动:该事实此前只藏在明细表 client 列里。
- **2026-09-05 运维记事**:ego-browser 共享库快捷方式(`~/.agents/skills/ego-browser`,
  2026-09-03 建)经用户确认摘除——ZCode/Codex 自带浏览器控制不需要加载它;
  实体保留于 `~/.local/share/ego/ego-skills`,Haha/WorkBuddy/Accio 的直连快捷方式保留。
  防线按设计拒绝 builtin-app 删除计划(实体与快捷方式不区分),按手册走符号链接
  手工修;恢复命令 `ln -s /Users/bt/.local/share/ego/ego-skills ~/.agents/skills/ego-browser`。
  摘除后 ZCode 45→44、Codex 69→68,builtin-app-spread 黄灯消除。引擎空档
  (builtin-app 散布快捷方式无法走正规收回流程)记入 AGENTS.md 候选改进。

## 历史教训(规则来源)

1. **安全规则必须在执行边界生效,不能依赖调用者传参**——builtin-app 绕过事件。
2. **加载知识有版本条件**——Codex 导入共享库、Haha 镜像假设、Claude 卸载,
   每次都推翻过一期规则;现在规则带来源/核实日期/适用范围(RULE_VERSION)。
3. **缓存/暂存绝不能放进客户端递归扫描的技能目录**——ZCode 面板候选泄漏事件。
4. **校验过的备份 ≠ 可恢复的备份**——内部链接/目录权限往返失败、归档可被替换,
   2026-09-05 全部转为合同测试。
