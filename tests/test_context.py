from hypotrack.context import build_context, estimate_tokens
from hypotrack.render import hyp_tree_md


def _populate(project):
    s = project.store
    s.new_hypothesis("root", None)  # H1
    s.new_hypothesis("regularization", "H1", statement="dropout helps")  # H1.1
    s.new_hypothesis("optimizer", "H1")  # H1.2
    s.new_hypothesis("dropout rate", "H1.1")  # H1.1.1
    s.new_hypothesis("weight decay", "H1.1")  # H1.1.2
    for i, (hyp, title) in enumerate([("H1.1.1", "sweep dropout"), ("H1.1.1", "sweep dropout 2"), ("H1.2", "adamw"), ("H1.1.2", "wd")]):
        e = s.new_experiment(hyp, title, f"intent {i}")
        if i < 2:
            e.status = "completed"
            e.summary = {"metrics": {"val_loss": {"run": "1", "value": 0.4 - i * 0.01, "step": 200, "direction": "min"}}}
            s.save_experiment(e)
    s.append_note("exp-001", "dropout 0.2 looks best; 0.3 underfits")
    h = s.get_hypothesis("H1.1.1")
    h.status, h.conclusion, h.evidence = "supported", "0.2 is the sweet spot\nmore detail", ["exp-001"]
    s.save_hypothesis(h)


def test_context_sections(project):
    _populate(project)
    md, data = build_context(project.store, project.cfg, project.paths, "H1.1.1", recent=5)
    assert md.startswith("# context: H1.1.1\n")
    assert "- H1 [open] root\n- H1.1 [open] regularization\n- H1.1.1 [supported] dropout rate — 0.2 is the sweet spot" in md
    assert "siblings:\n- H1.1.2 [open] weight decay" in md
    # scope = target + siblings: exp-003 (H1.2) is outside
    ids = [e["id"] for e in data["experiments"]]
    assert ids == ["exp-001", "exp-002", "exp-004"]
    assert "val_loss=0.4 @200 (1)" in md
    assert "## recent (3 of 3)" in md
    assert "dropout 0.2 looks best" in md
    # open items: exp-002 completed but no discussion; exp-004 draft; H1.1.2 open leaf is a sibling, not a child
    assert "completed experiment without discussion in notes: exp-002" in md
    assert "draft experiment not submitted: exp-004" in md
    assert data["open_items"]["undiscussed_experiments"] == ["exp-002"]
    assert data["open_items"]["open_hypotheses"] == []


def test_context_whole_project_and_budget(project):
    _populate(project)
    full, _ = build_context(project.store, project.cfg, project.paths, None, recent=5)
    assert "# context: all hypotheses" in full and "exp-003" in full
    full_tokens = estimate_tokens(full)
    md, data = build_context(project.store, project.cfg, project.paths, None, recent=5, budget=full_tokens // 2)
    assert estimate_tokens(md) <= full_tokens // 2 + 60  # the trailer itself is appended after fitting
    assert data["truncated"] and md.rstrip().endswith("_")
    assert "_truncated to fit --budget" in md
    assert "recent 5 ->" in md


def test_hyp_tree_md_filter(project):
    _populate(project)
    md = hyp_tree_md(project.store)
    assert md.splitlines()[0] == "- H1 [open] root"
    assert "    - H1.1.1 [supported] dropout rate (2 exps) — 0.2 is the sweet spot" in md
    filtered = hyp_tree_md(project.store, "supported")
    assert "H1.1.1" in filtered and "H1.2" not in filtered and "H1 [open]" in filtered  # ancestors kept
