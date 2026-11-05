"""`hypx init`: create .exps/, config.yaml, the Claude Code skill, the cluster git remote, and register the project."""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from . import gitops
from .config import Config, register_project
from .errors import HypxError
from .paths import EXPS_DIR, Paths, git_root

GITIGNORE_LINES = ["results/", "index.sqlite", "*.sqlite.tmp", "hypotrack.log", "submit.log"]


def template(name: str) -> str:
    return resources.files("hypotrack").joinpath("templates", name).read_text(encoding="utf-8")


def claude_md_snippet() -> str:
    return template("CLAUDE.md.snippet")


def _parent_dir(remote_path: str) -> str:
    p = remote_path.rstrip("/")
    return p.rsplit("/", 1)[0] if "/" in p else "."


@dataclass
class InitResult:
    paths: Paths
    cfg: Config
    created: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def init_project(
    start: Path | None = None,
    *,
    cluster_host: str | None = None,
    remote_repo: str | None = None,
    work_root: str | None = None,
    output_root: str | None = None,
    name: str | None = None,
    default_metrics: list[str] | None = None,
    register: bool = True,
) -> InitResult:
    start = (start or Path.cwd()).resolve()
    root = git_root(start)
    if root is None:
        raise HypxError(f"{start} is not inside a git repository; hypotrack needs one for shadow commits")
    paths = Paths(root)
    result = InitResult(paths=paths, cfg=Config())

    if paths.config.exists():
        cfg = Config.load(paths)
        result.notes.append(f"{EXPS_DIR}/config.yaml exists; updating only the options given")
    else:
        cfg = Config()
        result.created.append(paths.rel(paths.config))

    cfg.project = name or cfg.project or root.name
    if cluster_host is not None:
        cfg.cluster_host = cluster_host
    if remote_repo is not None:
        cfg.remote_repo = remote_repo
    if work_root is not None:
        cfg.work_root = work_root
    elif not cfg.work_root and cfg.remote_repo:
        cfg.work_root = f"{_parent_dir(cfg.remote_repo)}/.exp-work"
    if output_root is not None:
        cfg.output_root = output_root
    elif not cfg.output_root and cfg.remote_repo:
        cfg.output_root = f"{_parent_dir(cfg.remote_repo)}/outputs"
    if default_metrics is not None:
        cfg.default_metrics = list(default_metrics)

    for d in (paths.exps, paths.hypotheses, paths.experiments):
        if not d.exists():
            d.mkdir(parents=True)
            result.created.append(paths.rel(d) + "/")
    cfg.save(paths)

    gi = paths.gitignore
    existing = gi.read_text(encoding="utf-8").splitlines() if gi.exists() else []
    missing = [ln for ln in GITIGNORE_LINES if ln not in existing]
    if missing or not gi.exists():
        gi.write_text("\n".join([*existing, *missing]).strip() + "\n", encoding="utf-8")
        if not existing:
            result.created.append(paths.rel(gi))

    skill = paths.skill
    skill_text = template("SKILL.md")
    if not skill.exists() or skill.read_text(encoding="utf-8") != skill_text:
        skill.parent.mkdir(parents=True, exist_ok=True)
        skill.write_text(skill_text, encoding="utf-8")
        result.created.append(paths.rel(skill))

    if cfg.cluster_host and cfg.remote_repo:
        url = cfg.remote_url()
        if gitops.ensure_remote(root, cfg.git_remote, url):
            result.notes.append(f"added git remote {cfg.git_remote} -> {url}")
        else:
            current = gitops.remote_url(root, cfg.git_remote)
            if current != url:
                result.notes.append(f"git remote {cfg.git_remote} already exists ({current}); not changed")
    else:
        result.notes.append("cluster_host / remote_repo not set; `hypx run` needs them (edit .exps/config.yaml or rerun init)")

    if register:
        register_project(cfg, paths)
        result.notes.append(f"registered project {cfg.project} in ~/.config/hypotrack/projects.toml")

    result.cfg = cfg
    return result
