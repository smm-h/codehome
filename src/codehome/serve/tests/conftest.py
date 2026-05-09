"""Shared fixtures for serve tests."""

import pytest

from codehome.serve.services import ServiceInstance, ServiceManager, State


@pytest.fixture
def mgr():
    """Fresh ServiceManager instance (not the singleton)."""
    return ServiceManager()


@pytest.fixture
def make_service():
    """Create a ServiceInstance with defaults."""

    def _make(
        key="test/svc",
        service_type="compose",
        branch="test",
        display_name="Test",
        state=State.STOPPED,
        depends_on=None,
        metadata=None,
    ):
        return ServiceInstance(
            key=key,
            service_type=service_type,
            branch=branch,
            display_name=display_name,
            depends_on=depends_on or [],
            state=state,
            metadata=metadata or {},
        )

    return _make
