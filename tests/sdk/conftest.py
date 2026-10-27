"""Tests that need the real, pinned remora.sdk (upstream/remora.lock.json).

They are deselected, not skipped, when the SDK is not importable, so a CI
run without the REMORA checkout stays green and honest ("deselected" is not
"skipped"). Run them against the pin with:

    PYTHONPATH=<checkout of REMORA-research at the pinned commit> python -m pytest tests/sdk -v
"""
from __future__ import annotations

import pytest


def _sdk_available() -> bool:
    try:
        import remora.sdk  # noqa: F401
        from remora.sdk.effects import verify_effect  # noqa: F401
    except Exception:
        return False
    return True


def pytest_collection_modifyitems(config, items):
    if _sdk_available():
        return
    deselected = [it for it in items if "tests/sdk" in str(it.fspath).replace("\\", "/")]
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        for it in deselected:
            items.remove(it)
