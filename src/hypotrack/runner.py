"""`hypx run`: shadow commit -> push -> cluster worktree -> submit command -> capture job ids."""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field

from . import gitops, slurm
from .config import Config
from .errors import HypxError, RemoteError
from .models import Experiment, Run, now_iso
from .overrides import config_name_from, extract_overrides
from .paths import Paths
from .remote import Remote
from .store import Store


def shell_path(path: str) -> str:
    """Quote a cluster path for sh while keeping a leading ~ expandable."""
    if path == "~":
        return '"$HOME"'
    if path.startswith("~/"):
        return '"$HOME"/' + shlex.quote(path[2:])
    return shlex.quote(path)


def worktree_command(remote_repo: str, work_root: str, work_dir: str, ref: str) -> str:
    """Idempotent: create the worktree, or re-point an existing one at the ref (resubmit)."""
    wt, root, repo, r = shell_path(work_dir), shell_path(work_root), shell_path(remote_repo), shlex.quote(ref)
    return (
        f"if [ -d {wt} ]; then git -C {wt} checkout -q --detach {r}; "
        f"else mkdir -p {root} && git -C {repo} worktree add --detach {wt} {r}; fi"
    )


def submit_command(work_dir: str, exp_id: str, command: list[str]) -> str:
    return f"cd {shell_path(work_dir)} && EXP_ID={shlex.quote(exp_id)} {shlex.join(command)}"


def find_duplicate(store: Store, exp: Experiment, tree: str, overrides: list[str]) -> Experiment | None:
    """Most recent other experiment that was submitted with the same tree hash and overrides."""
    wanted = sorted(overrides)
    for other in reversed(store.list_experiments()):
        if other.id == exp.id or other.status == "draft":
            continue
        if other.code.get("tree") == tree and sorted(other.overrides) == wanted:
            return other
    return None


@dataclass
class RunResult:
    exp: Experiment
    shadow: gitops.ShadowCommit
    job_ids: list[str]
    warnings: list[str] = field(default_factory=list)
    committed: bool = False


def run_experiment(
    store: Store,
    cfg: Config,
    paths: Paths,
    exp_id: str,
    command: list[str],
    *,
    resubmit: bool = False,
    force: bool = False,
    remote: Remote | None = None,
) -> RunResult:
    if not command:
        raise HypxError("no submit command given; usage: hypx run exp-012 -- bash scripts/submit.sh -m ...")
    exp = store.get_experiment(exp_id)
    if exp.status != "draft" and not resubmit:
        raise HypxError(f"{exp_id} is {exp.status}, not draft; pass --resubmit to submit it again")
    for key in ("cluster_host", "remote_repo", "work_root", "output_root"):
        if not getattr(cfg, key):
            raise HypxError(f"config.yaml: {key} is not set (run `hypx init --cluster-host ... --remote-repo ...`)")

    remote = remote or Remote(cfg.cluster_host)
    repo = paths.root
    warnings: list[str] = []

    # 1. shadow commit (local, kept even if later steps fail)
    shadow = gitops.shadow_commit(repo, exp_id, exp.title)
    overrides = extract_overrides(command)
    exp.code = {
        "ref": shadow.ref,
        "commit": shadow.commit,
        "tree": shadow.tree,
        "base": gitops.short_sha(shadow.base),
        "branch": shadow.branch,
    }
    exp.hydra = {"overrides": overrides, "config_name": config_name_from(command)}
    exp.status = "draft"
    store.save_experiment(exp)

    # 2. duplicate detection
    dup = find_duplicate(store, exp, shadow.tree, overrides)
    if dup is not None:
        msg = f"same code (tree {shadow.tree[:8]}) and identical overrides as {dup.id}"
        if not force:
            raise HypxError(msg + "; nothing submitted. Pass --force to submit anyway.")
        warnings.append(msg + " (submitted anyway because of --force)")

    # 3. push the ref straight to the cluster clone (never via origin)
    gitops.push_ref(repo, cfg.git_remote, shadow.ref)

    # 4. per-experiment worktree on the cluster
    work_dir = cfg.exp_work_dir(exp_id)
    try:
        remote.ssh(worktree_command(cfg.remote_repo, cfg.work_root, work_dir, shadow.ref))
    except RemoteError as exc:
        raise RemoteError(f"could not prepare worktree {work_dir}: {exc}") from exc

    # 5. run the submit command inside the worktree
    res = remote.ssh(submit_command(work_dir, exp_id, command), check=False)
    log_path = store.submit_log_path(exp_id)
    log_path.write_text(
        f"$ {submit_command(work_dir, exp_id, command)}\n# rc={res.returncode}\n"
        f"--- stdout ---\n{res.stdout}\n--- stderr ---\n{res.stderr}\n",
        encoding="utf-8",
    )
    exp.submit = {"command": shlex.join(command), "cwd": work_dir, "submitted_at": now_iso()}
    exp.output_root = cfg.exp_output_dir(exp_id)

    # 6. capture job ids
    job_ids = slurm.parse_job_ids(res.stdout + "\n" + res.stderr)
    if not job_ids:
        exp.status = "draft"
        store.save_experiment(exp)
        raise HypxError(
            f"no 'Submitted batch job N' line in submit output (rc={res.returncode}); "
            f"{exp_id} stays draft. See {paths.rel(log_path)}"
        )
    if res.returncode != 0:
        warnings.append(f"submit command exited with rc={res.returncode} but job ids were captured")

    known = set(exp.job_ids)
    exp.runs.extend(Run(job_id=j) for j in job_ids if j not in known)
    exp.status = "submitted"
    exp.synced_at = None
    store.save_experiment(exp)

    committed = False
    if cfg.auto_commit:
        committed = gitops.commit_paths(
            repo, [store.meta_path(exp_id), store.notes_path(exp_id)], f"hypx run {exp_id}"
        )
    return RunResult(exp=exp, shadow=shadow, job_ids=job_ids, warnings=warnings, committed=committed)
