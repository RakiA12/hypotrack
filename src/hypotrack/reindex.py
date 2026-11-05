"""Minimal SQLite index rebuilt from .exps/ (phase 1: rebuild only; nothing reads it yet)."""

from __future__ import annotations

import json
import sqlite3

from .paths import Paths
from .store import Store

SCHEMA = """
CREATE TABLE hypotheses (
    id TEXT PRIMARY KEY, parent TEXT, title TEXT, status TEXT, created TEXT, concluded TEXT,
    evidence TEXT, statement TEXT, rationale TEXT, conclusion TEXT
);
CREATE TABLE experiments (
    id TEXT PRIMARY KEY, hypothesis TEXT, title TEXT, status TEXT, created TEXT, synced_at TEXT,
    code_ref TEXT, code_tree TEXT, overrides TEXT, submit_command TEXT, output_root TEXT
);
CREATE TABLE runs (
    experiment TEXT, label TEXT, subdir TEXT, job_id TEXT, state TEXT, exit_code TEXT, elapsed TEXT,
    overrides TEXT, wandb_url TEXT,
    PRIMARY KEY (experiment, label)
);
CREATE TABLE metrics_last (
    experiment TEXT, run TEXT, metric TEXT, last REAL, last_step REAL, best REAL, best_step REAL,
    PRIMARY KEY (experiment, run, metric)
);
"""


def rebuild_index(store: Store, paths: Paths) -> dict[str, int]:
    tmp = paths.index.with_suffix(".sqlite.tmp")
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(tmp)
    counts = {"hypotheses": 0, "experiments": 0, "runs": 0, "metrics_last": 0}
    try:
        con.executescript(SCHEMA)
        for h in store.list_hypotheses():
            con.execute(
                "INSERT INTO hypotheses VALUES (?,?,?,?,?,?,?,?,?,?)",
                (h.id, h.parent, h.title, h.status, h.created, h.concluded, json.dumps(h.evidence), h.statement, h.rationale, h.conclusion),
            )
            counts["hypotheses"] += 1
        for e in store.list_experiments():
            con.execute(
                "INSERT INTO experiments VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    e.id, e.hypothesis, e.title, e.status, e.created, e.synced_at,
                    e.code.get("ref"), e.code.get("tree"), json.dumps(e.overrides),
                    e.submit.get("command"), e.output_root,
                ),
            )
            counts["experiments"] += 1
            for r in e.runs:
                con.execute(
                    "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?,?)",
                    (e.id, r.label, r.subdir, r.job_id, r.state, r.exit_code, r.elapsed, json.dumps(r.overrides), r.wandb_url),
                )
                counts["runs"] += 1
            for run_label, entry in ((e.summary or {}).get("runs") or {}).items():
                for metric, vals in entry.items():
                    con.execute(
                        "INSERT OR REPLACE INTO metrics_last VALUES (?,?,?,?,?,?,?)",
                        (e.id, run_label, metric, vals.get("last"), vals.get("last_step"), vals.get("best"), vals.get("best_step")),
                    )
                    counts["metrics_last"] += 1
        con.commit()
    finally:
        con.close()
    tmp.replace(paths.index)
    return counts
