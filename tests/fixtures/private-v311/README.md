# private-v311 冻结 fixture(完全虚构)

Task 0(v4 精简开源泛化)建立的零退化合同:覆盖 shared / Codex / WorkBuddy / Ego
四类位置、自建白名单、builtin-app owner 语义、符号链接别名与漂移、同名重复加载、
来源审查、计划/备份/恢复闭环和旧 CLI 入口。

**本目录全部内容均为虚构**:技能名、仓库、版本、描述都是为测试编造的,
不含任何真实 skill 清单、个人路径、账号或秘密。`home/` 是虚构 HOME 树
(测试复制到临时目录,`wb-link`/`wb-drift` 为相对符号链接),
`data/` 是虚构个人配置(known-sources.json + self-built.txt)。

预期扫描结果(冻结口径,见 tests/test_private_compatibility.py):

- 位置 4 个:shared、codex-user、workbuddy-user、ego-user(全部 mutable)
- 实例 9 个、逻辑 Skill 7 个(shared-alpha 同指纹 3 实例合一;
  builtin-widget 共享库副本与 ego 正本内容不同,是两个逻辑身份)
- findings 恰好 3 条黄灯:duplicate-load(codex)、builtin-app-spread(shared)、
  link-drift(wb-drift);无红灯;观察完整
- 审查队列 4 项:shared-alpha、shared-beta(漂移副本,目录名不能继承白名单)、
  codex-extra、wb-only;shared-beta 正本与 builtin-widget 两份受保护不入队
