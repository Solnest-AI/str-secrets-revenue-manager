import pytest

from turno_mcp.safety import ConfirmationRequired, require_confirm


def test_require_confirm_blocks_without_confirm():
    with pytest.raises(ConfirmationRequired) as ei:
        require_confirm(False, "delete_project")
    assert "confirm=true" in str(ei.value)
    assert "delete_project" in str(ei.value)


def test_require_confirm_allows_with_confirm():
    assert require_confirm(True, "delete_project") is None
