import pytest

from hypotrack import gitops
from hypotrack.errors import HypxError
from tests.conftest import git


def test_clean_tree_points_ref_at_head(repo):
    head = git(repo, "rev-parse", "HEAD")
    sc = gitops.shadow_commit(repo, "exp-001", "clean")
    assert sc.created_new is False
    assert sc.commit == head
    assert git(repo, "rev-parse", "refs/exps/exp-001") == head
    assert sc.branch == "main"


def test_dirty_tree_creates_hidden_commit(repo):
    head = git(repo, "rev-parse", "HEAD")
    (repo / "train.py").write_text("print('v2')\n")  # modified tracked file
    (repo / "new_module.py").write_text("x = 1\n")  # untracked file
    (repo / "debug.log").write_text("ignored\n")  # gitignored
    (repo / ".exps").mkdir()
    (repo / ".exps" / "config.yaml").write_text("a: 1\n")  # excluded explicitly

    sc = gitops.shadow_commit(repo, "exp-002", "dirty")
    assert sc.created_new is True
    assert sc.base == head
    assert git(repo, "rev-parse", "HEAD") == head, "branch history untouched"
    files = git(repo, "ls-tree", "-r", "--name-only", "refs/exps/exp-002").splitlines()
    assert "train.py" in files and "new_module.py" in files
    assert "debug.log" not in files
    assert not any(f.startswith(".exps/") for f in files)
    assert git(repo, "show", "refs/exps/exp-002:train.py") == "print('v2')"
    assert git(repo, "log", "-1", "--format=%s", "refs/exps/exp-002") == "exp-002: dirty"
    # working tree and index were not touched
    assert "new_module.py" in git(repo, "status", "--porcelain")


def test_same_content_gives_same_tree(repo):
    (repo / "train.py").write_text("print('v2')\n")
    a = gitops.shadow_commit(repo, "exp-001", "a")
    b = gitops.shadow_commit(repo, "exp-002", "b")
    assert a.tree == b.tree
    (repo / "train.py").write_text("print('v3')\n")
    c = gitops.shadow_commit(repo, "exp-003", "c")
    assert c.tree != a.tree


def test_push_ref_to_cluster_remote(repo, tmp_path):
    (repo / "train.py").write_text("print('v2')\n")
    sc = gitops.shadow_commit(repo, "exp-001", "a")
    gitops.push_ref(repo, "cluster", sc.ref)
    bare = tmp_path / "cluster.git"
    assert git(bare, "rev-parse", "refs/exps/exp-001") == sc.commit


def test_diff_refs_and_missing_ref(repo):
    a = gitops.shadow_commit(repo, "exp-001", "a")
    (repo / "train.py").write_text("print('v2')\n")
    b = gitops.shadow_commit(repo, "exp-002", "b")
    out = gitops.diff_refs(repo, a.ref, b.ref)
    assert "-print('v1')" in out and "+print('v2')" in out
    assert "train.py" in gitops.diff_refs(repo, a.ref, b.ref, stat=True)
    with pytest.raises(HypxError):
        gitops.diff_refs(repo, a.ref, "refs/exps/exp-999")


def test_ensure_remote_is_idempotent(repo):
    assert gitops.ensure_remote(repo, "other", "ssh://h/~/x") is True
    assert gitops.ensure_remote(repo, "other", "ssh://h/~/x") is False
    assert gitops.remote_url(repo, "other") == "ssh://h/~/x"
