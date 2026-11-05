import pytest

from hypotrack.errors import HypxError


def test_new_hypothesis_tree(project):
    s = project.store
    root = s.new_hypothesis("root")
    assert root.id == "H1"
    child = s.new_hypothesis("child", "H1", statement="stmt")
    assert child.id == "H1.1"
    assert s.new_hypothesis("child2", "H1").id == "H1.2"
    assert s.new_hypothesis("root2").id == "H2"
    assert s.hypothesis_ids() == ["H1", "H1.1", "H1.2", "H2"]
    assert not s.is_leaf("H1") and s.is_leaf("H1.1")
    with pytest.raises(HypxError):
        s.new_hypothesis("x", "H9")


def test_experiment_requires_leaf(project, leaf):
    s = project.store
    exp = s.new_experiment(leaf, "first", "the intent")
    assert exp.id == "exp-001" and exp.status == "draft"
    notes = s.read_notes("exp-001")
    assert notes.startswith("# exp-001: first") and "the intent" in notes
    with pytest.raises(HypxError):
        s.new_experiment("H1", "on a non-leaf", "x")
    # a hypothesis with experiments cannot get children
    with pytest.raises(HypxError):
        s.new_hypothesis("child of leaf", leaf)


def test_notes_append_and_sections(project, leaf):
    s = project.store
    s.new_experiment(leaf, "e", "intent text")
    assert not s.has_discussion("exp-001")
    s.append_note("exp-001", "observation 1")
    s.append_note("exp-001", "observation 2\nsecond line")
    sections = s.note_sections("exp-001")
    assert len(sections) == 3
    assert sections[0][0].endswith("intent")
    assert sections[-1][1] == "observation 2\nsecond line"
    assert s.has_discussion("exp-001")


def test_save_and_reload_experiment(project, leaf):
    s = project.store
    exp = s.new_experiment(leaf, "e", "i")
    exp.status = "submitted"
    exp.code = {"ref": "refs/exps/exp-001"}
    s.save_experiment(exp)
    back = s.get_experiment("exp-001")
    assert back.status == "submitted" and back.code["ref"] == "refs/exps/exp-001"
    assert s.experiments_for(leaf)[0].id == "exp-001"
    with pytest.raises(HypxError):
        s.get_experiment("exp-999")
