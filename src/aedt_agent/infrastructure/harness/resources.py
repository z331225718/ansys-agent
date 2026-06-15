from __future__ import annotations

import threading
import time
from dataclasses import dataclass


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
