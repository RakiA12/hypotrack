import json
import os
import subprocess
import sys

import pytest
from typer.testing import CliRunner

from hypotrack.cli import app, main

runner = CliRunner()


@pytest.fixture
def in_repo(repo, monkeypatch):
    monkeypatch.chdir(repo)
    monkeypatch.setattr("sys.stdin", open(os.devnull))
    return repo


def run(*args, input=None):
    return runner.invoke(app, list(args), input=input, catch_exceptions=False)


def test_full_local_cycle(in_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("HYPOTRACK_PROJECTS_TOML", str(tmp_path / "projects.toml"))
    r = run("init", "--cluster-host", "hpc", "--remote-repo", "~/proj/repo", "--metric", "val_loss")
    assert r.exit_code == 0, r.output
    assert (in_repo / ".exps" / "config.yaml").exists()
    assert (in_repo / ".claude" / "skills" / "hypotrack" / "SKILL.md").exists()
    assert "registered project" in r.output
    toml = (tmp_path / "projects.toml").read_text()
    assert "cluster_host" in toml

    assert "## Experiments (hypotrack)" in run("init", "--claude-md").output

    r = run("hyp", "new", "--title", "root", "--body", "root statement")
    assert "created H1" in r.output
    r = run("hyp", "new", "--parent", "H1", "--title", "leaf")
    assert "created H1.1" in r.output
    r = run("exp", "new", "--hyp", "H1.1", "--title", "first", "--body", "because")
    assert "created exp-001" in r.output
    r = run("exp", "new", "--hyp", "H1.1", "--title", "second", "--body", "-", input="piped intent\n")
    assert "created exp-002" in r.output
    assert "piped intent" in (in_repo / ".exps/experiments/exp-002/notes.md").read_text()

    r = run("note", "exp-001", "--body", "looks good")
    assert "appended to .exps/experiments/exp-001/notes.md" in r.output

    r = run("list", "--json")
    rows = json.loads(r.output)
    assert [x["id"] for x in rows] == ["exp-001", "exp-002"]
    assert run("list", "--status", "draft").output.count("exp-") == 2
    assert "(no experiments)" in run("list", "--status", "running").output

    r = run("hyp", "conclude", "H1.1", "--status", "supported", "--evidence", "exp-001", "--body", "done")
    assert "H1.1 -> supported" in r.output
    tree = run("hyp", "tree").output
    assert "- H1.1 [supported] leaf (2 exps) — done" in tree
    assert json.loads(run("hyp", "tree", "--json").output)[1]["status"] == "supported"

    ctx = run("context", "--hyp", "H1.1").output
    assert "# context: H1.1" in ctx and "exp-001" in ctx
    assert json.loads(run("context", "--json", "--budget", "200").output)["hypothesis"] is None

    assert "rebuilt .exps/index.sqlite" in run("reindex").output
    assert (in_repo / ".exps" / "index.sqlite").exists()
    assert "## runs" not in run("show", "exp-001").output  # draft: no runs section
    assert "- hypothesis: H1.1 — leaf [supported]" in run("show", "exp-001").output


def test_errors_go_to_stderr_with_exit_codes(in_repo, capsys):
    assert main(["list"]) == 1  # no .exps yet
    assert "not inside a hypotrack project" in capsys.readouterr().err
    run("init", "--no-register")
    assert main(["gc"]) == 3
    assert "phase 2" in capsys.readouterr().err
    assert main(["exp", "new", "--hyp", "H9", "--title", "x", "--body", "y"]) == 1
    assert main(["list", "--all-projects"]) == 3
    assert main(["results", "exp-001"]) == 1
    assert main(["--version"]) == 0
    assert "hypotrack" in capsys.readouterr().out
    assert main(["bogus"]) == 2  # usage error from typer


def test_missing_body_does_not_hang_without_tty(repo):
    """Agents run hypx with a non-TTY stdin that never closes; the CLI must not block on it."""
    subprocess.run([sys.executable, "-m", "hypotrack.cli", "init", "--no-register"], cwd=repo, check=True, capture_output=True)
    subprocess.run([sys.executable, "-m", "hypotrack.cli", "hyp", "new", "--title", "root"], cwd=repo, check=True, capture_output=True, stdin=subprocess.PIPE, timeout=15)
    proc = subprocess.run(
        [sys.executable, "-m", "hypotrack.cli", "exp", "new", "--hyp", "H1", "--title", "t"],
        cwd=repo,
        capture_output=True,
        text=True,
        stdin=subprocess.PIPE,
        timeout=15,
    )
    assert proc.returncode == 1
    assert "intent is required" in proc.stderr
    proc = subprocess.run(
        [sys.executable, "-m", "hypotrack.cli", "exp", "new", "--hyp", "H1", "--title", "t"],
        cwd=repo,
        capture_output=True,
        text=True,
        input="piped\n",
        timeout=15,
    )
    assert proc.returncode == 0, proc.stderr
    assert "created exp-001" in proc.stdout
