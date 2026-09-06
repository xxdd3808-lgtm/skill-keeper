# Skill-Keeper 全项目 Review(第二轮)

审查日期:2026-09-06 晚。基线:`f4929ea`(v4.1.0 发布 + 2 个发布后提交:022f3df SKILL.md 交付约定、f4929ea 测试输出防污染)。

## 结论

**发布级健康:未发现 P0/P1 问题。** 早前一轮审查(docs/reviews/2026-09-06/,F01–F13)的全部修复在代码中确认到位、无回归;最新两个提交本身干净,未削弱任何断言。本轮共 3 个 P2、4 个 P3,均为小修或记录性事项,已随本目录归档同批修复。

## 验证范围与证据

- 全文通读安全关键路径约 2600 行:`scripts/serve.py`、`scripts/core/changes.py`、`service.py`、`policy.py`、`io.py`、`transactions.py`、`staging.py`、`manage.py`;`backup.py` 抽查关键函数(create/verify/manifest 校验)。
- 定向核查盘点/审查正确性:`scan.py`、`core/location_input.py`、`fingerprint.py`、`clients/*`(accio/common)、`value_review.py` 退出码与损坏台账 fail-closed。
- 文档一致性:SKILL.md 命令表 ↔ `cli.py` 路由;`启动技能报告.command` 与 5 个 `data/*.example.*` 存在性;版本三处同源(4.1.0:code/SKILL.md/pyproject dynamic);PROGRESS/BLOCKED 时效。
- 本机 `python3 scripts/verify.py --json`:373 项,0 失败/0 错误/0 跳过;233 个冻结 ID 完整;5 项反作弊检查全过;stdout 为纯 JSON。
- CI(f4929ea):Ubuntu 3.8 / Ubuntu / macOS / Windows 四 job 均为 `pip install .` + `python -m scripts.verify`,全绿。
- 本轮执行环境:macOS 26.6.2 / arm64 / Python 3.9.6。子代理并发受限,全部审查由主会话亲自完成;未重跑 Windows/Linux 平台测试(以 CI 全绿记录为准)。

## 主要发现

优先级:P2 = 应修的不一致或小缺口;P3 = 记录性/观察项。不涉及 P0/P1。

- **[P2] CSP 声明与实现有出入** — `serve.py` 模块 docstring 与 `docs/skill-manual.md` 称 CSP"不放宽 unsafe-inline/unsafe-eval",实际 `_csp_for()`(serve.py:364)的 `style-src` 允许 `unsafe-inline`(仅内联样式)。修复:文档改为精确措辞("脚本不使用;样式保留 style-src unsafe-inline"),代码行为不变。
- **[P2] `/api/ignore` 是唯一未持进程内锁的写入口** — serve.py 原 305-306 行,`ignore.json` 是读改写且随后触发重扫,与 `/api/plan`、`/api/vet`、`/api/rescan` 的加锁口径不一致,并发可丢更新。修复:与其他写端点一致套 `ctx.process_lock`。
- **[P2] 残留死代码** — serve.py 原 186 行占位变量 `origin_ok = "http://127.0.0.1:%d" % 0` 从未使用,删除。
- **[P3] token 走 URL query** — 会留本机浏览器历史;已有仅绑 127.0.0.1 + no-referrer 缓解,手册补充"接受的取舍"说明。
- **[P3] `skill-keeper` 命令装后不在本机 PATH** — 打包入口声明正确(pyproject `[project.scripts]`),属用户环境(user base bin 不在 PATH);可用 pipx 或补 PATH。
- **[P3] AGENTS.md「当前状态」段未反映发布后 2 个提交** — 陈述均为发布时点事实,不算错误;随洁癖收尾同步。
- **[P3] 事务保管目录 `.sk-txn-*` 短暂出现在 Skill 父目录** — 点前缀,客户端发现机制跳过;同目录 rename 的原子性所需,属设计取舍,无需处理。

## 核对通过的关键不变式(抽查证据)

- 变更闭环:计划落盘只读+digest 校验(changes.py:71-78,406-407);执行需布尔 confirm + digest 常量时间比较(769-773);执行期重验策略/路径/符号链接/位置根并重算磁盘指纹防 TOCTOU(411-457);备份先建先验(873-875);事务分段+保管 rename+回滚按计划前置哈希核对(F12,525-595);提交点语义(490);跨计划未完成事务/损坏 fail-closed(F05,666-702);恢复持锁(705-740);审计 JSONL append+fsync(audit.py)。
- 安检绑定:plan_id+candidate_hash+证据非空,占位文本拒绝(changes.py:236-273;policy.py:140-163);staging 安检后被改拒用(864-868)。
- 保护策略:损坏拒一切写(含内部条目损坏,F08,policy.py:55-63);模型自报位置即使 mutable 被翻也拒绝(95-100);builtin-app owner 散布收回口子窄化正确(127-131)。
- 服务面:127.0.0.1 绑定(381)、token_urlsafe(24)+compare_digest 字节比较(379,210)、Origin 校验、64 KiB 上限、错误不带内部信息、拒绝也写审计;报告 HTML 全部第三方文本经 `esc()` 转义(抽查 6 处拼接点),JS 为静态资源,无 XSS 偷 token 路径;计划 ID 正则不含斜杠,无目录遍历面。
- 盘点/审查:敏感字段白名单+递归脱敏(io.py:17-47);accio 清单仅取白名单五字段(accio.py:12-29);未知客户端根严格在 HOME 内(scan.py:700-701);location_input 白名单/限额/不回显;指纹确定性、符号链接只记 target、排除清单参与版本(fingerprint.py);退出码 0/1/2 各入口一致。
- 测试:f4929ea 的 4 处 redirect_stdout 接法正确,断言无弱化;安检占位拒绝与 `/api/vet` 空证据拒绝均有测试覆盖(test_change_update.py:179+,test_report_frontend.py:131)。
