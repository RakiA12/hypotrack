"""Typer CLI. Every command accepts --json; default output is Markdown for agents."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import typer

from . import __version__, hlog
from .config import Config
from .errors import HypxError, NotImplementedPhase2
from .models import EXP_STATUSES, HYP_STATUSES, today_iso
from .paths import Paths, require_paths
from .store import Store

app = typer.Typer(
    name="hypx",
    help="Context management for hypothesis-driven ML experiments (hypothesis -> experiment -> result).",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
    rich_markup_mode=None,
)
hyp_app = typer.Typer(help="Hypotheses: new / tree / conclude / show", no_args_is_help=True)
exp_app = typer.Typer(help="Experiments: new", no_args_is_help=True)
app.add_typer(hyp_app, name="hyp")
app.add_typer(exp_app, name="exp")

JsonOpt = typer.Option(False, "--json", help="Machine-readable JSON instead of Markdown.")
BodyOpt = typer.Option(None, "--body", "-b", help="Free text. Omit to read it from stdin.")


# ----------------------------------------------------------------------------- helpers


class Ctx:
    def __init__(self, paths: Paths, cfg: Config, store: Store) -> None:
        self.paths, self.cfg, self.store = paths, cfg, store


def load_ctx(need_config: bool = True) -> Ctx:
    paths = require_paths()
    hlog.configure(paths.log, verbose=hlog._verbose)
    cfg = Config.load(paths) if need_config else Config()
    return Ctx(paths, cfg, Store(paths))


def emit(md: str, data: Any, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    else:
        typer.echo(md)


def _stdin_has_data(timeout: float = 0.2) -> bool:
    """True when stdin is a pipe/file with data ready. Never blocks on an idle, non-TTY stdin (agents)."""
    try:
        if sys.stdin is None or sys.stdin.closed or sys.stdin.isatty():
            return False
        import select

        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        return bool(ready)
    except (OSError, ValueError):
        return False


def read_body(body: str | None, required: bool = False, what: str = "body") -> str:
    """--body TEXT, or --body - to read stdin, or omitted: stdin only if something is already piped in."""
    if body is not None and body != "-":
        return body
    if body == "-" or _stdin_has_data():
        text = sys.stdin.read()
        if text.strip() or not required:
            return text
    if required:
        raise HypxError(f"{what} is required: pass --body TEXT, or pipe text and pass --body -")
    return ""


def warn(msg: str) -> None:
    typer.echo(f"warning: {msg}", err=True)


def _remote(cfg: Config):
    from .remote import Remote

    if not cfg.cluster_host:
        raise HypxError("cluster_host is not set in .exps/config.yaml")
    return Remote(cfg.cluster_host)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"hypotrack {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Echo every git/ssh command to stderr."),
    version: bool = typer.Option(False, "--version", callback=_version, is_eager=True, help="Show version."),
) -> None:
    hlog.set_verbose(verbose)


# ----------------------------------------------------------------------------- init


@app.command()
def init(
    cluster_host: str | None = typer.Option(None, "--cluster-host", help="ssh host alias of the Slurm cluster."),
    remote_repo: str | None = typer.Option(None, "--remote-repo", help="Path of this repository's clone on the cluster, e.g. ~/proj/repo."),
    work_root: str | None = typer.Option(None, "--work-root", help="Cluster dir for per-experiment worktrees (default: <remote parent>/.exp-work)."),
    output_root: str | None = typer.Option(None, "--output-root", help="Cluster dir where Hydra writes outputs/<exp-id> (default: <remote parent>/outputs)."),
    name: str | None = typer.Option(None, "--name", help="Project name for ~/.config/hypotrack/projects.toml (default: repo dir name)."),
    metric: list[str] = typer.Option([], "--metric", help="Default metric(s) for results/pull summaries. Repeatable."),
    claude_md: bool = typer.Option(False, "--claude-md", help="Print the CLAUDE.md snippet and exit."),
    no_register: bool = typer.Option(False, "--no-register", help="Do not touch ~/.config/hypotrack/projects.toml."),
    as_json: bool = JsonOpt,
) -> None:
    """Create .exps/ and config.yaml here; generate the Claude Code skill; add the cluster git remote."""
    from .initcmd import claude_md_snippet, init_project

    if claude_md:
        typer.echo(claude_md_snippet().rstrip("\n"))
        return
    res = init_project(
        Path.cwd(),
        cluster_host=cluster_host,
        remote_repo=remote_repo,
        work_root=work_root,
        output_root=output_root,
        name=name,
        default_metrics=metric or None,
        register=not no_register,
    )
    hlog.configure(res.paths.log, verbose=hlog._verbose)
    hlog.log("hypx init")
    data = {"root": str(res.paths.root), "created": res.created, "notes": res.notes, "config": res.cfg.__dict__}
    lines = [f"# hypotrack initialized at {res.paths.root}", ""]
    lines += [f"- created: {c}" for c in res.created] or ["- nothing new to create"]
    lines += [f"- {n}" for n in res.notes]
    lines += ["", "next: `hypx init --claude-md >> CLAUDE.md`, then `hypx hyp new --title ...`"]
    emit("\n".join(lines), data, as_json)


# ----------------------------------------------------------------------------- hyp


@hyp_app.command("new")
def hyp_new(
    title: str = typer.Option(..., "--title", "-t"),
    parent: str | None = typer.Option(None, "--parent", "-p", help="Parent hypothesis id (omit for a new root)."),
    body: str | None = BodyOpt,
    rationale: str = typer.Option("", "--rationale", help="Background / why this is worth testing."),
    as_json: bool = JsonOpt,
) -> None:
    """Create a hypothesis under --parent (or a new root). Statement comes from --body or stdin."""
    c = load_ctx()
    statement = read_body(body).strip()
    hyp = c.store.new_hypothesis(title, parent=parent, statement=statement, rationale=rationale.strip())
    path = c.paths.rel(c.store._hyp_path(hyp.id))
    emit(f"created {hyp.id}: {hyp.title}\n- file: {path}", {"id": hyp.id, "title": hyp.title, "parent": hyp.parent, "file": path}, as_json)


@hyp_app.command("tree")
def hyp_tree(
    status: str | None = typer.Option(None, "--status", help="Show only hypotheses with this status (ancestors kept)."),
    as_json: bool = JsonOpt,
) -> None:
    """Hypothesis tree with status and attached experiment counts."""
    from .render import hyp_tree_data, hyp_tree_md

    c = load_ctx()
    if status and status not in HYP_STATUSES:
        raise HypxError(f"unknown status {status!r}; one of {', '.join(HYP_STATUSES)}")
    emit(hyp_tree_md(c.store, status), hyp_tree_data(c.store, status), as_json)


@hyp_app.command("show")
def hyp_show(hyp_id: str = typer.Argument(...), as_json: bool = JsonOpt) -> None:
    """One hypothesis with its statement, conclusion and experiments."""
    from .render import hyp_md

    c = load_ctx()
    h = c.store.get_hypothesis(hyp_id)
    exps = c.store.experiments_for(hyp_id)
    data = h.to_dict()
    data["experiments"] = [e.id for e in exps]
    emit(hyp_md(h, exps), data, as_json)


@hyp_app.command("conclude")
def hyp_conclude(
    hyp_id: str = typer.Argument(...),
    status: str = typer.Option(..., "--status", "-s", help="supported | refuted | inconclusive | abandoned | open"),
    evidence: str = typer.Option("", "--evidence", "-e", help="Comma-separated experiment ids."),
    body: str | None = BodyOpt,
    as_json: bool = JsonOpt,
) -> None:
    """Record the verdict on a hypothesis. Idempotent: re-running overwrites the verdict."""
    c = load_ctx()
    if status not in HYP_STATUSES:
        raise HypxError(f"unknown status {status!r}; one of {', '.join(HYP_STATUSES)}")
    h = c.store.get_hypothesis(hyp_id)
    ev = [e.strip() for e in evidence.split(",") if e.strip()]
    for e in ev:
        if not c.store.has_experiment(e):
            raise HypxError(f"evidence {e} is not an experiment in this project")
    conclusion = read_body(body).strip()
    h.status = status
    h.evidence = ev
    if conclusion:
        h.conclusion = conclusion
    h.concluded = None if status == "open" else today_iso()
    c.store.save_hypothesis(h)
    emit(f"{h.id} -> {h.status}" + (f" (evidence: {', '.join(ev)})" if ev else ""), h.to_dict(), as_json)


# ----------------------------------------------------------------------------- exp


@exp_app.command("new")
def exp_new(
    hyp: str = typer.Option(..., "--hyp", "-H", help="Leaf hypothesis this experiment tests."),
    title: str = typer.Option(..., "--title", "-t"),
    body: str | None = BodyOpt,
    as_json: bool = JsonOpt,
) -> None:
    """Create a draft experiment. The intent (--body or stdin) becomes the first notes.md section."""
    c = load_ctx()
    intent = read_body(body, required=True, what="intent")
    exp = c.store.new_experiment(hyp, title, intent)
    data = {
        "id": exp.id,
        "hypothesis": exp.hypothesis,
        "title": exp.title,
        "status": exp.status,
        "meta": c.paths.rel(c.store.meta_path(exp.id)),
        "notes": c.paths.rel(c.store.notes_path(exp.id)),
    }
    emit(f"created {exp.id}: {exp.title} (hypothesis {exp.hypothesis}, status draft)\n- notes: {data['notes']}", data, as_json)


# ----------------------------------------------------------------------------- run / status / pull / cancel


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def run(
    ctx: typer.Context,
    exp_id: str = typer.Argument(..., help="Draft experiment to submit."),
    resubmit: bool = typer.Option(False, "--resubmit", help="Allow submitting a non-draft experiment again."),
    force: bool = typer.Option(False, "--force", help="Submit even if code and overrides equal an earlier experiment."),
    as_json: bool = JsonOpt,
) -> None:
    """Shadow-commit the working tree, push it to the cluster, and run the submit command after `--`."""
    from .runner import run_experiment

    command = list(ctx.args)
    if command and command[0] == "--":
        command = command[1:]
    c = load_ctx()
    res = run_experiment(c.store, c.cfg, c.paths, exp_id, command, resubmit=resubmit, force=force)
    for w in res.warnings:
        warn(w)
    exp = res.exp
    lines = [
        f"submitted {exp.id}: {exp.title}",
        f"- jobs: {', '.join(res.job_ids)}",
        f"- code: {res.shadow.ref} -> {res.shadow.commit[:8]}" + ("" if res.shadow.created_new else " (clean tree, = HEAD)"),
        f"- cwd: {exp.submit.get('cwd')}",
        f"- log: {c.paths.rel(c.store.submit_log_path(exp.id))}",
    ]
    data = {
        "id": exp.id,
        "status": exp.status,
        "job_ids": res.job_ids,
        "code": exp.code,
        "submit": exp.submit,
        "warnings": res.warnings,
        "committed": res.committed,
    }
    emit("\n".join(lines), data, as_json)


@app.command()
def status(
    exp_id: str | None = typer.Argument(None, help="Experiment id; default: every submitted/running experiment."),
    as_json: bool = JsonOpt,
) -> None:
    """Sync run states from sacct and derive each experiment's status."""
    from .render import md_table
    from .sync import sync_status

    c = load_ctx()
    changes = sync_status(c.store, c.cfg, _remote(c.cfg), [exp_id] if exp_id else None)
    if not changes:
        emit("(no submitted or running experiments)", [], as_json)
        return
    rows = []
    data = []
    for ch in changes:
        states = ", ".join(f"{r.label}:{r.state or '?'}" for r in ch.exp.runs) or "-"
        arrow = ch.after if ch.before == ch.after else f"{ch.before} -> {ch.after}"
        rows.append([ch.exp.id, ch.exp.title, arrow, states])
        data.append({"id": ch.exp.id, "before": ch.before, "after": ch.after, "runs": [r.to_dict() for r in ch.exp.runs]})
    emit(md_table(["id", "title", "status", "runs"], rows), data, as_json)


