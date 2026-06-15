"""Shared pytest fixtures for host tests."""

import pytest


@pytest.fixture
def enable_execution_metadata(monkeypatch):
    """Re-enable execution metadata for unit tests of the gated subsystem."""
    import testing.host.config as cfg
    import testing.host.cell_run_snapshot as crs
    import testing.host.execution_metadata as em
    import testing.host.kernel_session as ks

    monkeypatch.setattr(cfg, "KERNEL_EXECUTION_METADATA_ENABLED", True)
    monkeypatch.setattr(em, "enabled", lambda: True)
    monkeypatch.setattr(crs, "_execution_metadata_enabled", lambda: True)
    monkeypatch.setattr(ks, "_execution_metadata_enabled", lambda: True)
