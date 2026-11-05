"""Append-only command log at .exps/hypotrack.log, mirrored to stderr with --verbose."""

from __future__ import annotations

import shlex
import sys
from datetime import datetime
from pathlib import Path

_log_path: Path | None = None
_verbose = False


def configure(log_path: Path | None, verbose: bool = False) -> None:
    global _log_path, _verbose
    _log_path = log_path
    _verbose = verbose


def set_verbose(verbose: bool) -> None:
    global _verbose
    _verbose = verbose


def log(message: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {message}"
    if _verbose:
        print(line, file=sys.stderr)
    if _log_path is not None:
        try:
            _log_path.parent.mkdir(parents=True, exist_ok=True)
            with _log_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass


def log_command(argv: list[str], cwd: str | Path | None, returncode: int, stdout: str, stderr: str) -> None:
    where = f" (cwd={cwd})" if cwd else ""
    log(f"$ {shlex.join(argv)}{where} -> rc={returncode}")
    for label, text in (("stdout", stdout), ("stderr", stderr)):
        text = (text or "").rstrip()
        if text:
            for ln in text.splitlines():
                log(f"  [{label}] {ln}")