@app.command()
def pull(
    exp_id: str | None = typer.Argument(None, help="Experiment id; default: running experiments and finished ones never pulled."),
    include_logs: bool = typer.Option(False, "--include-logs", help="Pull Slurm logs for every run, not only failed ones."),
    tail: int | None = typer.Option(None, "--tail", help="Fetch only the last N lines of each log."),
    all_completed: bool = typer.Option(False, "--all-completed", help="Pull every finished experiment."),
    as_json: bool = JsonOpt,
) -> None:
    """rsync .hydra/, metrics and (failed) Slurm logs back into experiments/<id>/results/."""
    from .sync import pull_experiment

    c = load_ctx()
    if exp_id:
        targets = [c.store.get_experiment(exp_id)]
    elif all_completed:
        targets = [e for e in c.store.list_experiments() if e.status in ("completed", "partial", "failed")]
    else:
        targets = [
            e
            for e in c.store.list_experiments()
            if e.status == "running" or (e.status in ("completed", "partial", "failed") and not e.synced_at)
        ]
    if not targets:
        emit("(nothing to pull)", [], as_json)
        return
    remote = _remote(c.cfg)
    lines, data = [], []
    for exp in targets:
        rep = pull_experiment(c.store, c.cfg, c.paths, remote, exp, include_logs=include_logs, tail=tail)
        for w in rep.warnings:
            warn(f"{exp.id}: {w}")
        best = ", ".join(
            f"{m}={b.get('value')} ({b.get('run')})" for m, b in ((exp.summary or {}).get("metrics") or {}).items()
        )
        lines.append(
            f"- {exp.id} [{exp.status}]: {len(rep.run_dirs)} run dir(s) -> {c.paths.rel(c.store.results_dir(exp.id))}/"
            + (f"; logs: {len(rep.pulled_logs)}" if rep.pulled_logs else "")
            + (f"; best: {best}" if best else "")
        )
        data.append(
            {
                "id": exp.id,
                "status": exp.status,
                "run_dirs": rep.run_dirs,
                "logs": rep.pulled_logs,
                "summary": exp.summary,
                "results": c.paths.rel(c.store.results_dir(exp.id)),
                "warnings": rep.warnings,
            }
        )
    emit("\n".join(lines), data, as_json)


