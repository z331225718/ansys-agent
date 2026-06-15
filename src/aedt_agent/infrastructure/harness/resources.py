from __future__ import annotations

import threading
import time
from dataclasses import dataclass


RESOURCE_ORDER = {"license": 0, "aedt": 1, "cpu": 2}


class ResourceAcquireTimeout(TimeoutError):
    def __init__(self, resource_class: str, timeout_seconds: float):
        super().__init__(
            f"timed out acquiring {resource_class} resource after {timeout_seconds} seconds"
        )
        self.resource_class = resource_class
        self.timeout_seconds = timeout_seconds


@dataclass
class ResourceLease:
    resource_class: str
    waited_seconds: float
    _semaphore: threading.BoundedSemaphore
    _released: bool = False

    def __enter__(self) -> "ResourceLease":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.release()

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._semaphore.release()


@dataclass
class CompositeResourceLease:
    leases: tuple[ResourceLease, ...]

    @property
    def resource_classes(self) -> tuple[str, ...]:
        return tuple(lease.resource_class for lease in self.leases)

    @property
    def waited_seconds(self) -> dict[str, float]:
        return {
            lease.resource_class: lease.waited_seconds
            for lease in self.leases
        }

    def __enter__(self) -> "CompositeResourceLease":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.release()

    def release(self) -> None:
        for lease in reversed(self.leases):
            lease.release()


class ResourceGate:
    def __init__(
        self,
        *,
        max_concurrent_cpu: int = 4,
        max_concurrent_aedt: int = 1,
        max_concurrent_license_jobs: int = 1,
    ):
        limits = {
            "cpu": max_concurrent_cpu,
            "aedt": max_concurrent_aedt,
            "license": max_concurrent_license_jobs,
        }
        for name, value in limits.items():
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} resource limit must be a positive integer")
        self._semaphores = {
            name: threading.BoundedSemaphore(value)
            for name, value in limits.items()
        }

    def acquire(self, resource_class: str, timeout_seconds: float) -> ResourceLease:
        semaphore = self._semaphores.get(resource_class)
        if semaphore is None:
            raise ValueError(f"unsupported resource class: {resource_class}")
        if timeout_seconds < 0:
            raise ValueError("resource timeout_seconds must be non-negative")
        started = time.monotonic()
        acquired = semaphore.acquire(timeout=timeout_seconds)
        waited = time.monotonic() - started
        if not acquired:
            raise ResourceAcquireTimeout(resource_class, timeout_seconds)
        return ResourceLease(resource_class, waited, semaphore)

    def acquire_many(
        self,
        resource_classes: tuple[str, ...] | list[str],
        timeout_seconds: float,
    ) -> CompositeResourceLease:
        if timeout_seconds < 0:
            raise ValueError("resource timeout_seconds must be non-negative")
        normalized = tuple(
            sorted(
                set(resource_classes),
                key=lambda name: RESOURCE_ORDER.get(name, 99),
            )
        )
        if not normalized:
            raise ValueError("resource_classes must not be empty")
        unknown = [
            name for name in normalized
            if name not in RESOURCE_ORDER
        ]
        if unknown:
            raise ValueError(f"unsupported resource class: {unknown[0]}")
        deadline = time.monotonic() + timeout_seconds
        acquired: list[ResourceLease] = []
        try:
            for name in normalized:
                remaining = max(0.0, deadline - time.monotonic())
                acquired.append(self.acquire(name, remaining))
        except Exception:
            for lease in reversed(acquired):
                lease.release()
            raise
        return CompositeResourceLease(tuple(acquired))
