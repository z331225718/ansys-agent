from __future__ import annotations

from dataclasses import dataclass


class TargetValidationError(ValueError):
    pass


@dataclass(frozen=True)
class AedtTarget:
    kind: str
    value: int

    def __post_init__(self) -> None:
        if self.kind not in {"pid", "port"}:
            raise TargetValidationError("target kind must be pid or port")
        if type(self.value) is not int or self.value <= 0:
            raise TargetValidationError("target value must be a positive integer")
        if self.kind == "port" and self.value > 65535:
            raise TargetValidationError("port must not exceed 65535")

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.value}"

    def to_dict(self) -> dict[str, int | str]:
        return {"kind": self.kind, "value": self.value}

    @classmethod
    def from_values(cls, *, pid: int | None = None, port: int | None = None) -> "AedtTarget":
        supplied = [("pid", pid), ("port", port)]
        selected = [(kind, value) for kind, value in supplied if value is not None]
        if len(selected) != 1:
            raise TargetValidationError("exactly one of pid or port is required")
        return cls(selected[0][0], selected[0][1])

    @classmethod
    def from_dict(cls, value: object) -> "AedtTarget":
        if not isinstance(value, dict) or set(value) != {"kind", "value"}:
            raise TargetValidationError("target must contain exactly kind and value")
        return cls(str(value["kind"]), value["value"])