@app.command()
def cancel(exp_id: str = typer.Argument(...), as_json: bool = JsonOpt) -> None:
    """scancel every job of the experiment and mark it cancelled."""
    from .sync import cancel_experiment

    c = load_ctx()
    exp = cancel_experiment(c.store, c.cfg, _remote(c.cfg), c.store.get_experiment(exp_id))
    emit(f"cancelled {exp.id} (jobs: {', '.join(exp.job_ids) or '-'})", {"id": exp.id, "status": exp.status, "job_ids": exp.job_ids}, as_json)


# ----------------------------------------------------------------------------- results / show / context / note / diff / list


@app.command()
def results(
    exp_id: str = typer.Argument(...),
    metric: list[str] = typer.Option([], "--metric", "-m", help="Metric column(s); first one gets the best column. Repeatable."),
    best: str | None = typer.Option(None, "--best", help="min | max for the primary metric (default from config / name)."),
    last: bool = typer.Option(False, "--last", help="Only last values, no best column."),
    at_step: int | None = typer.Option(None, "--at-step", help="Value at (or just before) this step instead of last/best."),
    raw: bool = typer.Option(False, "--raw", help="Print the metrics.jsonl paths per run instead of a table."),
    as_json: bool = JsonOpt,
) -> None:
    """Run x metric table from pulled metrics. Columns: varying overrides + requested metrics."""
    from .render import results_table

    if best is not None and best not in ("min", "max"):
        raise HypxError("--best must be min or max")
    c = load_ctx()
    exp = c.store.get_experiment(exp_id)
    md, data = results_table(c.store, c.cfg, c.paths, exp, metric or None, best, last, at_step, raw)
    emit(md, data, as_json)


