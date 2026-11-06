"""Repository root discovery and the fixed layout under .exps/."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .errors import HypxError

EXPS_DIR = ".exps"
USER_CONFIG_DIR = Path.home() / ".config" / "hypotrack"
PROJECTS_TOML = USER_CONFIG_DIR / "projects.toml"


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def exps(self) -> Path:
        return self.root / EXPS_DIR

    @property
    def config(self) -> Path:
        return self.exps / "config.yaml"

    @property
    def hyp_index(self) -> Path:
        """hypotheses.yaml: metadata of every hypothesis in one file, keyed by id (tool-written)."""
        return self.exps / "hypotheses.yaml"

    @property
    def hypotheses(self) -> Path:
        """hypotheses/H1.1.md: hypothesis prose (statement, rationale, conclusion)."""
        return self.exps / "hypotheses"

    @property
    def exp_index(self) -> Path:
        """experiments.yaml: metadata of every experiment in one file, keyed by id (tool-written)."""
        return self.exps / "experiments.yaml"

    @property
    def experiments(self) -> Path:
        """experiments/exp-001/: notes.md, submit.log and pulled results/ of one experiment."""
        return self.exps / "experiments"

    @property
    def index(self) -> Path:
        return self.exps / "index.sqlite"

    @property
    def log(self) -> Path:
        return self.exps / "hypotrack.log"

    @property
    def gitignore(self) -> Path:
        return self.exps / ".gitignore"

    @property
    def skill(self) -> Path:
        return self.root / ".claude" / "skills" / "hypotrack" / "SKILL.md"

    def exp_dir(self, exp_id: str) -> Path:
        return self.experiments / exp_id

    def rel(self, p: Path) -> str:
        """Repository-relative path string (agents open these with Read)."""
        try:
            return str(p.relative_to(self.root))
        except ValueError:
            return str(p)


def find_root(start: Path | None = None) -> Path | None:
    """Walk upward from start looking for a directory that contains .exps/."""
    cur = (start or Path.cwd()).resolve()
    for candidate in (cur, *cur.parents):
        if (candidate / EXPS_DIR).is_dir():
            return candidate
    return None


def git_root(start: Path | None = None) -> Path | None:
    """Walk upward looking for a .git directory or file (worktree)."""
    cur = (start or Path.cwd()).resolve()
    for candidate in (cur, *cur.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def require_paths(start: Path | None = None) -> Paths:
    root = find_root(start)
    if root is None:
        raise HypxError(
            "not inside a hypotrack project (no .exps/ found). Run `hypx init` at the repository root."
        )
    return Paths(root)
