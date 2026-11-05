import json

import pytest

from hypotrack import metrics
from hypotrack.errors import HypxError
from hypotrack.models import Run
from hypotrack.render import results_table, show_md
from tests.conftest import METRICS_RUN0, sweep_files


def test_metrics_helpers(tmp_path):
    p = tmp_path / "metrics.jsonl"
    p.write_text(METRICS_RUN0 + "\nnot json\n")
    recs = metrics.read_jsonl(p)
    assert len(recs) == 3
    assert metrics.last(recs, "val_loss", "step") == (0.6, 300)
    assert metrics.best(recs, "val_loss", "min", "step") == (0.5, 200)
    assert metrics.best(recs, "val_loss", "max", "step") == (0.9, 100)
    assert metrics.at_step(recs, "val_loss", 200, "step") == 0.5
    assert metrics.at_step(recs, "val_loss", 250, "step") == 0.5  # nearest at/before
    assert metrics.last(recs, "missing", "step") is None
    assert metrics.metric_names(recs, "step") == ["val_loss", "train_loss"]
    assert metrics.fmt_num(0.41234) == "0.4123" and metrics.fmt_num(3) == "3" and metrics.fmt_num(None) == ""


def _pulled_experiment(project, leaf):
    s = project.store
    exp = s.new_experiment(leaf, "sweep", "intent")
    exp.status = "partial"
    exp.hydra = {"overrides": ["model.dropout=0.1,0.2", "trainer.max_epochs=50"]}
    td = "t"
    exp.runs = [
        Run(subdir=f"{td}/0", job_id="1", state="COMPLETED", overrides=["model.dropout=0.1", "trainer.max_epochs=50"]),
        Run(subdir=f"{td}/1", job_id="2", state="FAILED", exit_code="1:0", overrides=["model.dropout=0.2", "trainer.max_epochs=50"]),
    ]
    rd = s.results_dir(exp.id)
    for rel, content in sweep_files(td).items():
        p = rd / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    (rd / "slurm-2.out").write_text("l1\nl2\nl3\nl4\nl5\nl6\nl7\n")
    exp.summary = metrics.summarize_experiment(exp, rd, project.cfg)
    s.save_experiment(exp)
    return exp


def test_results_table_shape(project, leaf):
    exp = _pulled_experiment(project, leaf)
    md, data = results_table(project.store, project.cfg, project.paths, exp)
    header = md.splitlines()
    assert header[0].startswith("# exp-001: sweep  (H1.1)  status: partial")
    assert "fixed: trainer.max_epochs=50" in md
    assert "| run | model.dropout | val_loss (best, step) | val_loss (last) | state |" in md
    assert "| 0 | 0.1 | 0.5 (step 200) | 0.6 | COMPLETED |" in md
    assert "| 1 | 0.2 | 0.4 (step 200) | 0.45 | FAILED (exit 1:0) |" in md
    assert "best: run 1 (model.dropout=0.2) by val_loss min" in md
    # failed run gets the last 5 log lines
    assert "log tail (run 1):" in md and "l3\nl4\nl5\nl6\nl7" in md and "l2" not in md.split("log tail")[1]
    assert data["best"]["run"] == "1" and data["varying_overrides"] == ["model.dropout"]
    assert json.dumps(data)  # serializable


def test_results_options(project, leaf):
    exp = _pulled_experiment(project, leaf)
    md, _ = results_table(project.store, project.cfg, project.paths, exp, metric_names=["train_loss", "val_loss"], best_dir="max")
    assert "| train_loss (best, step) | train_loss (last) | val_loss (last) |" in md
    assert "by train_loss max" in md
    md, _ = results_table(project.store, project.cfg, project.paths, exp, last_only=True)
    assert "(best" not in md and "best:" not in md
    md, data = results_table(project.store, project.cfg, project.paths, exp, step=100)
    assert "val_loss (step 100)" in md and data["runs"][0]["metrics"]["val_loss"]["at_step"] == 0.9
    md, data = results_table(project.store, project.cfg, project.paths, exp, raw=True)
    assert data[0]["files"] == [".exps/experiments/exp-001/results/t/0/metrics.jsonl"]
    assert ".exps/experiments/exp-001/results/t/1/metrics.jsonl" in md


def test_results_requires_pull(project, leaf):
    exp = project.store.new_experiment(leaf, "x", "i")
    with pytest.raises(HypxError, match="hypx pull"):
        results_table(project.store, project.cfg, project.paths, exp)


def test_show(project, leaf):
    exp = _pulled_experiment(project, leaf)
    hyp = project.store.get_hypothesis(leaf)
    md, data = show_md(project.store, project.cfg, project.paths, exp, hyp)
    assert md.startswith("# exp-001: sweep")
    assert "- hypothesis: H1.1 — leaf [open]" in md
    assert "## runs" in md and "## results summary" in md and "## notes" in md
    assert "- notes: .exps/experiments/exp-001/notes.md" in md
    assert data["files"]["results"] == ".exps/experiments/exp-001/results"
