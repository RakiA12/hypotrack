"""Project config (.exps/config.yaml) and the user-level project registry (~/.config/hypotrack/projects.toml)."""

from __future__ import annotations

import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from .errors import HypxError
from .paths import PROJECTS_TOML, Paths

DEFAULT_EXCLUDES = ["*.ckpt", "*.pt", "*.safetensors", "*.pth", "wandb/"]


@dataclass
class Config:
    project: str = ""
    cluster_host: str = ""
    # Path of the repository clone on the cluster, e.g. ~/proj/repo
    remote_repo: str = ""
    # Where per-experiment worktrees are created, e.g. ~/proj/.exp-work
    work_root: str = ""
    # Where Hydra writes outputs/<exp-id>/..., e.g. ~/proj/outputs
    output_root: str = ""
    # Name of the git remote pointing at the cluster clone
    git_remote: str = "cluster"
    metrics_glob: str = "metrics.jsonl"
    metrics_step_key: str = "step"
    default_metrics: list[str] = field(default_factory=list)
    # metric name -> "min" | "max". Unlisted metrics fall back to a name heuristic.
    metric_direction: dict[str, str] = field(default_factory=dict)
    pull_exclude: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDES))
    # Pattern of the Slurm stdout file relative to the submit cwd. {job_id} is substituted.
    slurm_log_pattern: str = "slurm-{job_id}.out"
    auto_commit: bool = False

    @classmethod
    def load(cls, paths: Paths) -> "Config":
        if not paths.config.exists():
            raise HypxError(f"missing {paths.rel(paths.config)}; run `hypx init`")
        data = yaml.safe_load(paths.config.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise HypxError(f"{paths.rel(paths.config)} must be a mapping")
        known = {f for f in cls.__dataclass_fields__}
        kwargs = {k: v for k, v in data.items() if k in known}
        cfg = cls(**kwargs)
        if cfg.default_metrics is None:
            cfg.default_metrics = []
        return cfg

    def save(self, paths: Paths) -> None:
        paths.exps.mkdir(parents=True, exist_ok=True)
        paths.config.write_text(
            yaml.safe_dump(asdict(self), sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

    def direction_for(self, metric: str) -> str:
        if metric in self.metric_direction:
            return self.metric_direction[metric]
        lowered = metric.lower()
        if any(tok in lowered for tok in ("loss", "error", "err", "perplexity", "ppl", "mse", "mae", "rmse")):
            return "min"
        return "max"

    def remote_url(self) -> str:
        """ssh:// URL for the cluster clone (used by `git remote add`)."""
        if not self.cluster_host or not self.remote_repo:
            raise HypxError("cluster_host and remote_repo must be set in .exps/config.yaml")
        path = self.remote_repo
        if path.startswith("~"):
            # ssh://host/~/path is the git syntax for home-relative paths
            return f"ssh://{self.cluster_host}/{path}"
        if not path.startswith("/"):
            return f"ssh://{self.cluster_host}/~/{path}"
        return f"ssh://{self.cluster_host}{path}"

    def exp_work_dir(self, exp_id: str) -> str:
        return f"{self.work_root.rstrip('/')}/{exp_id}"

    def exp_output_dir(self, exp_id: str) -> str:
        return f"{self.output_root.rstrip('/')}/{exp_id}"


# ----------------------------------------------------------------------------
# ~/.config/hypotrack/projects.toml
# ----------------------------------------------------------------------------


def _projects_path(path: Path | None) -> Path:
    # Resolved at call time so tests (and HYPOTRACK_PROJECTS_TOML) can redirect it.
    import os

    if path is not None:
        return path
    env = os.environ.get("HYPOTRACK_PROJECTS_TOML")
    return Path(env) if env else PROJECTS_TOML


def load_projects(path: Path | None = None) -> dict[str, dict[str, str]]:
    path = _projects_path(path)
    if not path.exists():
        return {}
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    return {k: dict(v) for k, v in data.items() if isinstance(v, dict)}


def _toml_str(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def save_projects(projects: dict[str, dict[str, str]], path: Path | None = None) -> None:
    path = _projects_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for name in sorted(projects):
        lines.append(f"[{_toml_str(name)}]" if not name.replace("-", "").replace("_", "").isalnum() else f"[{name}]")
        for key, value in projects[name].items():
            lines.append(f"{key} = {_toml_str(str(value))}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def register_project(cfg: Config, paths: Paths, path: Path | None = None) -> None:
    path = _projects_path(path)
    projects = load_projects(path)
    projects[cfg.project] = {
        "path": str(paths.root),
        "cluster_host": cfg.cluster_host,
        "remote_repo": cfg.remote_repo,
    }
    save_projects(projects, path)
