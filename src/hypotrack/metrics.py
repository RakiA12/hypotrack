"""metrics.jsonl reading and last / best / at-step summaries. No interpretation: numbers only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import Config
from .models import Experiment

Number = int | float


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    records.append(obj)
    except OSError:
        return []
    return records


def metrics_files(run_dir: Path, pattern: str) -> list[Path]:
    if not run_dir.is_dir():
        return []
    if "/" in pattern or "**" in pattern:
        return sorted(run_dir.glob(pattern))
    return sorted(run_dir.rglob(pattern))


def load_run_metrics(run_dir: Path, pattern: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for f in metrics_files(run_dir, pattern):
        records.extend(read_jsonl(f))
    return records


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def metric_names(records: list[dict[str, Any]], step_key: str) -> list[str]:
    names: list[str] = []
    for r in records:
        for k, v in r.items():
            if k != step_key and _is_number(v) and k not in names:
                names.append(k)
    return names


def _step_of(r: dict[str, Any], step_key: str) -> Number | None:
    v = r.get(step_key)
    return v if _is_number(v) else None


def last(records: list[dict[str, Any]], metric: str, step_key: str) -> tuple[Number, Number | None] | None:
    for r in reversed(records):
        if _is_number(r.get(metric)):
            return r[metric], _step_of(r, step_key)
    return None


def best(
    records: list[dict[str, Any]], metric: str, direction: str, step_key: str
) -> tuple[Number, Number | None] | None:
    pick = min if direction == "min" else max
    cands = [(r[metric], _step_of(r, step_key)) for r in records if _is_number(r.get(metric))]
    if not cands:
        return None
    return pick(cands, key=lambda t: t[0])


def at_step(records: list[dict[str, Any]], metric: str, step: Number, step_key: str) -> Number | None:
    exact = [r for r in records if _step_of(r, step_key) == step and _is_number(r.get(metric))]
    if exact:
        return exact[-1][metric]
    # nearest step at or before the requested one
    before = [r for r in records if (_step_of(r, step_key) or -1) <= step and _is_number(r.get(metric))]
    return before[-1][metric] if before else None


def fmt_num(v: Number | None) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return str(v)
    if abs(v) >= 1000 or (abs(v) < 1e-3 and v != 0):
        return f"{v:.4g}"
    return f"{v:.4f}".rstrip("0").rstrip(".") if "." in f"{v:.4f}" else f"{v:.4f}"


def run_results_dir(results_dir: Path, subdir: str | None) -> Path | None:
    if not subdir:
        return None
    return results_dir / subdir


def summarize_experiment(exp: Experiment, results_dir: Path, cfg: Config) -> dict[str, Any]:
    """Build meta.yaml `summary`: per-run last/best per metric and the experiment-level best per metric."""
    step_key = cfg.metrics_step_key
    per_run: dict[str, dict[str, Any]] = {}
    per_run_records: dict[str, list[dict[str, Any]]] = {}
    for run in exp.runs:
        rd = run_results_dir(results_dir, run.subdir)
        if rd is None:
            continue
        records = load_run_metrics(rd, cfg.metrics_glob)
        if records:
            per_run_records[run.label] = records

    names: list[str] = list(cfg.default_metrics)
    if not names:
        for recs in per_run_records.values():
            for n in metric_names(recs, step_key):
                if n not in names:
                    names.append(n)
        names = names[:10]

    bests: dict[str, Any] = {}
    for label, records in per_run_records.items():
        entry: dict[str, Any] = {}
        for m in names:
            direction = cfg.direction_for(m)
            lv = last(records, m, step_key)
            bv = best(records, m, direction, step_key)
            if lv is None and bv is None:
                continue
            e: dict[str, Any] = {}
            if lv is not None:
                e["last"] = lv[0]
                if lv[1] is not None:
                    e["last_step"] = lv[1]
            if bv is not None:
                e["best"] = bv[0]
                if bv[1] is not None:
                    e["best_step"] = bv[1]
                cur = bests.get(m)
                better = cur is None or (bv[0] < cur["value"] if direction == "min" else bv[0] > cur["value"])
                if better:
                    bests[m] = {"run": label, "value": bv[0], "step": bv[1], "direction": direction}
            entry[m] = e
        if entry:
            per_run[label] = entry

    return {"metrics": bests, "runs": per_run}
