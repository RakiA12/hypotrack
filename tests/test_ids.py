import pytest

from hypotrack import ids
from hypotrack.errors import HypxError


def test_hyp_path_and_parent():
    assert ids.hyp_path("H1.2.3") == (1, 2, 3)
    assert ids.parent_of("H1.2.3") == "H1.2"
    assert ids.parent_of("H4") is None
    assert ids.ancestors("H1.2.3") == ["H1", "H1.2", "H1.2.3"]


def test_invalid_hyp_id():
    with pytest.raises(HypxError):
        ids.validate_hyp_id("1.2")
    with pytest.raises(HypxError):
        ids.validate_hyp_id("H1.")


def test_next_child_id():
    existing = ["H1", "H1.1", "H1.3", "H2"]
    assert ids.next_child_id(None, existing) == "H3"
    assert ids.next_child_id("H1", existing) == "H1.4"
    assert ids.next_child_id("H2", existing) == "H2.1"
    assert ids.next_child_id("H1.1", existing) == "H1.1.1"


def test_hyp_sort_key_is_numeric():
    assert sorted(["H10", "H2", "H1.10", "H1.2"], key=ids.hyp_sort_key) == ["H1.2", "H1.10", "H2", "H10"]


def test_next_exp_id():
    assert ids.next_exp_id([]) == "exp-001"
    assert ids.next_exp_id(["exp-001", "exp-012"]) == "exp-013"
    assert ids.next_exp_id(["exp-999"]) == "exp-1000"
    with pytest.raises(HypxError):
        ids.validate_exp_id("exp12")
