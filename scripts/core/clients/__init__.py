"""客户端发现适配器包:统一从 scripts.core.clients 导入。"""
from .base import ADAPTERS, ClientAdapter, discover_locations, discover_skill_roots
from .common import client_load_aliases
from ..models import Location

__all__ = [
    "ADAPTERS", "ClientAdapter", "Location",
    "discover_locations", "discover_skill_roots", "client_load_aliases",
]
