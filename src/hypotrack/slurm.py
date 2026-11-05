"""Slurm glue: job id capture from sbatch output, sacct parsing, scancel."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

from .models import ACTIVE_STATES, TERMINAL_STATES
from .remote import Remote

JOB_RE = re.compile(r"Submitted batch job (\d+)")
SACCT_FORMAT = "JobID,State,ExitCode,Elapsed"


def parse_job_ids(text: str) -> list[str]:
    """All `Submitted batch job N` ids in order of appearance, de-duplicated."""
    seen: list[str] = []
    for m in JOB_RE.finditer(text or ""):
        if m.group(1) not in seen:
            seen.append(m.group(1))
    return seen


@dataclass
class JobInfo:
    job_id: str
    state: str
    exit_code: str | None = None
    elapsed: str | None = None


def normalize_state(raw: str) -> str:
    # "CANCELLED by 12345" -> "CANCELLED"; "COMPLETED+" style suffixes are stripped
    s = (raw or "").strip().split()[0] if raw and raw.strip() else ""
    return s.rstrip("+").upper()


def parse_sacct(text: str) -> dict[str, JobInfo]:
    """Parse `sacct --parsable2 --noheader --format=JobID,State,ExitCode,Elapsed` output.

    Step rows (1234.batch, 1234.extern, 1234.0) are ignored; array tasks (1234_0) are kept as-is.
    """
    infos: dict[str, JobInfo] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) < 2:
            continue
        job_id = parts[0].strip()
        if job_id.lower() == "jobid":
            continue  # header
        if "." in job_id:
            continue
        state = normalize_state(parts[1])
        exit_code = parts[2].strip() if len(parts) > 2 and parts[2].strip() else None
        elapsed = parts[3].strip() if len(parts) > 3 and parts[3].strip() else None
        infos[job_id] = JobInfo(job_id=job_id, state=state, exit_code=exit_code, elapsed=elapsed)
    return infos


def lookup(infos: dict[str, JobInfo], job_id: str) -> JobInfo | None:
    """Find a job: exact id, or an aggregate over its array tasks (job_id_N)."""
    if job_id in infos:
        return infos[job_id]
    tasks = [v for k, v in infos.items() if k.startswith(f"{job_id}_")]
    if not tasks:
        return None
    states = [t.state for t in tasks]
    if any(s in ACTIVE_STATES for s in states):
        state = "RUNNING"
    elif not all(s in TERMINAL_STATES for s in states):
        state = "PENDING"
    elif all(s == "COMPLETED" for s in states):
        state = "COMPLETED"
    elif all(s == "CANCELLED" for s in states):
        state = "CANCELLED"
    else:
        state = "FAILED"
    bad = [t for t in tasks if t.state != "COMPLETED" and t.exit_code]
    exit_code = bad[0].exit_code if bad else (tasks[0].exit_code if tasks else None)
    return JobInfo(job_id=job_id, state=state, exit_code=exit_code, elapsed=None)


def query_sacct(remote: Remote, job_ids: list[str]) -> dict[str, JobInfo]:
    if not job_ids:
        return {}
    cmd = f"sacct -j {','.join(job_ids)} --format={SACCT_FORMAT} --parsable2 --noheader"
    res = remote.ssh(cmd)
    return parse_sacct(res.stdout)


def scancel(remote: Remote, job_ids: list[str]) -> None:
    if not job_ids:
        return
    remote.ssh("scancel " + " ".join(shlex.quote(j) for j in job_ids))
