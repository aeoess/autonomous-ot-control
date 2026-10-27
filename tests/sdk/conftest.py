"""Tests that need the real, pinned remora.sdk (upstream/remora.lock.json).

They are excluded before module import when the SDK is not importable.
The separate SDK CI job checks out the upstream pin and requires the SDK;
that job cannot succeed by excluding these tests. Run against the pin with:

    PYTHONPATH=<checkout of REMORA-research at the pinned commit> python -m pytest --require-remora-sdk tests/sdk -v
"""
from __future__ import annotations

from tests.conftest import remora_sdk_available


def pytest_ignore_collect(collection_path, config):
    if collection_path.name.startswith("test_") and collection_path.suffix == ".py":
        return not remora_sdk_available()
    return None
