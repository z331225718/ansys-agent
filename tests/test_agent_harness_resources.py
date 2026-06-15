from __future__ import annotations

import threading
import time

import pytest

from aedt_agent.infrastructure.harness.resources import (
    ResourceAcquireTimeout,
    ResourceGate,
)


def test_aedt_resource_gate_serializes_two_workers():
    gate = ResourceGate(max_concurrent_cpu=2, max_concurrent_aedt=1, max_concurrent_license_jobs=1)
    first_entered = threading.Event()
    second_entered = threading.Event()
    release_first = threading.Event()

    def first_worker():
        with gate.acquire("aedt", timeout_seconds=2):
            first_entered.set()
            release_first.wait(timeout=2)

    def second_worker():
        with gate.acquire("aedt", timeout_seconds=2):
            second_entered.set()

    first = threading.Thread(target=first_worker)
    second = threading.Thread(target=second_worker)
    first.start()
    assert first_entered.wait(timeout=1)
    second.start()
    time.sleep(0.1)

    assert not second_entered.is_set()

    release_first.set()
    first.join(timeout=2)
    second.join(timeout=2)
    assert second_entered.is_set()


def test_license_and_aedt_resource_limits_are_independent():
    gate = ResourceGate(max_concurrent_cpu=1, max_concurrent_aedt=1, max_concurrent_license_jobs=1)

    with gate.acquire("aedt", timeout_seconds=0.1):
        with gate.acquire("license", timeout_seconds=0.1):
            pass


def test_resource_gate_times_out_when_slot_is_occupied():
    gate = ResourceGate(max_concurrent_cpu=1, max_concurrent_aedt=1, max_concurrent_license_jobs=1)

    with gate.acquire("license", timeout_seconds=0.1):
        with pytest.raises(ResourceAcquireTimeout, match="license"):
            with gate.acquire("license", timeout_seconds=0.05):
                raise AssertionError("unreachable")


def test_resource_gate_rejects_unknown_resource_class():
    gate = ResourceGate(max_concurrent_cpu=1, max_concurrent_aedt=1, max_concurrent_license_jobs=1)

    with pytest.raises(ValueError, match="unsupported resource class"):
        gate.acquire("gpu", timeout_seconds=1)


@pytest.mark.parametrize("value", [0, -1, True])
def test_resource_gate_rejects_invalid_limits(value):
    with pytest.raises(ValueError, match="positive integer"):
        ResourceGate(
            max_concurrent_cpu=value,
            max_concurrent_aedt=1,
            max_concurrent_license_jobs=1,
        )
