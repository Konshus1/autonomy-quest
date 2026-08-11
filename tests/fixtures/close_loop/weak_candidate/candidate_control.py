import pytest

@pytest.mark.skip(reason="vacuous weak control")
def test_weak_skipped_control():
    assert True
