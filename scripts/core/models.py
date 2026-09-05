"""skill-keeper v2 稳定数据模型:Location / SkillInstance / ChangePlan。

所有 JSON 状态文件共享 SCHEMA_VERSION;dataclass 一律 frozen,
转换只通过 to_dict / from_dict,保证 schema 字段显式、可校验。
"""
from dataclasses import asdict, dataclass
from typing import Tuple

SCHEMA_VERSION = 2


@dataclass(frozen=True)
class Location:
    """一个可发现 skill 的目录位置(客户端 + 路径 + 类别 + 证据)。"""
    location_id: str
    client: str
    path: str
    kind: str            # user | workspace | builtin | plugin-cache
    mutable: bool        # False 的位置只扫描,不提供删除/更新入口
    evidence: Tuple[str, ...]
    aliases: Tuple[str, ...] = ()   # 复用同一物理位置的其他客户端(如 haha 复用 claude)

    def to_dict(self):
        row = asdict(self)
        row["evidence"] = list(self.evidence)
        row["aliases"] = list(self.aliases)
        return row

    @classmethod
    def from_dict(cls, row):
        data = dict(row)
        data["evidence"] = tuple(data.get("evidence", ()))
        data["aliases"] = tuple(data.get("aliases", ()))
        return cls(**data)


@dataclass(frozen=True)
class SkillInstance:
    """一个物理安装实例:位置 + 目录名 + 规范化真实路径 唯一确定。"""
    instance_id: str
    location_id: str
    directory_name: str
    path: str
    real_path: str
    logical_name: str
    tree_hash: str
    mutable: bool
    client: str
    kind: str
    evidence: Tuple[str, ...]

    def to_dict(self):
        row = asdict(self)
        row["evidence"] = list(self.evidence)
        return row

    @classmethod
    def from_dict(cls, row):
        data = dict(row)
        data["evidence"] = tuple(data.get("evidence", ()))
        return cls(**data)


@dataclass(frozen=True)
class ChangePlan:
    """不可变变更计划:生成后内容不允许改,执行需 plan_id + digest + confirm 三重匹配。"""
    plan_id: str
    action: str          # remove | update | restore
    target_ids: Tuple[str, ...]
    preconditions: Tuple[Tuple[str, str], ...]   # (键, 期望值) 对,执行前逐项复核
    summary: str
    digest: str
    created_at: str
    expires_at: str
    reason: str = ""                 # 用户理由(正式入计划,digest 覆盖)
    recommendation_id: str = ""      # 价值审查推荐记录 ID(可选)

    def to_dict(self):
        row = asdict(self)
        row["target_ids"] = list(self.target_ids)
        row["preconditions"] = [list(x) for x in self.preconditions]
        return row

    @classmethod
    def from_dict(cls, row):
        data = dict(row)
        data["target_ids"] = tuple(data.get("target_ids", ()))
        data["preconditions"] = tuple(tuple(x) for x in data.get("preconditions", ()))
        data.setdefault("reason", "")
        data.setdefault("recommendation_id", "")
        return cls(**data)
