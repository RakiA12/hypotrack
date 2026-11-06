import pytest

from hypotrack.errors import HypxError
from hypotrack.models import Run


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


def test_hypothesis_is_index_entry_plus_markdown(project):
    s = project.store
    h = s.new_hypothesis("root", statement="the claim", rationale="because")
    yaml_path, md_path = s.hyp_files("H1")
    assert yaml_path == s.paths.exps / "hypotheses.yaml"
    assert md_path == s.paths.exps / "hypotheses" / "H1.md"
    assert yaml_path.exists() and md_path.exists()
    assert "statement" not in yaml_path.read_text()
    assert yaml_path.read_text().startswith("H1:\n  parent: null\n  title: root\n")
    s.new_hypothesis("leaf", "H1")
    s.new_hypothesis("root2")
    text = yaml_path.read_text()
    assert text.index("H1:\n") < text.index("H1.1:\n") < text.index("H2:\n")  # one file, sorted by id
    assert [h.title for h in s.list_hypotheses()] == ["root", "leaf", "root2"]
    assert "## Statement\n\nthe claim" in md_path.read_text()
    # hand edits to the .md are what get_hypothesis returns
    md_path.write_text(md_path.read_text().replace("the claim", "refined claim") + "\n## Notes\n\nkeep me\n")
    assert s.get_hypothesis("H1").statement == "refined claim"
    h = s.get_hypothesis("H1")
    h.status, h.conclusion = "supported", "yes"
    s.save_hypothesis(h)
    text = md_path.read_text()
    assert "## Conclusion\n\nyes" in text and "## Notes\n\nkeep me" in text and "refined claim" in text
    assert "status: supported" in yaml_path.read_text()


def test_new_hypothesis_refuses_to_clobber_orphan_markdown(project):
    s = project.store
    s.new_hypothesis("root")
    s.paths.hypotheses.mkdir(parents=True, exist_ok=True)
    (s.paths.hypotheses / "H2.md").write_text("# H2: lost meta\n\n## Statement\n\nprecious\n")
    with pytest.raises(HypxError, match="orphaned"):
        s.new_hypothesis("root2")
    assert "precious" in (s.paths.hypotheses / "H2.md").read_text()


def test_legacy_single_yaml_hypothesis_migrates_on_save(project):
    s = project.store
    s.paths.hypotheses.mkdir(parents=True, exist_ok=True)
    (s.paths.hypotheses / "H1.yaml").write_text(
        "id: H1\nparent: null\ntitle: old\nstatus: open\ncreated: 2026-11-01\nstatement: |\n  from yaml\nrationale: ''\nconclusion: ''\n"
    )
    assert s.hypothesis_ids() == ["H1"] and s.has_hypothesis("H1")
    h = s.get_hypothesis("H1")
    assert h.statement == "from yaml"  # no .md yet: body comes from the YAML
    s.save_hypothesis(h)
    assert not (s.paths.hypotheses / "H1.yaml").exists()  # moved into hypotheses.yaml
    assert "statement" not in s.paths.hyp_index.read_text()
    assert "from yaml" in (s.paths.hypotheses / "H1.md").read_text()
    assert s.hypothesis_ids() == ["H1"]
    assert s.get_hypothesis("H1").statement == "from yaml"


def test_legacy_meta_dir_hypothesis_migrates_on_save(project):
    s = project.store
    meta = s.paths.exps / "meta"
    meta.mkdir()
    (meta / "H1.yaml").write_text("id: H1\nparent: null\ntitle: split\nstatus: open\ncreated: 2026-11-05\nevidence: []\n")
    s.paths.hypotheses.mkdir(parents=True, exist_ok=True)
    (s.paths.hypotheses / "H1.md").write_text("# H1: split\n\n## Statement\n\nfrom md\n")
    assert s.hypothesis_ids() == ["H1"]
    h = s.get_hypothesis("H1")
    assert h.title == "split" and h.statement == "from md"
    s.save_hypothesis(h)
    assert not (meta / "H1.yaml").exists()
    assert "title: split" in s.paths.hyp_index.read_text()
    assert s.get_hypothesis("H1").statement == "from md"


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


def test_experiments_share_one_index_file(project, leaf):
    s = project.store
    s.new_experiment(leaf, "first", "a")
    s.new_experiment(leaf, "second", "b")
    index = s.paths.exps / "experiments.yaml"
    assert s.meta_path("exp-001") == index and index.exists()
    assert not (s.exp_dir("exp-001") / "meta.yaml").exists()
    text = index.read_text()
    assert text.startswith("exp-001:\n  hypothesis: ") and "\nexp-002:\n" in text
    e = s.get_experiment("exp-002")
    e.status, e.runs = "submitted", [Run(job_id="42", overrides=["a=1"])]
    s.save_experiment(e)
    assert s.get_experiment("exp-001").status == "draft"  # untouched sibling entry
    back = s.get_experiment("exp-002")
    assert back.status == "submitted" and back.runs[0].job_id == "42" and back.runs[0].overrides == ["a=1"]
    assert [x.id for x in s.list_experiments()] == ["exp-001", "exp-002"]


def test_legacy_per_dir_meta_yaml_migrates_on_save(project, leaf):
    s = project.store
    d = s.exp_dir("exp-001")
    d.mkdir(parents=True)
    (d / "meta.yaml").write_text(f"id: exp-001\nhypothesis: {leaf}\ntitle: old\nstatus: draft\n")
    (d / "notes.md").write_text("# exp-001: old\n")
    assert s.experiment_ids() == ["exp-001"] and s.has_experiment("exp-001")
    assert s.experiments_for(leaf)[0].title == "old"
    assert s.new_experiment(leaf, "next", "x").id == "exp-002"  # legacy ids count when numbering
    e = s.get_experiment("exp-001")
    s.save_experiment(e)
    assert not (d / "meta.yaml").exists()
    assert "title: old" in s.paths.exp_index.read_text()
    assert s.experiment_ids() == ["exp-001", "exp-002"]


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
