"""`hypx status` (sacct sync), `hypx pull` (rsync results back), `hypx cancel` (scancel)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import gitops, metrics, slurm
from .config import Config
from .errors import HypxError
from .models import TERMINAL_STATES, Experiment, Run, now_iso
from .overrides import read_run_overrides
from .paths import Paths
from .remote import Remote
from .runner import shell_path
from .store import Store

WANDB_URL_RE = re.compile(r"https://wandb\.ai/[\w\-./]+/runs/[\w\-]+")


# ----------------------------------------------------------------------------- status


@dataclass
class StatusChange:
    exp: Experiment
    before: str
    after: str


def sync_status(store: Store, cfg: Config, remote: Remote, exp_ids: list[str] | None = None) -> list[StatusChange]:
    if exp_ids:
        targets = [store.get_experiment(e) for e in exp_ids]
    else:
        targets = [e for e in store.list_experiments() if e.status in ("submitted", "running")]
    job_ids: list[str] = []
    for e in targets:
        job_ids.extend(j for j in e.job_ids if j not in job_ids)
    infos = slurm.query_sacct(remote, job_ids) if job_ids else {}

    changes: list[StatusChange] = []
    for exp in targets:
        before = exp.status
        if exp.status == "draft":
            changes.append(StatusChange(exp, before, before))
            continue
        for run in exp.runs:
            if not run.job_id:
                continue
            info = slurm.lookup(infos, run.job_id)
            if info is None:
                continue
            run.state = info.state
            run.exit_code = info.exit_code
            run.elapsed = info.elapsed
        exp.status = exp.derive_status()
        store.save_experiment(exp)
        changes.append(StatusChange(exp, before, exp.status))
    return changes


# ----------------------------------------------------------------------------- pull


@dataclass
class PullReport:
    exp: Experiment
    run_dirs: list[str]
    pulled_logs: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def reconcile_runs(exp: Experiment, subdirs: list[str]) -> list[str]:
    """Attach discovered Hydra run subdirs to runs. Returns warnings."""
    warnings: list[str] = []
    assigned = {r.subdir for r in exp.runs if r.subdir}
    new_dirs = [d for d in subdirs if d not in assigned]
    free_runs = [r for r in exp.runs if not r.subdir]
    free_runs.sort(key=lambda r: int(r.job_id) if r.job_id and r.job_id.isdigit() else 0)

    if not new_dirs:
        return warnings
    if len(free_runs) == len(new_dirs):
        for r, d in zip(free_runs, new_dirs):
            r.subdir = d
    elif len(free_runs) == 1:
        # one sbatch call produced several Hydra runs (job array or in-job sweep)
        template = free_runs[0]
        template.subdir = new_dirs[0]
        for d in new_dirs[1:]:
            exp.runs.append(Run(subdir=d, job_id=template.job_id, state=template.state, exit_code=template.exit_code))
        if len(new_dirs) > 1:
            warnings.append(f"{len(new_dirs)} run dirs share job {template.job_id}")
    else:
        for r, d in zip(free_runs, new_dirs):
            r.subdir = d
        for d in new_dirs[len(free_runs):]:
            exp.runs.append(Run(subdir=d))
        warnings.append(
            f"{len(new_dirs)} new run dirs but {len(free_runs)} runs without a dir; mapping is positional"
        )
    return warnings


def slurm_log_remote_path(cfg: Config, exp: Experiment, job_id: str) -> str:
    pattern = cfg.slurm_log_pattern.format(job_id=job_id, exp_id=exp.id)
    if pattern.startswith(("/", "~")):
        return pattern
    cwd = exp.submit.get("cwd") or cfg.exp_work_dir(exp.id)
    return f"{cwd.rstrip('/')}/{pattern}"


def _rsync_remote_path(path: str) -> str:
    # rsync hands the path to the remote shell, so ~ expands there; keep it unquoted.
    return path


def pull_experiment(
    store: Store,
    cfg: Config,
    paths: Paths,
    remote: Remote,
    exp: Experiment,
    *,
    include_logs: bool = False,
    tail: int | None = None,
) -> PullReport:
    if exp.status == "draft":
        raise HypxError(f"{exp.id} is draft; nothing to pull")
    output_dir = exp.output_root or cfg.exp_output_dir(exp.id)
    results_dir = store.results_dir(exp.id)
    results_dir.mkdir(parents=True, exist_ok=True)

    subdirs = remote.find_run_dirs(output_dir)
    report = PullReport(exp=exp, run_dirs=subdirs)
    report.warnings += reconcile_runs(exp, subdirs)

    if subdirs:
        includes = ["*/", ".hydra/***", cfg.metrics_glob]
        remote.rsync(
            _rsync_remote_path(output_dir.rstrip("/") + "/"),
            str(results_dir) + "/",
            includes=includes,
            excludes=list(cfg.pull_exclude),
        )
    else:
        report.warnings.append(f"no Hydra run dirs found under {output_dir}")

    # Slurm logs: failed runs by default, every run with --include-logs
    for run in exp.runs:
        if not run.job_id:
            continue
        if not (include_logs or run.is_failed):
            continue
        rpath = slurm_log_remote_path(cfg, exp, run.job_id)
        local = results_dir / Path(rpath).name
        if tail:
            text = remote.read_file_tail(rpath, tail)
            if text is None:
                report.warnings.append(f"log not found: {rpath}")
                continue
            local.write_text(text, encoding="utf-8")
        else:
            res = remote.rsync(_rsync_remote_path(rpath), str(local), check=False)
            if not res.ok:
                report.warnings.append(f"log not found: {rpath}")
                continue
        report.pulled_logs.append(paths.rel(local))

    # per-run overrides and wandb URL
    for run in exp.runs:
        rd = metrics.run_results_dir(results_dir, run.subdir)
        if rd is not None and not run.overrides:
            run.overrides = read_run_overrides(rd)
        if not run.wandb_url:
            run.wandb_url = find_wandb_url(results_dir, run, cfg, exp)

    exp.summary = metrics.summarize_experiment(exp, results_dir, cfg)
    exp.synced_at = now_iso()
    store.save_experiment(exp)
    if cfg.auto_commit:
        gitops.commit_paths(paths.root, [store.meta_path(exp.id)], f"hypx pull {exp.id}")
    return report


def find_wandb_url(results_dir: Path, run: Run, cfg: Config, exp: Experiment) -> str | None:
    candidates: list[Path] = []
    if run.job_id:
        candidates.append(results_dir / Path(cfg.slurm_log_pattern.format(job_id=run.job_id, exp_id=exp.id)).name)
    rd = metrics.run_results_dir(results_dir, run.subdir)
    if rd is not None and rd.is_dir():
        candidates.extend(sorted(rd.glob("wandb/latest-run/files/*.json")))
        candidates.extend(sorted(rd.glob("*.log")))
    for p in candidates:
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = WANDB_URL_RE.search(text)
        if m:
            return m.group(0)
    return None


# ----------------------------------------------------------------------------- cancel


def cancel_experiment(store: Store, cfg: Config, remote: Remote, exp: Experiment) -> Experiment:
    if exp.status == "draft":
        raise HypxError(f"{exp.id} is draft; nothing to cancel")
    if exp.is_finished:
        raise HypxError(f"{exp.id} is already {exp.status}")
    slurm.scancel(remote, exp.job_ids)
    for run in exp.runs:
        if run.state is None or run.state not in TERMINAL_STATES:
            run.state = "CANCELLED"
    exp.status = "cancelled"
    store.save_experiment(exp)
    return exp


def local_log_tail(results_dir: Path, cfg: Config, exp: Experiment, run: Run, lines: int = 5) -> list[str]:
    """Last lines of a pulled Slurm log for a run (empty if not pulled)."""
    if not run.job_id:
        return []
    p = results_dir / Path(cfg.slurm_log_pattern.format(job_id=run.job_id, exp_id=exp.id)).name
    if not p.is_file():
        return []
    try:
        return p.read_text(encoding="utf-8", errors="replace").rstrip("\n").splitlines()[-lines:]
    except OSError:
        return []


__all__ = [
    "StatusChange",
    "PullReport",
    "sync_status",
    "pull_experiment",
    "cancel_experiment",
    "reconcile_runs",
    "local_log_tail",
    "shell_path",
]
