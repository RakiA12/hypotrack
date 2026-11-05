"""Git operations: shadow commits under refs/exps/*, pushing them to the cluster clone, diffs."""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import hlog
from .errors import HypxError

EXPS_REF_PREFIX = "refs/exps/"


def exp_ref(exp_id: str) -> str:
    return f"{EXPS_REF_PREFIX}{exp_id}"


def git(
    args: list[str],
    cwd: Path | str,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    argv = ["git", *args]
    proc = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, env=env)
    hlog.log_command(argv, cwd, proc.returncode, proc.stdout, proc.stderr)
    if check and proc.returncode != 0:
        raise HypxError(f"git {' '.join(args[:2])} failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc


def head_sha(repo: Path) -> str:
    proc = git(["rev-parse", "--verify", "-q", "HEAD"], repo, check=False)
    if proc.returncode != 0:
        raise HypxError("repository has no commits yet; make an initial commit before `hypx run`")
    return proc.stdout.strip()


def short_sha(sha: str) -> str:
    return sha[:8]


def current_branch(repo: Path) -> str | None:
    proc = git(["symbolic-ref", "--short", "-q", "HEAD"], repo, check=False)
    return proc.stdout.strip() or None


def resolve(repo: Path, rev: str) -> str | None:
    proc = git(["rev-parse", "--verify", "-q", rev], repo, check=False)
    return proc.stdout.strip() or None


def ref_exists(repo: Path, ref: str) -> bool:
    return resolve(repo, ref) is not None


def tree_of(repo: Path, rev: str) -> str:
    return git(["rev-parse", f"{rev}^{{tree}}"], repo).stdout.strip()


def is_dirty(repo: Path, exclude: tuple[str, ...] = (".exps",)) -> bool:
    pathspec = [".", *[f":(exclude){e}" for e in exclude]]
    proc = git(["status", "--porcelain", "--untracked-files=all", "--", *pathspec], repo)
    return bool(proc.stdout.strip())


@dataclass
class ShadowCommit:
    ref: str
    commit: str
    tree: str
    base: str
    branch: str | None
    created_new: bool  # False when the working tree was clean and the ref points at HEAD


def shadow_commit(repo: Path, exp_id: str, title: str, exclude: tuple[str, ...] = (".exps",)) -> ShadowCommit:
    """Snapshot the whole working tree (respecting .gitignore, excluding .exps/) into refs/exps/<exp_id>.

    Branch history is untouched: the commit is reachable only from the hidden ref.
    """
    base = head_sha(repo)
    branch = current_branch(repo)
    ref = exp_ref(exp_id)

    if not is_dirty(repo, exclude):
        git(["update-ref", ref, base], repo)
        return ShadowCommit(ref=ref, commit=base, tree=tree_of(repo, base), base=base, branch=branch, created_new=False)

    with tempfile.TemporaryDirectory(prefix="hypx-index-") as td:
        env = dict(os.environ)
        env["GIT_INDEX_FILE"] = str(Path(td) / "index")
        # Start from HEAD so tracked-but-ignored files are kept, then fold in the working tree.
        git(["read-tree", "HEAD"], repo, env=env)
        git(["add", "-A", "--", ".", *[f":(exclude){e}" for e in exclude]], repo, env=env)
        for e in exclude:
            git(["rm", "-r", "-q", "--cached", "--ignore-unmatch", "--", e], repo, env=env, check=False)
        tree = git(["write-tree"], repo, env=env).stdout.strip()

    head_tree = tree_of(repo, base)
    if tree == head_tree:
        git(["update-ref", ref, base], repo)
        return ShadowCommit(ref=ref, commit=base, tree=tree, base=base, branch=branch, created_new=False)

    message = f"{exp_id}: {title}"
    commit_env = dict(os.environ)
    # Fall back to a neutral identity so commit-tree never fails on identity-less machines.
    commit_env.setdefault("GIT_AUTHOR_NAME", "hypotrack")
    commit_env.setdefault("GIT_AUTHOR_EMAIL", "hypotrack@localhost")
    commit_env.setdefault("GIT_COMMITTER_NAME", commit_env["GIT_AUTHOR_NAME"])
    commit_env.setdefault("GIT_COMMITTER_EMAIL", commit_env["GIT_AUTHOR_EMAIL"])
    commit = git(["commit-tree", tree, "-p", base, "-m", message], repo, env=commit_env).stdout.strip()
    git(["update-ref", ref, commit], repo)
    return ShadowCommit(ref=ref, commit=commit, tree=tree, base=base, branch=branch, created_new=True)


def ensure_remote(repo: Path, name: str, url: str) -> bool:
    """Add remote `name` -> url if missing. Returns True when it was added."""
    proc = git(["remote", "get-url", name], repo, check=False)
    if proc.returncode == 0:
        return False
    git(["remote", "add", name, url], repo)
    return True


def remote_url(repo: Path, name: str) -> str | None:
    proc = git(["remote", "get-url", name], repo, check=False)
    return proc.stdout.strip() or None


def push_ref(repo: Path, remote: str, ref: str) -> None:
    from .errors import RemoteError

    if remote_url(repo, remote) is None:
        raise HypxError(f"git remote {remote!r} is not configured; run `hypx init` with --cluster-host/--remote-repo")
    # refs/exps/* are owned by hypotrack and never branches; a resubmit replaces the snapshot,
    # so the refspec is forced for this one ref only.
    proc = git(["push", "--quiet", remote, f"+{ref}:{ref}"], repo, check=False)
    if proc.returncode != 0:
        raise RemoteError(f"git push to {remote} failed: {proc.stderr.strip()}")


def diff_refs(repo: Path, ref_a: str, ref_b: str, stat: bool = False) -> str:
    for r in (ref_a, ref_b):
        if not ref_exists(repo, r):
            raise HypxError(f"ref {r} does not exist (was the experiment ever submitted with `hypx run`?)")
    args = ["diff", "--no-color"]
    if stat:
        args.append("--stat")
    args += [ref_a, ref_b]
    return git(args, repo).stdout


def commit_paths(repo: Path, paths: list[Path], message: str) -> bool:
    """Stage and commit the given paths (auto_commit). Returns False when nothing changed."""
    rel = [str(p) for p in paths if p.exists()]
    if not rel:
        return False
    git(["add", "--", *rel], repo)
    proc = git(["diff", "--cached", "--quiet", "--", *rel], repo, check=False)
    if proc.returncode == 0:
        return False
    git(["commit", "-q", "-m", message, "--", *rel], repo)
    return True
