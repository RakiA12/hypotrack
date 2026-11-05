"""Thin subprocess layer for ssh / rsync. Tests monkeypatch run_ssh / run_rsync."""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass

from . import hlog
from .errors import RemoteError

SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]


@dataclass
class CmdResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _run(argv: list[str]) -> CmdResult:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RemoteError(f"{argv[0]} not found on PATH") from exc
    hlog.log_command(argv, None, proc.returncode, proc.stdout, proc.stderr)
    return CmdResult(proc.returncode, proc.stdout, proc.stderr)


def run_ssh(host: str, command: str, check: bool = True) -> CmdResult:
    """Run `command` through `ssh host`. Raises RemoteError when check and the command fails."""
    if not host:
        raise RemoteError("cluster_host is not configured (.exps/config.yaml)")
    result = _run(["ssh", *SSH_OPTS, host, command])
    if check and not result.ok:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RemoteError(f"ssh {host} failed (rc={result.returncode}): {detail}")
    return result


def run_rsync(args: list[str], check: bool = True) -> CmdResult:
    result = _run(["rsync", *args])
    if check and not result.ok:
        raise RemoteError(f"rsync failed (rc={result.returncode}): {result.stderr.strip()}")
    return result


class Remote:
    """Cluster access bound to one host. Delegates to the module functions so tests can patch them."""

    def __init__(self, host: str) -> None:
        self.host = host

    def ssh(self, command: str, check: bool = True) -> CmdResult:
        return run_ssh(self.host, command, check=check)

    def ssh_argv(self, argv: list[str], check: bool = True) -> CmdResult:
        return self.ssh(shlex.join(argv), check=check)

    def rsync(
        self,
        remote_path: str,
        local_dest: str,
        includes: list[str] | None = None,
        excludes: list[str] | None = None,
        extra: list[str] | None = None,
        check: bool = True,
    ) -> CmdResult:
        args = ["-az", "--prune-empty-dirs", "-e", "ssh " + " ".join(SSH_OPTS)]
        for e in excludes or []:
            args += ["--exclude", e]
        for i in includes or []:
            args += ["--include", i]
        if includes:
            args += ["--exclude", "*"]
        args += extra or []
        args += [f"{self.host}:{remote_path}", local_dest]
        return run_rsync(args, check=check)

    def read_file_tail(self, remote_path: str, lines: int) -> str | None:
        """Return the last `lines` lines of a remote file, or None if it does not exist."""
        res = self.ssh(f"tail -n {int(lines)} {shlex.quote(remote_path)}", check=False)
        return res.stdout if res.ok else None

    def find_run_dirs(self, output_dir: str) -> list[str]:
        """List Hydra run subdirs under output_dir (dirs that contain .hydra/), relative to output_dir."""
        cmd = (
            f"test -d {shlex.quote(output_dir)} && cd {shlex.quote(output_dir)} && "
            "find . -mindepth 1 -maxdepth 4 -type d -name .hydra -printf '%h\\n' 2>/dev/null "
            "|| find . -mindepth 1 -maxdepth 4 -type d -name .hydra 2>/dev/null | sed 's#/.hydra$##'"
        )
        res = self.ssh(cmd, check=False)
        if not res.ok:
            return []
        out: list[str] = []
        for ln in res.stdout.splitlines():
            ln = ln.strip()
            if not ln or ln == ".":
                continue
            out.append(ln[2:] if ln.startswith("./") else ln)
        return sorted(set(out))
