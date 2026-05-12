from __future__ import annotations

from typing import Protocol

from aedt_agent.knowledge.models import ApiSemantic, CommonTrap, WorkflowCase


class KnowledgeProvider(Protocol):
    def search_api(self, query: str, limit: int = 10) -> list[ApiSemantic]: ...

    def list_workflow_cases(self) -> list[WorkflowCase]: ...

    def list_common_traps(self, filter_ids: list[str] | None = None) -> list[CommonTrap]: ...
