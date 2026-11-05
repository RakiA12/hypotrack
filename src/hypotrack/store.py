"""File CRUD for .exps/: hypotheses/*.yaml, experiments/*/meta.yaml, notes.md."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from . import ids
from .errors import HypxError
from .models import Experiment, Hypothesis, now_iso
from .paths import Paths


class Store:
    def __init__(self, paths: Paths) -> None:
        self.paths = paths

    # ------------------------------------------------------------------ hypotheses

    def _hyp_path(self, hyp_id: str) -> Path:
        return self.paths.hypotheses / f"{hyp_id}.yaml"

    def hypothesis_ids(self) -> list[str]:
        if not self.paths.hypotheses.is_dir():
            return []
        found = [p.stem for p in self.paths.hypotheses.glob("H*.yaml") if ids.HYP_RE.match(p.stem)]
        return sorted(found, key=ids.hyp_sort_key)

    def has_hypothesis(self, hyp_id: str) -> bool:
        return self._hyp_path(hyp_id).exists()

    def get_hypothesis(self, hyp_id: str) -> Hypothesis:
        ids.validate_hyp_id(hyp_id)
        p = self._hyp_path(hyp_id)
        if not p.exists():
            raise HypxError(f"hypothesis {hyp_id} not found ({self.paths.rel(p)})")
        return Hypothesis.from_yaml(p.read_text(encoding="utf-8"))

    def list_hypotheses(self) -> list[Hypothesis]:
        return [self.get_hypothesis(h) for h in self.hypothesis_ids()]

    def save_hypothesis(self, hyp: Hypothesis) -> Path:
        self.paths.hypotheses.mkdir(parents=True, exist_ok=True)
        p = self._hyp_path(hyp.id)
        p.write_text(hyp.to_yaml(), encoding="utf-8")
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
        hyp = Hypothesis(id=hyp_id, title=title, parent=parent, statement=statement, rationale=rationale)
        self.save_hypothesis(hyp)
        return hyp

    # ------------------------------------------------------------------ experiments

    def exp_dir(self, exp_id: str) -> Path:
        return self.paths.exp_dir(exp_id)

    def meta_path(self, exp_id: str) -> Path:
        return self.exp_dir(exp_id) / "meta.yaml"

    def notes_path(self, exp_id: str) -> Path:
        return self.exp_dir(exp_id) / "notes.md"

    def results_dir(self, exp_id: str) -> Path:
        return self.exp_dir(exp_id) / "results"

    def submit_log_path(self, exp_id: str) -> Path:
        return self.exp_dir(exp_id) / "submit.log"

    def experiment_ids(self) -> list[str]:
        if not self.paths.experiments.is_dir():
            return []
        found = [
            p.name
            for p in self.paths.experiments.iterdir()
            if p.is_dir() and ids.EXP_RE.match(p.name) and (p / "meta.yaml").exists()
        ]
        return sorted(found, key=ids.exp_number)

    def has_experiment(self, exp_id: str) -> bool:
        return self.meta_path(exp_id).exists()

    def get_experiment(self, exp_id: str) -> Experiment:
        ids.validate_exp_id(exp_id)
        p = self.meta_path(exp_id)
        if not p.exists():
            raise HypxError(f"experiment {exp_id} not found ({self.paths.rel(p)})")
        return Experiment.from_yaml(p.read_text(encoding="utf-8"))

    def list_experiments(self) -> list[Experiment]:
        return [self.get_experiment(e) for e in self.experiment_ids()]

    def experiments_for(self, hyp_id: str) -> list[Experiment]:
        return [e for e in self.list_experiments() if e.hypothesis == hyp_id]

    def save_experiment(self, exp: Experiment) -> Path:
        d = self.exp_dir(exp.id)
        d.mkdir(parents=True, exist_ok=True)
        p = self.meta_path(exp.id)
        p.write_text(exp.to_yaml(), encoding="utf-8")
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
        """Return (heading, body) pairs for each `## ` section in notes.md."""
        sections: list[tuple[str, str]] = []
        heading: str | None = None
        body: list[str] = []
        for line in self.read_notes(exp_id).splitlines():
            if line.startswith("## "):
                if heading is not None:
                    sections.append((heading, "\n".join(body).strip()))
                heading = line[3:].strip()
                body = []
            elif heading is not None:
                body.append(line)
        if heading is not None:
            sections.append((heading, "\n".join(body).strip()))
        return sections

    def has_discussion(self, exp_id: str) -> bool:
        """True when notes.md has a section beyond the initial intent."""
        sections = self.note_sections(exp_id)
        return any(not h.endswith("intent") for h, _ in sections)

    # ------------------------------------------------------------------ misc

    def touch_synced(self, exp: Experiment) -> None:
        exp.synced_at = now_iso()
        self.save_experiment(exp)
