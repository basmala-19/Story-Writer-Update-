import pytest


@pytest.fixture(autouse=True)
def _no_continuity_audit_by_default(monkeypatch):
    """Older tests script only the writer and critic; tests of the audit switch it on explicitly."""
    monkeypatch.setenv("S4_CONTINUITY_AUDIT", "0")
