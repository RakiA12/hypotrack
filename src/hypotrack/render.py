"""Markdown / JSON rendering for agent-facing output. No numeric interpretation here."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import ids, metrics
from .config import Config
from .errors import HypxError
from .models import Experiment, Hypothesis
from .overrides import override_diff, overrides_dict, varying_and_fixed
from .paths import Paths
from .store import Store
from .sync import local_log_tail


def md_table(headers: list[str], rows: list[list[str]]) -> str:
    def esc(s: Any) -> str:
        return str(s).replace("|", "\\|").replace("\n", " ")

    lines = ["| " + " | ".join(esc(h) for h in headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for r in rows:
        lines.append("| " + " | ".join(esc(c) for c in r) + " |")
    return "\n".join(lines)


def first_line(text: str) -> str:
    for ln in (text or "").splitlines():
        if ln.strip():
            return ln.strip()
    return ""


# ----------------------------------------------------------------------------- hypotheses


def hyp_tree_data(store: Store, status: str | None = None) -> list[dict[str, Any]]:
    hyps = store.list_hypotheses()
    exps = store.list_experiments()
    count: dict[str, int] = {}
    for e in exps:
        count[e.hypothesis] = count.get(e.hypothesis, 0) + 1
    keep: set[str] | None = None
    if status:
        matched = {h.id for h in hyps if h.status == status}
        keep = set()
        for m in matched:
            keep.update(ids.ancestors(m))
    out = []
    for h in hyps:
        if keep is not None and h.id not in keep:
            continue
        out.append(
            {
                "id": h.id,
                "parent": h.parent,
                "depth": len(ids.hyp_path(h.id)) - 1,
                "title": h.title,
                "status": h.status,
                "experiments": count.get(h.id, 0),
                "concluded": h.concluded,
                "conclusion": first_line(h.conclusion),
            }
        )
    return out


def hyp_tree_md(store: Store, status: str | None = None) -> str:
    rows = hyp_tree_data(store, status)
    if not rows:
        return "(no hypotheses)" if not status else f"(no hypotheses with status {status})"
    lines = []
    for r in rows:
        indent = "  " * r["depth"]
        n = r["experiments"]
        exps = f" ({n} exp{'s' if n != 1 else ''})" if n else ""
        concl = f" — {r['conclusion']}" if r["conclusion"] and r["status"] != "open" else ""
        lines.append(f"{indent}- {r['id']} [{r['status']}] {r['title']}{exps}{concl}")
    return "\n".join(lines)


def hyp_md(h: Hypothesis, exps: list[Experiment]) -> str:
    lines = [f"# {h.id}: {h.title}", "", f"- status: {h.status}", f"- parent: {h.parent or '(root)'}", f"- created: {h.created}"]
    if h.concluded:
        lines.append(f"- concluded: {h.concluded}")
    if h.evidence:
        lines.append(f"- evidence: {', '.join(h.evidence)}")
    for label, text in (("statement", h.statement), ("rationale", h.rationale), ("conclusion", h.conclusion)):
        if text.strip():
            lines += ["", f"## {label}", "", text.strip()]
    if exps:
        lines += ["", "## experiments", "", md_table(["id", "title", "status"], [[e.id, e.title, e.status] for e in exps])]
    return "\n".join(lines)


# ----------------------------------------------------------------------------- experiments list


def best_metric_line(exp: Experiment) -> str:
    bests = (exp.summary or {}).get("metrics") or {}
    if not bests:
        return ""
    parts = []
    for m, b in list(bests.items())[:2]:
        step = f" @{b['step']}" if b.get("step") is not None else ""
        parts.append(f"{m}={metrics.fmt_num(b.get('value'))}{step} ({b.get('run')})")
    return "; ".join(parts)


def exp_row(exp: Experiment) -> dict[str, Any]:
    return {
        "id": exp.id,
        "hypothesis": exp.hypothesis,
        "title": exp.title,
        "status": exp.status,
        "created": exp.created,
        "runs": len(exp.runs),
        "best": best_metric_line(exp),
        "synced_at": exp.synced_at,
    }


def list_md(exps: list[Experiment]) -> str:
    if not exps:
        return "(no experiments)"
    rows = [[e.id, e.hypothesis, e.title, e.status, str(len(e.runs)), best_metric_line(e)] for e in exps]
    return md_table(["id", "hyp", "title", "status", "runs", "best"], rows)


# ----------------------------------------------------------------------------- results


def results_table(
    store: Store,
    cfg: Config,
    paths: Paths,
    exp: Experiment,
    metric_names: list[str] | None = None,
    best_dir: str | None = None,
    last_only: bool = False,
    step: int | None = None,
    raw: bool = False,
) -> tuple[str, Any]:
    results_dir = store.results_dir(exp.id)
    if not results_dir.is_dir() or not any(r.subdir for r in exp.runs):
        raise HypxError(f"no pulled results for {exp.id}; run `hypx pull {exp.id}` first")
    step_key = cfg.metrics_step_key

    if raw:
        data = []
        for run in exp.runs:
            rd = metrics.run_results_dir(results_dir, run.subdir)
            files = metrics.metrics_files(rd, cfg.metrics_glob) if rd else []
            data.append({"run": run.label, "subdir": run.subdir, "files": [paths.rel(f) for f in files]})
        md = "\n".join(f"- {d['run']}: " + (", ".join(d["files"]) or "(no metrics file)") for d in data)
        return md, data

    per_run_records: dict[str, list[dict[str, Any]]] = {}
    for run in exp.runs:
        rd = metrics.run_results_dir(results_dir, run.subdir)
        per_run_records[run.label] = metrics.load_run_metrics(rd, cfg.metrics_glob) if rd else []

    names = list(metric_names or [])
    if not names:
        names = list(cfg.default_metrics)
    if not names:
        for recs in per_run_records.values():
            for n in metrics.metric_names(recs, step_key):
                if n not in names:
                    names.append(n)
        names = names[:4]
    primary = names[0] if names else None
    direction = best_dir or (cfg.direction_for(primary) if primary else "min")

    varying, fixed = varying_and_fixed([r.overrides for r in exp.runs])

    headers = ["run", *varying]
    for m in names:
        if step is not None:
            headers.append(f"{m} (step {step})")
        elif last_only:
            headers.append(f"{m} (last)")
        elif m == primary:
            headers += [f"{m} (best, step)", f"{m} (last)"]
        else:
            headers.append(f"{m} (last)")
    headers.append("state")

    rows: list[list[str]] = []
    data: list[dict[str, Any]] = []
    tails: list[tuple[str, list[str]]] = []
    best_run: tuple[str, Any, Any] | None = None  # (label, value, step)
    for run in exp.runs:
        records = per_run_records.get(run.label, [])
        od = overrides_dict(run.overrides)
        row = [run.label, *[od.get(k, "") for k in varying]]
        mvals: dict[str, Any] = {}
        for m in names:
            entry: dict[str, Any] = {}
            if step is not None:
                v = metrics.at_step(records, m, step, step_key)
                row.append(metrics.fmt_num(v))
                entry["at_step"] = v
            else:
                lv = metrics.last(records, m, step_key)
                if not last_only and m == primary:
                    bv = metrics.best(records, m, direction, step_key)
                    if bv is None:
                        row.append("")
                    else:
                        row.append(f"{metrics.fmt_num(bv[0])}" + (f" (step {bv[1]})" if bv[1] is not None else ""))
                        entry["best"], entry["best_step"] = bv
                        if best_run is None or (bv[0] < best_run[1] if direction == "min" else bv[0] > best_run[1]):
                            best_run = (run.label, bv[0], bv[1])
                row.append(metrics.fmt_num(lv[0]) if lv else "")
                if lv:
                    entry["last"], entry["last_step"] = lv
            mvals[m] = entry
        state = run.state or "?"
        if run.is_failed:
            state = f"{run.state} (exit {run.exit_code or '?'})"
            tail = local_log_tail(results_dir, cfg, exp, run)
            if tail:
                tails.append((run.label, tail))
        row.append(state)
        rows.append(row)
        data.append(
            {
                "run": run.label,
                "subdir": run.subdir,
                "job_id": run.job_id,
                "state": run.state,
                "exit_code": run.exit_code,
                "overrides": od,
                "metrics": mvals,
                "wandb_url": run.wandb_url,
            }
        )

    synced = f"  synced: {exp.synced_at}" if exp.synced_at else ""
    lines = [f"# {exp.id}: {exp.title}  ({exp.hypothesis})  status: {exp.status}{synced}", ""]
    if fixed:
        lines += ["fixed: " + ", ".join(f"{k}={v}" for k, v in fixed.items()), ""]
    lines.append(md_table(headers, rows))
    if best_run is not None and primary:
        label, value, bstep = best_run
        run = next((r for r in exp.runs if r.label == label), None)
        ov = ", ".join(f"{k}={overrides_dict(run.overrides).get(k, '')}" for k in varying) if run and varying else ""
        ov = f" ({ov})" if ov else ""
        lines += ["", f"best: run {label}{ov} by {primary} {direction}"]
    for label, tail in tails:
        lines += ["", f"log tail (run {label}):", "```", *tail, "```"]
    meta: dict[str, Any] = {
        "experiment": exp.id,
        "hypothesis": exp.hypothesis,
        "status": exp.status,
        "fixed_overrides": fixed,
        "varying_overrides": varying,
        "metrics": names,
        "best": {"run": best_run[0], "value": best_run[1], "step": best_run[2], "metric": primary, "direction": direction}
        if best_run
        else None,
        "runs": data,
    }
    return "\n".join(lines), meta


# ----------------------------------------------------------------------------- show


def show_md(store: Store, cfg: Config, paths: Paths, exp: Experiment, hyp: Hypothesis | None) -> tuple[str, Any]:
    lines = [f"# {exp.id}: {exp.title}", ""]
    hyp_title = f" — {hyp.title} [{hyp.status}]" if hyp else ""
    lines.append(f"- hypothesis: {exp.hypothesis}{hyp_title}")
    lines.append(f"- status: {exp.status}" + (f" (synced {exp.synced_at})" if exp.synced_at else ""))
    lines.append(f"- created: {exp.created}")
    if exp.code:
        c = exp.code
        lines.append(f"- code: {c.get('ref', '')} (base {c.get('base', '?')}, branch {c.get('branch') or '-'})")
    if exp.overrides:
        lines.append("- overrides: " + " ".join(exp.overrides))
    if exp.submit:
        lines.append(f"- submit: `{exp.submit.get('command', '')}` at {exp.submit.get('submitted_at', '?')}")
        lines.append(f"- cwd: {exp.submit.get('cwd', '')}")
    if exp.output_root:
        lines.append(f"- output: {exp.output_root}")

    if exp.runs:
        rows = [
            [r.label, r.job_id or "", r.state or "", r.exit_code or "", " ".join(r.overrides), r.wandb_url or ""]
            for r in exp.runs
        ]
        lines += ["", "## runs", "", md_table(["run", "job", "state", "exit", "overrides", "wandb"], rows)]

    bests = (exp.summary or {}).get("metrics") or {}
    if bests:
        rows = [
            [m, b.get("direction", ""), str(b.get("run", "")), metrics.fmt_num(b.get("value")), str(b.get("step", ""))]
            for m, b in bests.items()
        ]
        lines += ["", "## results summary", "", md_table(["metric", "dir", "best run", "value", "step"], rows)]

    notes = store.read_notes(exp.id).strip()
    if notes:
        lines += ["", "## notes", "", notes]

    lines += [
        "",
        "## files",
        "",
        f"- meta: {paths.rel(store.meta_path(exp.id))}",
        f"- notes: {paths.rel(store.notes_path(exp.id))}",
        f"- results: {paths.rel(store.results_dir(exp.id))}/",
    ]
    data = exp.to_dict()
    data["notes"] = notes
    data["files"] = {
        "meta": paths.rel(store.meta_path(exp.id)),
        "notes": paths.rel(store.notes_path(exp.id)),
        "results": paths.rel(store.results_dir(exp.id)),
    }
    return "\n".join(lines), data


# ----------------------------------------------------------------------------- diff


def diff_md(a: Experiment, b: Experiment, code_diff: str, stat: bool) -> tuple[str, Any]:
    od = override_diff(a.overrides, b.overrides)
    lines = [f"# diff {a.id} → {b.id}", ""]
    if od:
        rows = [[k, va if va is not None else "—", vb if vb is not None else "—"] for k, va, vb in od]
        lines += ["## overrides", "", md_table(["key", a.id, b.id], rows), ""]
    else:
        lines += ["## overrides", "", "(identical)", ""]
    lines += ["## code", ""]
    if code_diff.strip():
        lines += ["```diff" if not stat else "```", code_diff.rstrip("\n"), "```"]
    else:
        lines.append("(identical tree)")
    data = {
        "a": a.id,
        "b": b.id,
        "overrides": [{"key": k, a.id: va, b.id: vb} for k, va, vb in od],
        "code_diff": code_diff,
    }
    return "\n".join(lines), data


def rel_path(paths: Paths, p: Path) -> str:
    return paths.rel(p)
