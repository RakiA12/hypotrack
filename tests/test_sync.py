import pytest

from hypotrack.errors import HypxError
from hypotrack.models import Run
from hypotrack.runner import run_experiment
from hypotrack.sync import cancel_experiment, pull_experiment, reconcile_runs, sync_status
from tests.conftest import sweep_files

CMD = ["bash", "scripts/submit.sh", "-m", "model.dropout=0.1,0.2", "trainer.max_epochs=50"]


def _submitted(project, leaf, cluster):
    s = project.store
    s.new_experiment(leaf, "sweep", "intent")
    run_experiment(s, project.cfg, project.paths, "exp-001", CMD)
    return s.get_experiment("exp-001")


def test_status_derives_completed_partial_failed(project, leaf, cluster, remote_obj):
    _submitted(project, leaf, cluster)
    s = project.store

    cluster.sacct_text = "1001|RUNNING||00:01:00\n1002|PENDING||00:00:00\n"
    [ch] = sync_status(s, project.cfg, remote_obj, None)
    assert (ch.before, ch.after) == ("submitted", "running")
    assert cluster.ssh_calls[-1].startswith("sacct -j 1001,1002 --format=JobID,State,ExitCode,Elapsed --parsable2")

    cluster.sacct_text = "1001|COMPLETED|0:0|00:10:00\n1002|FAILED|1:0|00:02:00\n"
    [ch] = sync_status(s, project.cfg, remote_obj, None)
    assert ch.after == "partial"
    exp = s.get_experiment("exp-001")
    assert exp.runs[1].state == "FAILED" and exp.runs[1].exit_code == "1:0"

    cluster.sacct_text = "1001|FAILED|1:0|1\n1002|TIMEOUT|0:0|1\n"
    [ch] = sync_status(s, project.cfg, remote_obj, ["exp-001"])
    assert ch.after == "failed"

    cluster.sacct_text = "1001|COMPLETED|0:0|1\n1002|COMPLETED|0:0|1\n"
    [ch] = sync_status(s, project.cfg, remote_obj, ["exp-001"])
    assert ch.after == "completed"
    # finished experiments are not polled by default
    assert sync_status(s, project.cfg, remote_obj, None) == []


def test_pull_syncs_results_and_summarizes(project, leaf, cluster, remote_obj):
    exp = _submitted(project, leaf, cluster)
    s = project.store
    cluster.sacct_text = "1001|COMPLETED|0:0|1\n1002|FAILED|1:0|1\n"
    sync_status(s, project.cfg, remote_obj, None)
    exp = s.get_experiment("exp-001")

    td = "2026-11-05_17-41-20"
    cluster.run_dirs = [f"{td}/0", f"{td}/1"]
    cluster.files = sweep_files(td)
    cluster.log_files = {"slurm-1002.out": "Traceback\nValueError: boom\nwandb: View run at https://wandb.ai/me/proj/runs/abc123\n"}

    rep = pull_experiment(s, project.cfg, project.paths, remote_obj, exp)
    exp = s.get_experiment("exp-001")
    assert rep.run_dirs == [f"{td}/0", f"{td}/1"]
    assert [r.subdir for r in exp.runs] == [f"{td}/0", f"{td}/1"]
    assert exp.runs[0].overrides == ["model.dropout=0.1", "trainer.max_epochs=50"]
    assert exp.synced_at is not None
    # metrics summary: best val_loss is run 1 (0.4 at step 200)
    best = exp.summary["metrics"]["val_loss"]
    assert best["run"] == "1" and best["value"] == 0.4 and best["step"] == 200 and best["direction"] == "min"
    assert exp.summary["runs"]["0"]["val_loss"]["last"] == 0.6
    # only the failed run's log was pulled, and its wandb URL extracted
    assert rep.pulled_logs == [".exps/experiments/exp-001/results/slurm-1002.out"]
    assert exp.runs[1].wandb_url == "https://wandb.ai/me/proj/runs/abc123"
    assert exp.runs[0].wandb_url is None
    # rsync call shape: excludes before includes, then exclude *
    args = cluster.rsync_calls[0]
    assert args.index("--exclude") < args.index("--include")
    assert ".hydra/***" in args and "metrics.jsonl" in args and args[-2] == "hpc:~/proj/outputs/exp-001/"
    assert (s.results_dir("exp-001") / td / "1" / "metrics.jsonl").exists()


def test_pull_with_tail_and_include_logs(project, leaf, cluster, remote_obj):
    exp = _submitted(project, leaf, cluster)
    s = project.store
    cluster.run_dirs = ["t/0", "t/1"]
    cluster.files = sweep_files("t")
    cluster.log_files = {"slurm-1001.out": "a\nb\nc\n", "slurm-1002.out": "x\ny\n"}
    rep = pull_experiment(s, project.cfg, project.paths, remote_obj, exp, include_logs=True, tail=2)
    assert sorted(rep.pulled_logs) == [
        ".exps/experiments/exp-001/results/slurm-1001.out",
        ".exps/experiments/exp-001/results/slurm-1002.out",
    ]
    assert any(c.startswith("tail -n 2 ") for c in cluster.ssh_calls)
    # draft experiments cannot be pulled
    s.new_experiment(leaf, "d", "i")
    with pytest.raises(HypxError):
        pull_experiment(s, project.cfg, project.paths, remote_obj, s.get_experiment("exp-002"))


def test_reconcile_runs_single_job_many_dirs():
    from hypotrack.models import Experiment

    exp = Experiment(id="exp-001", hypothesis="H1", title="t", status="running", runs=[Run(job_id="9", state="RUNNING")])
    warnings = reconcile_runs(exp, ["t/0", "t/1", "t/2"])
    assert [r.subdir for r in exp.runs] == ["t/0", "t/1", "t/2"]
    assert all(r.job_id == "9" for r in exp.runs)
    assert warnings
    # idempotent
    assert reconcile_runs(exp, ["t/0", "t/1", "t/2"]) == []


def test_cancel(project, leaf, cluster, remote_obj):
    exp = _submitted(project, leaf, cluster)
    s = project.store
    exp = cancel_experiment(s, project.cfg, remote_obj, exp)
    assert exp.status == "cancelled"
    assert cluster.ssh_calls[-1] == "scancel 1001 1002"
    assert all(r.state == "CANCELLED" for r in s.get_experiment("exp-001").runs)
    with pytest.raises(HypxError):
        cancel_experiment(s, project.cfg, remote_obj, s.get_experiment("exp-001"))
