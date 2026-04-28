"""Agents 层：每个维度一个 agent 模块。

E1_staffing 是首个 vertical slice 实现。
"""

from .base import AgentResult, BaseAgent, Finding, parse_json_response
from .c1_structure import C1StructureAgent
from .c4_reference import C4ReferenceAgent
from .e1_staffing import E1StaffingAgent

__all__ = [
    "AgentResult",
    "BaseAgent",
    "Finding",
    "parse_json_response",
    "C1StructureAgent",
    "C4ReferenceAgent",
    "E1StaffingAgent",
]
