from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol


class GeneratorProtocol(Protocol):
    def generate(self, context: str, filename: str | None = None) -> str: ...


@dataclass(frozen=True)
class RepairRound:
    round_num: int
    code: str
    passed: bool
    error: str


@dataclass
class RepairRecord:
    task_id: str
    group: str
    rounds: list[RepairRound] = field(default_factory=list)

    def add_round(self, round_num: int, code: str, passed: bool, error: str) -> None:
        self.rounds.append(RepairRound(round_num=round_num, code=code, passed=passed, error=error))

    @property
    def total_rounds(self) -> int:
        return len(self.rounds)

    @property
    def success(self) -> bool:
        return bool(self.rounds) and self.rounds[-1].passed

    @property
    def repair_count(self) -> int:
        return max(0, self.total_rounds - 1)


def run_with_repair(
    task_id: str,
    group: str,
    context: str,
    generator: GeneratorProtocol,
    evaluator: Callable[[str], tuple[bool, str]],
) -> RepairRecord:
    record = RepairRecord(task_id=task_id, group=group)
    current_context = context
    for round_num in (1, 2):
        code = generator.generate(current_context)
        passed, error = evaluator(code)
        record.add_round(round_num, code, passed, error)
        if passed:
            break
        current_context = f"{context}\n\nPrevious error:\n{error}"
    return record
