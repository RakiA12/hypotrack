"""Fixtures: a real temporary git repo with .exps/, and a scripted fake cluster (ssh / rsync / sacct)."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from hypotrack import hlog, remote
from hypotrack.config import Config
from hypotrack.initcmd import init_project
from hypotrack.paths import Paths
from hypotrack.remote import CmdResult, Remote
from hypotrack.store import Store


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Git repo with one commit and a bare 'cluster' remote so pushes really happen."""
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "tester")
    (r / "train.py").write_text("print('v1')\n")
    (r / ".gitignore").write_text("*.log\n")
    git(r, "add", ".")
    git(r, "commit", "-q", "-m", "init")
    bare = tmp_path / "cluster.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    git(r, "remote", "add", "cluster", str(bare))
    hlog.configure(None, verbose=False)
    return r


@dataclass
class Project:
    root: Path
    paths: Paths
    cfg: Config
    store: Store


@pytest.fixture
def project(repo: Path) -> Project:
    res = init_project(
        repo,
        cluster_host="hpc",
        remote_repo="~/proj/repo",
        default_metrics=["val_loss"],
        register=False,
    )
    # the fixture repo already has a 'cluster' remote pointing at the bare repo; keep it
    return Project(root=repo, paths=res.paths, cfg=Config.load(res.paths), store=Store(res.paths))


@pytest.fixture
def leaf(project: Project) -> str:
    project.store.new_hypothesis("root", None, statement="root statement")
    h = project.store.new_hypothesis("leaf", "H1", statement="leaf statement")
    return h.id


@dataclass
class FakeCluster:
    """Scripted responses for ssh / rsync. `files` are written into the rsync destination."""

    submit_stdout: str = "Submitted batch job 1001\nSubmitted batch job 1002\n"
    submit_rc: int = 0
    sacct_text: str = ""
    run_dirs: list[str] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)  # relative to output dir -> content
    log_files: dict[str, str] = field(default_factory=dict)  # basename -> content
    ssh_calls: list[str] = field(default_factory=list)
    rsync_calls: list[list[str]] = field(default_factory=list)
    fail_ssh: bool = False

    def ssh(self, host: str, command: str, check: bool = True) -> CmdResult:
        self.ssh_calls.append(command)
        if self.fail_ssh:
            res = CmdResult(255, "", "ssh: connect to host hpc port 22: Connection refused")
            if check:
                raise remote.RemoteError(res.stderr)
            return res
        if command.startswith("sacct"):
            return CmdResult(0, self.sacct_text, "")
        if command.startswith("scancel"):
            return CmdResult(0, "", "")
        if "find ." in command:
            return CmdResult(0, "\n".join(f"./{d}" for d in self.run_dirs) + "\n", "")
        if command.startswith("tail "):
            name = command.rsplit("/", 1)[-1].strip("'\"")
            if name in self.log_files:
                return CmdResult(0, self.log_files[name], "")
            return CmdResult(1, "", "tail: no such file")
        if "worktree add" in command or "checkout" in command:
            return CmdResult(0, "", "")
        if "EXP_ID=" in command:
            res = CmdResult(self.submit_rc, self.submit_stdout, "")
            if check and res.returncode != 0:
                raise remote.RemoteError("submit failed")
            return res
        return CmdResult(0, "", "")

    def rsync(self, args: list[str], check: bool = True) -> CmdResult:
        self.rsync_calls.append(args)
        src, dest = args[-2], args[-1]
        if dest.endswith("/"):
            # directory sync: materialize scripted files
            for rel, content in self.files.items():
                p = Path(dest) / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content)
            return CmdResult(0, "", "")
        name = src.rsplit("/", 1)[-1]
        if name in self.log_files:
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            Path(dest).write_text(self.log_files[name])
            return CmdResult(0, "", "")
        res = CmdResult(23, "", "rsync: link_stat failed: No such file")
        if check:
            raise remote.RemoteError(res.stderr)
        return res


@pytest.fixture
def cluster(monkeypatch: pytest.MonkeyPatch) -> FakeCluster:
    fake = FakeCluster()
    monkeypatch.setattr(remote, "run_ssh", fake.ssh)
    monkeypatch.setattr(remote, "run_rsync", fake.rsync)
    return fake


@pytest.fixture
def remote_obj(project: Project, cluster: FakeCluster) -> Remote:
    return Remote(project.cfg.cluster_host)


METRICS_RUN0 = "\n".join(
    [
        '{"step": 100, "val_loss": 0.9, "train_loss": 0.8}',
        '{"step": 200, "val_loss": 0.5, "train_loss": 0.4}',
        '{"step": 300, "val_loss": 0.6, "train_loss": 0.3}',
    ]
)
METRICS_RUN1 = "\n".join(
    [
        '{"step": 100, "val_loss": 0.8, "train_loss": 0.7}',
        '{"step": 200, "val_loss": 0.4, "train_loss": 0.35}',
        '{"step": 300, "val_loss": 0.45, "train_loss": 0.3}',
    ]
)


def sweep_files(timedir: str = "2026-11-05_17-41-20") -> dict[str, str]:
    return {
        f"{timedir}/0/.hydra/overrides.yaml": "- model.dropout=0.1\n- trainer.max_epochs=50\n",
        f"{timedir}/0/.hydra/config.yaml": "model:\n  dropout: 0.1\n",
        f"{timedir}/0/metrics.jsonl": METRICS_RUN0 + "\n",
        f"{timedir}/1/.hydra/overrides.yaml": "- model.dropout=0.2\n- trainer.max_epochs=50\n",
        f"{timedir}/1/.hydra/config.yaml": "model:\n  dropout: 0.2\n",
        f"{timedir}/1/metrics.jsonl": METRICS_RUN1 + "\n",
    }
