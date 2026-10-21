"""Every service module must import cleanly.

This exists because of a specific bug: T2's api_key guard passed every unit test
on the guard function itself, then broke all three apps at import, because
`http://ingestion:8081` (a Compose service name) was classified as a public
collector and demanded a key that did not exist yet.

The general rule it taught: code whose failure mode is "the process will not
boot" cannot be covered by testing the function in isolation. Module-level
construction — publishers, producers, clients, config guards — only fails when
something actually imports the module.

Cheap here, and it catches the whole class locally without needing Docker. The
container-level version of this check (build the image, import inside it) is the
real proof; this is the fast one that runs on every commit.
"""
import importlib

import pytest

# conftest sets a placeholder DATABASE_URL — these modules read it at import time.
APP_MODULES = ["chatbot.main", "ingestion.main", "dashboard.main"]


@pytest.mark.parametrize("module", APP_MODULES)
def test_service_module_imports(module):
    """Import must not raise: no config guard, client or producer may reject boot."""
    importlib.import_module(module)


@pytest.mark.parametrize("module", APP_MODULES)
def test_service_exposes_a_fastapi_app(module):
    """And the import must actually produce an app, not just avoid raising."""
    mod = importlib.import_module(module)
    assert hasattr(mod, "app"), f"{module} defines no `app`"