@app.command()
def show(exp_id: str = typer.Argument(...), as_json: bool = JsonOpt) -> None:
    """Everything about one experiment: meta, runs, results summary, notes, wandb URLs, file paths."""
    from .render import show_md

    c = load_ctx()
    exp = c.store.get_experiment(exp_id)
    hyp = c.store.get_hypothesis(exp.hypothesis) if c.store.has_hypothesis(exp.hypothesis) else None
    md, data = show_md(c.store, c.cfg, c.paths, exp, hyp)
    emit(md, data, as_json)


@app.command()
def context(
    hyp: str | None = typer.Option(None, "--hyp", "-H", help="Focus hypothesis (default: whole project)."),
    recent: int = typer.Option(5, "--recent", help="How many recent experiments to expand."),
    budget: int | None = typer.Option(None, "--budget", help="Approximate token budget; sections are trimmed to fit."),
    as_json: bool = JsonOpt,
) -> None:
    """One Markdown package for an agent: hypothesis path, related experiments, recent notes, open items."""
    from .context import build_context

    c = load_ctx()
    md, data = build_context(c.store, c.cfg, c.paths, hyp, recent=recent, budget=budget)
    emit(md, data, as_json)


@app.command()
def note(
    exp_id: str = typer.Argument(...),
    body: str | None = BodyOpt,
    as_json: bool = JsonOpt,
) -> None:
    """Append a timestamped observation to experiments/<id>/notes.md."""
    c = load_ctx()
    text = read_body(body, required=True, what="note text")
    section = c.store.append_note(exp_id, text)
    path = c.paths.rel(c.store.notes_path(exp_id))
    emit(f"appended to {path}:{section}", {"id": exp_id, "file": path, "section": section}, as_json)


