"""File CRUD for .exps/: hypotheses.yaml + hypotheses/H*.md, experiments.yaml + experiments/*/notes.md."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from . import ids
from .errors import HypxError
from .models import Experiment, Hypothesis, dump_yaml, load_yaml, now_iso, split_markdown_sections
from .paths import Paths


def _load_index(p: Path, paths: Paths) -> dict[str, dict[str, Any]]:
    """Read an index file ({id: metadata-without-id}); missing file means empty."""
    if not p.exists():
        return {}
    data = load_yaml(p.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise HypxError(f"{paths.rel(p)} must be a mapping of id -> metadata")
    return {str(k): dict(v or {}) for k, v in data.items()}


def _write_index(p: Path, index: dict[str, dict[str, Any]], key: Callable[[str], Any]) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    ordered = {k: index[k] for k in sorted(index, key=key)}
    p.write_text(dump_yaml(ordered), encoding="utf-8")
    return p


class Store:
    def __init__(self, paths: Paths) -> None:
        self.paths = paths

    # ------------------------------------------------------------------ hypotheses

    def _hyp_md_path(self, hyp_id: str) -> Path:
        """hypotheses/H1.md: the prose (statement, rationale, conclusion), editable by hand."""
        return self.paths.hypotheses / f"{hyp_id}.md"

    def _legacy_hyp_paths(self, hyp_id: str) -> list[Path]:
        """Per-hypothesis YAML layouts that predate hypotheses.yaml: hypotheses/H1.yaml, meta/H1.yaml."""
        return [self.paths.hypotheses / f"{hyp_id}.yaml", self.paths.exps / "meta" / f"{hyp_id}.yaml"]

    def _legacy_hyp_path(self, hyp_id: str) -> Path | None:
        return next((p for p in self._legacy_hyp_paths(hyp_id) if p.exists()), None)

    def _load_index(self) -> dict[str, dict[str, Any]]:
        """hypotheses.yaml as {id: metadata-without-id}."""
        return _load_index(self.paths.hyp_index, self.paths)

    def _write_index(self, index: dict[str, dict[str, Any]]) -> Path:
        return _write_index(self.paths.hyp_index, index, ids.hyp_sort_key)

    def hyp_files(self, hyp_id: str) -> list[Path]:
        return [self.paths.hyp_index, self._hyp_md_path(hyp_id)]

    def hypothesis_ids(self) -> list[str]:
        found = {h for h in self._load_index() if ids.HYP_RE.match(h)}
        for d in (self.paths.hypotheses, self.paths.exps / "meta"):
            if d.is_dir():
                found.update(p.stem for p in d.glob("H*.yaml") if ids.HYP_RE.match(p.stem))
        return sorted(found, key=ids.hyp_sort_key)

    def has_hypothesis(self, hyp_id: str) -> bool:
        return hyp_id in self._load_index() or self._legacy_hyp_path(hyp_id) is not None

    def get_hypothesis(self, hyp_id: str) -> Hypothesis:
        ids.validate_hyp_id(hyp_id)
        entry = self._load_index().get(hyp_id)
        if entry is not None:
            hyp = Hypothesis.from_dict({**entry, "id": hyp_id})
        else:
            legacy = self._legacy_hyp_path(hyp_id)
            if legacy is None:
                raise HypxError(f"hypothesis {hyp_id} not found ({self.paths.rel(self.paths.hyp_index)})")
            hyp = Hypothesis.from_yaml(legacy.read_text(encoding="utf-8"))
        md = self._hyp_md_path(hyp_id)
        if md.exists():
            # The Markdown file wins over any body fields left in a legacy single-file YAML.
            hyp.apply_markdown(md.read_text(encoding="utf-8"))
        return hyp

    def list_hypotheses(self) -> list[Hypothesis]:
        return [self.get_hypothesis(h) for h in self.hypothesis_ids()]

    def save_hypothesis(self, hyp: Hypothesis) -> Path:
        """Update the hyp's entry in hypotheses.yaml and write hypotheses/H1.md. Unknown sections already in H1.md are kept."""
        self.paths.hypotheses.mkdir(parents=True, exist_ok=True)
        md = self._hyp_md_path(hyp.id)
        existing = md.read_text(encoding="utf-8") if md.exists() else None
        md.write_text(hyp.to_markdown(existing), encoding="utf-8")
        index = self._load_index()
        index[hyp.id] = {k: v for k, v in hyp.to_meta_dict().items() if k != "id"}
        p = self._write_index(index)
        for legacy in self._legacy_hyp_paths(hyp.id):
            if legacy.exists():
                legacy.unlink()  # migrated from a per-file layout; removed only after both new files exist
        return p

    def children(self, hyp_id: str | None) -> list[Hypothesis]:
        return [self.get_hypothesis(h) for h in self.hypothesis_ids() if ids.is_child_of(h, hyp_id)]

    def is_leaf(self, hyp_id: str) -> bool:
        return not any(ids.is_child_of(h, hyp_id) for h in self.hypothesis_ids())

    def new_hypothesis(
        self,
        title: str,
        parent: str | None = None,
        statement: str = "",
        rationale: str = "",
    ) -> Hypothesis:
        if parent is not None:
            if not self.has_hypothesis(parent):
                raise HypxError(f"parent hypothesis {parent} does not exist")
            if self.experiments_for(parent):
                raise HypxError(
                    f"{parent} already has experiments attached; experiments must hang off leaf hypotheses. "
                    "Create the child under a different parent or move the experiments first."
                )
        hyp_id = ids.next_child_id(parent, self.hypothesis_ids())
        orphan = self._hyp_md_path(hyp_id)
        if orphan.exists():
            raise HypxError(
                f"{self.paths.rel(orphan)} exists but {hyp_id} is not in {self.paths.rel(self.paths.hyp_index)}; "
                "restore the entry or remove the orphaned Markdown before creating a new hypothesis"
            )
        hyp = Hypothesis(id=hyp_id, title=title, parent=parent, statement=statement, rationale=rationale)
        self.save_hypothesis(hyp)
        return hyp

    # ------------------------------------------------------------------ experiments

    def exp_dir(self, exp_id: str) -> Path:
        return self.paths.exp_dir(exp_id)

    def meta_path(self, exp_id: str) -> Path:
        """The file holding this experiment's metadata: experiments.yaml (shared by all experiments)."""
        return self.paths.exp_index

    def _legacy_meta_path(self, exp_id: str) -> Path:
        """Pre-index layout: experiments/exp-001/meta.yaml."""
        return self.exp_dir(exp_id) / "meta.yaml"

    def notes_path(self, exp_id: str) -> Path:
        return self.exp_dir(exp_id) / "notes.md"

    def results_dir(self, exp_id: str) -> Path:
        return self.exp_dir(exp_id) / "results"

    def submit_log_path(self, exp_id: str) -> Path:
        return self.exp_dir(exp_id) / "submit.log"

    def _load_exp_index(self) -> dict[str, dict[str, Any]]:
        """experiments.yaml as {id: metadata-without-id}."""
        return _load_index(self.paths.exp_index, self.paths)

    def _write_exp_index(self, index: dict[str, dict[str, Any]]) -> Path:
        return _write_index(self.paths.exp_index, index, ids.exp_number)

    def experiment_ids(self) -> list[str]:
        found = {e for e in self._load_exp_index() if ids.EXP_RE.match(e)}
        if self.paths.experiments.is_dir():
            found.update(
                p.name
                for p in self.paths.experiments.iterdir()
                if p.is_dir() and ids.EXP_RE.match(p.name) and (p / "meta.yaml").exists()
            )
        return sorted(found, key=ids.exp_number)

    def has_experiment(self, exp_id: str) -> bool:
        return exp_id in self._load_exp_index() or self._legacy_meta_path(exp_id).exists()

    def _experiment_from_entry(self, exp_id: str, index: dict[str, dict[str, Any]]) -> Experiment:
        entry = index.get(exp_id)
        if entry is not None:
            return Experiment.from_dict({**entry, "id": exp_id})
        legacy = self._legacy_meta_path(exp_id)
        if not legacy.exists():
            raise HypxError(f"experiment {exp_id} not found ({self.paths.rel(self.paths.exp_index)})")
        return Experiment.from_yaml(legacy.read_text(encoding="utf-8"))

    def get_experiment(self, exp_id: str) -> Experiment:
        ids.validate_exp_id(exp_id)
        return self._experiment_from_entry(exp_id, self._load_exp_index())

    def list_experiments(self) -> list[Experiment]:
        index = self._load_exp_index()
        return [self._experiment_from_entry(e, index) for e in self.experiment_ids()]

    def experiments_for(self, hyp_id: str) -> list[Experiment]:
        return [e for e in self.list_experiments() if e.hypothesis == hyp_id]

    def save_experiment(self, exp: Experiment) -> Path:
        """Update the experiment's entry in experiments.yaml (the exp dir is created for notes/results)."""
        self.exp_dir(exp.id).mkdir(parents=True, exist_ok=True)
        index = self._load_exp_index()
        index[exp.id] = {k: v for k, v in exp.to_dict().items() if k != "id"}
        p = self._write_exp_index(index)
        legacy = self._legacy_meta_path(exp.id)
        if legacy.exists():
            legacy.unlink()  # migrated from the per-directory layout; removed only after the index is written
        return p

    def new_experiment(self, hyp_id: str, title: str, intent: str = "") -> Experiment:
        if not self.has_hypothesis(hyp_id):
            raise HypxError(f"hypothesis {hyp_id} does not exist")
        if not self.is_leaf(hyp_id):
            raise HypxError(f"{hyp_id} has child hypotheses; attach the experiment to a leaf hypothesis")
        exp_id = ids.next_exp_id(self.experiment_ids())
        exp = Experiment(id=exp_id, hypothesis=hyp_id, title=title)
        self.save_experiment(exp)
        header = f"# {exp_id}: {title}\n\nhypothesis: {hyp_id}\n"
        self.notes_path(exp_id).write_text(header, encoding="utf-8")
        self.append_note(exp_id, intent.strip() or "(no intent recorded)", label="intent")
        return exp

    # ------------------------------------------------------------------ notes

    def read_notes(self, exp_id: str) -> str:
        p = self.notes_path(exp_id)
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def append_note(self, exp_id: str, text: str, when: datetime | None = None, label: str = "") -> str:
        if not self.has_experiment(exp_id):
            raise HypxError(f"experiment {exp_id} not found")
        stamp = (when or datetime.now()).strftime("%Y-%m-%d %H:%M")
        heading = f"## {stamp}" + (f" {label}" if label else "")
        section = f"\n{heading}\n\n{text.rstrip()}\n"
        p = self.notes_path(exp_id)
        existing = p.read_text(encoding="utf-8") if p.exists() else ""
        if existing and not existing.endswith("\n"):
            existing += "\n"
        p.write_text(existing + section, encoding="utf-8")
        return section

    def note_sections(self, exp_id: str) -> list[tuple[str, str]]:
        """Return (heading, body) pairs for each `## ` section in notes.md (fenced code is not a heading)."""
        return split_markdown_sections(self.read_notes(exp_id))[1]

    def has_discussion(self, exp_id: str) -> bool:
        """True when notes.md has a section beyond the initial intent."""
        sections = self.note_sections(exp_id)
        return any(not h.endswith("intent") for h, _ in sections)

    # ------------------------------------------------------------------ misc

    def touch_synced(self, exp: Experiment) -> None:
        exp.synced_at = now_iso()
        self.save_experiment(exp)
