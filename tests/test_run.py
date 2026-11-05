import pytest

from hypotrack import gitops
from hypotrack.errors import HypxError, RemoteError
from hypotrack.runner import run_experiment, shell_path, submit_command, worktree_command
from tests.conftest import git

CMD = ["bash", "scripts/submit.sh", "-m", "model.dropout=0.1,0.2"]


def test_shell_path_keeps_tilde_expandable():
    assert shell_path("~/proj/.exp-work/exp-001") == '"$HOME"/proj/.exp-work/exp-001'
    assert shell_path("/abs/path with space") == "'/abs/path with space'"
    assert "EXP_ID=exp-001" in submit_command("~/w/exp-001", "exp-001", CMD)
    wt = worktree_command("~/proj/repo", "~/proj/.exp-work", "~/proj/.exp-work/exp-001", "refs/exps/exp-001")
    assert "worktree add --detach" in wt and "checkout -q --detach" in wt


def test_run_happy_path(project, leaf, cluster, tmp_path):
    s = project.store
    s.new_experiment(leaf, "sweep", "intent")
    (project.root / "train.py").write_text("print('changed')\n")

    res = run_experiment(s, project.cfg, project.paths, "exp-001", CMD)

    exp = s.get_experiment("exp-001")
    assert exp.status == "submitted"
    assert res.job_ids == ["1001", "1002"]
    assert [r.job_id for r in exp.runs] == ["1001", "1002"]
    assert exp.code["ref"] == "refs/exps/exp-001" and len(exp.code["tree"]) == 40
    assert exp.hydra["overrides"] == ["model.dropout=0.1,0.2"]
    assert exp.submit["cwd"] == "~/proj/.exp-work/exp-001"
    assert exp.output_root == "~/proj/outputs/exp-001"
    # ref really pushed to the cluster remote
    assert git(tmp_path / "cluster.git", "rev-parse", "refs/exps/exp-001") == res.shadow.commit
    # ssh: worktree then submit, in the worktree, with EXP_ID
    assert len(cluster.ssh_calls) == 2
    assert "worktree add" in cluster.ssh_calls[0]
    assert cluster.ssh_calls[1].startswith('cd "$HOME"/proj/.exp-work/exp-001 && EXP_ID=exp-001 bash scripts/submit.sh')
    assert s.submit_log_path("exp-001").exists()


def test_run_without_job_ids_returns_to_draft(project, leaf, cluster):
    s = project.store
    s.new_experiment(leaf, "sweep", "intent")
    cluster.submit_stdout = "sbatch: error: invalid partition\n"
    cluster.submit_rc = 1
    with pytest.raises(HypxError, match="stays draft"):
        run_experiment(s, project.cfg, project.paths, "exp-001", CMD)
    exp = s.get_experiment("exp-001")
    assert exp.status == "draft" and exp.runs == []
    assert gitops.ref_exists(project.root, "refs/exps/exp-001"), "ref is kept"


def test_run_refuses_non_draft_and_requires_command(project, leaf, cluster):
    s = project.store
    s.new_experiment(leaf, "sweep", "intent")
    run_experiment(s, project.cfg, project.paths, "exp-001", CMD)
    with pytest.raises(HypxError, match="--resubmit"):
        run_experiment(s, project.cfg, project.paths, "exp-001", CMD)
    with pytest.raises(HypxError, match="no submit command"):
        run_experiment(s, project.cfg, project.paths, "exp-001", [])


def test_resubmit_appends_runs(project, leaf, cluster):
    s = project.store
    s.new_experiment(leaf, "sweep", "intent")
    run_experiment(s, project.cfg, project.paths, "exp-001", CMD)
    cluster.submit_stdout = "Submitted batch job 2001\n"
    res = run_experiment(s, project.cfg, project.paths, "exp-001", CMD, resubmit=True, force=True)
    assert res.job_ids == ["2001"]
    assert [r.job_id for r in s.get_experiment("exp-001").runs] == ["1001", "1002", "2001"]
    assert "checkout" in cluster.ssh_calls[-2], "existing worktree is re-pointed, not re-added"


def test_duplicate_code_and_overrides_is_rejected_unless_forced(project, leaf, cluster):
    s = project.store
    s.new_experiment(leaf, "a", "intent")
    s.new_experiment(leaf, "b", "intent")
    run_experiment(s, project.cfg, project.paths, "exp-001", CMD)
    with pytest.raises(HypxError, match="identical overrides as exp-001"):
        run_experiment(s, project.cfg, project.paths, "exp-002", CMD)
    assert s.get_experiment("exp-002").status == "draft"
    # different overrides -> fine
    run_experiment(s, project.cfg, project.paths, "exp-002", [*CMD[:-1], "model.dropout=0.5"])
    assert s.get_experiment("exp-002").status == "submitted"
    # forced duplicate -> warning, submitted
    s.new_experiment(leaf, "c", "intent")
    res = run_experiment(s, project.cfg, project.paths, "exp-003", CMD, force=True)
    assert res.warnings and s.get_experiment("exp-003").status == "submitted"


def test_ssh_failure_leaves_experiment_draft(project, leaf, cluster):
    s = project.store
    s.new_experiment(leaf, "a", "intent")
    cluster.fail_ssh = True
    with pytest.raises(RemoteError):
        run_experiment(s, project.cfg, project.paths, "exp-001", CMD)
    assert s.get_experiment("exp-001").status == "draft"