@app.command()
def diff(
    exp_a: str = typer.Argument(...),
    exp_b: str = typer.Argument(...),
    stat: bool = typer.Option(False, "--stat", help="git diff --stat instead of the full patch."),
    as_json: bool = JsonOpt,
) -> None:
    """Code (refs/exps/A..B) and override differences between two experiments."""
    from . import gitops
    from .render import diff_md

    c = load_ctx()
    a, b = c.store.get_experiment(exp_a), c.store.get_experiment(exp_b)
    code = gitops.diff_refs(c.paths.root, gitops.exp_ref(a.id), gitops.exp_ref(b.id), stat=stat)
    md, data = diff_md(a, b, code, stat)
    emit(md, data, as_json)


@app.command("list")
def list_cmd(
    hyp: str | None = typer.Option(None, "--hyp", "-H", help="Only experiments under this hypothesis (subtree)."),
    status: str | None = typer.Option(None, "--status", "-s"),
    all_projects: bool = typer.Option(False, "--all-projects", help="(phase 2) across every registered project."),
    as_json: bool = JsonOpt,
) -> None:
    """Experiments table."""
    from .render import exp_row, list_md

    if all_projects:
        raise NotImplementedPhase2("hypx list --all-projects")
    if status and status not in EXP_STATUSES:
        raise HypxError(f"unknown status {status!r}; one of {', '.join(EXP_STATUSES)}")
    c = load_ctx()
    exps = c.store.list_experiments()
    if hyp:
        exps = [e for e in exps if e.hypothesis == hyp or e.hypothesis.startswith(hyp + ".")]
    if status:
        exps = [e for e in exps if e.status == status]
    emit(list_md(exps), [exp_row(e) for e in exps], as_json)


@app.command()
def reindex(as_json: bool = JsonOpt) -> None:
    """Rebuild .exps/index.sqlite from the YAML files."""
    from .reindex import rebuild_index

    c = load_ctx()
    counts = rebuild_index(c.store, c.paths)
    emit(
        f"rebuilt {c.paths.rel(c.paths.index)}: " + ", ".join(f"{k}={v}" for k, v in counts.items()),
        {"index": c.paths.rel(c.paths.index), **counts},
        as_json,
    )


@app.command()
def gc(older_than: str = typer.Option("30d", "--older-than")) -> None:
    """(phase 2) Remove cluster worktrees of completed experiments."""
    raise NotImplementedPhase2("hypx gc")


@app.command()
def serve(port: int = typer.Option(8787, "--port")) -> None:
    """(phase 2) Human-facing web UI."""
    raise NotImplementedPhase2("hypx serve")


# ----------------------------------------------------------------------------- entry point


def main(argv: list[str] | None = None) -> int:
    """Console entry point: HypxError -> `error: ...` on stderr + its exit code; usage errors come from typer."""
    try:
        app(args=argv)
    except HypxError as exc:
        typer.echo(f"error: {exc}", err=True)
        return exc.exit_code
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        return code if isinstance(code, int) else 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
