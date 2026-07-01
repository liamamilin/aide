"""Permission detection tests."""

from ai_desktop.utils.permissions import (
    PermissionStatus,
    check_accessibility,
    check_all,
    check_input_monitoring,
)


def test_check_accessibility_returns_bool():
    result = check_accessibility()
    assert isinstance(result, bool)


def test_check_input_monitoring_returns_bool():
    result = check_input_monitoring()
    assert isinstance(result, bool)


def test_check_all_returns_permission_status():
    result = check_all()
    assert isinstance(result, PermissionStatus)
    assert isinstance(result.accessibility, bool)
    assert isinstance(result.input_monitoring, bool)


def test_permission_status_all_granted():
    assert PermissionStatus(True, True).all_granted is True
    assert PermissionStatus(True, False).all_granted is False
    assert PermissionStatus(False, True).all_granted is False
    assert PermissionStatus(False, False).all_granted is False
