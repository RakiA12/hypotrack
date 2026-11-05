"""Hypothesis / Experiment / Run data model and YAML (de)serialization."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any

import yaml

HYP_STATUSES = ("open", "supported", "refuted", "inconclusive", "abandoned")
EXP_STATUSES = ("draft", "submitted", "running", "completed", "failed", "partial", "cancelled")

# Slurm states that mean the job will not change any more.
TERMINAL_STATES = {
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "TIMEOUT",
    "OUT_OF_MEMORY",
    "NODE_FAIL",
    "PREEMPTED",
    "BOOT_FAIL",
    "DEADLINE",
    "REVOKED",
}
ACTIVE_STATES = {"RUNNING", "COMPLETING", "SUSPENDED", "STAGE_OUT", "SIGNALING"}
QUEUED_STATES = {"PENDING", "CONFIGURING", "REQUEUED", "RESIZING", "RESV_DEL_HOLD", "REQUEUE_HOLD"}


# ----------------------------------------------------------------------------
# YAML helpers: block scalars for multi-line strings, insertion-ordered keys
# ----------------------------------------------------------------------------


class _Dumper(yaml.SafeDumper):
    pass


def _str_representer(dumper: yaml.SafeDumper, data: str):  # type: ignore[no-untyped-def]
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_Dumper.add_representer(str, _str_representer)


def dump_yaml(data: Any) -> str:
    return yaml.dump(data, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=1000)


def load_yaml(text: str) -> Any:
    return yaml.safe_load(text)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def today_iso() -> str:
    return date.today().isoformat()


def _drop_none(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


# ----------------------------------------------------------------------------
# Hypothesis
# ----------------------------------------------------------------------------


@dataclass
class Hypothesis:
    id: str
    title: str
    parent: str | None = None
    status: str = "open"
    created: str = field(default_factory=today_iso)
    concluded: str | None = None
    evidence: list[str] = field(default_factory=list)
    statement: str = ""
    rationale: str = ""
    conclusion: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "parent": self.parent,
            "title": self.title,
            "status": self.status,
            "created": self.created,
            "concluded": self.concluded,
            "evidence": list(self.evidence),
            "statement": self.statement,
            "rationale": self.rationale,
            "conclusion": self.conclusion,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Hypothesis":
        return cls(
            id=str(d["id"]),
            title=str(d.get("title") or ""),
            parent=d.get("parent") or None,
            status=str(d.get("status") or "open"),
            created=str(d.get("created") or today_iso()),
            concluded=str(d["concluded"]) if d.get("concluded") else None,
            evidence=[str(e) for e in (d.get("evidence") or [])],
            statement=(d.get("statement") or "").rstrip("\n"),
            rationale=(d.get("rationale") or "").rstrip("\n"),
            conclusion=(d.get("conclusion") or "").rstrip("\n"),
        )

    def to_yaml(self) -> str:
        return dump_yaml(self.to_dict())

    @classmethod
    def from_yaml(cls, text: str) -> "Hypothesis":
        return cls.from_dict(load_yaml(text) or {})


# ----------------------------------------------------------------------------
# Experiment / Run
# ----------------------------------------------------------------------------


@dataclass
class Run:
    subdir: str | None = None
    job_id: str | None = None
    overrides: list[str] = field(default_factory=list)
    state: str | None = None
    exit_code: str | None = None
    elapsed: str | None = None
    wandb_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if not d["overrides"]:
            d.pop("overrides")
        return _drop_none(d)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Run":
        return cls(
            subdir=str(d["subdir"]) if d.get("subdir") is not None else None,
            job_id=str(d["job_id"]) if d.get("job_id") is not None else None,
            overrides=[str(o) for o in (d.get("overrides") or [])],
            state=str(d["state"]) if d.get("state") else None,
            exit_code=str(d["exit_code"]) if d.get("exit_code") is not None else None,
            elapsed=str(d["elapsed"]) if d.get("elapsed") else None,
            wandb_url=str(d["wandb_url"]) if d.get("wandb_url") else None,
        )

    @property
    def label(self) -> str:
        """Short run label: Hydra job num (last path component) or job id."""
        if self.subdir:
            return self.subdir.rsplit("/", 1)[-1]
        return self.job_id or "?"

    @property
    def is_failed(self) -> bool:
        return self.state is not None and self.state in TERMINAL_STATES and self.state != "COMPLETED"


@dataclass
class Experiment:
    id: str
    hypothesis: str
    title: str
    created: str = field(default_factory=now_iso)
    code: dict[str, Any] = field(default_factory=dict)
    hydra: dict[str, Any] = field(default_factory=dict)
    submit: dict[str, Any] = field(default_factory=dict)
    output_root: str | None = None
    status: str = "draft"
    runs: list[Run] = field(default_factory=list)
    synced_at: str | None = None
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "hypothesis": self.hypothesis,
            "title": self.title,
            "created": self.created,
            "code": dict(self.code),
            "hydra": dict(self.hydra),
            "submit": dict(self.submit),
            "output_root": self.output_root,
            "status": self.status,
            "runs": [r.to_dict() for r in self.runs],
            "synced_at": self.synced_at,
            "summary": dict(self.summary),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Experiment":
        return cls(
            id=str(d["id"]),
            hypothesis=str(d.get("hypothesis") or ""),
            title=str(d.get("title") or ""),
            created=str(d.get("created") or now_iso()),
            code=dict(d.get("code") or {}),
            hydra=dict(d.get("hydra") or {}),
            submit=dict(d.get("submit") or {}),
            output_root=d.get("output_root") or None,
            status=str(d.get("status") or "draft"),
            runs=[Run.from_dict(r) for r in (d.get("runs") or [])],
            synced_at=str(d["synced_at"]) if d.get("synced_at") else None,
            summary=dict(d.get("summary") or {}),
        )

    def to_yaml(self) -> str:
        return dump_yaml(self.to_dict())

    @classmethod
    def from_yaml(cls, text: str) -> "Experiment":
        return cls.from_dict(load_yaml(text) or {})

    @property
    def overrides(self) -> list[str]:
        return [str(o) for o in (self.hydra.get("overrides") or [])]

    @property
    def job_ids(self) -> list[str]:
        return [r.job_id for r in self.runs if r.job_id]

    @property
    def is_finished(self) -> bool:
        return self.status in ("completed", "failed", "partial", "cancelled")

    def derive_status(self) -> str:
        """Derive experiment status from run states (see spec section 5 / 8)."""
        if self.status == "cancelled":
            return "cancelled"
        if self.status == "draft" or not self.runs:
            return self.status
        states = [r.state for r in self.runs]
        if any(s in ACTIVE_STATES for s in states):
            return "running"
        if not all(s is not None and s in TERMINAL_STATES for s in states):
            # unknown or still queued (PENDING etc.)
            return "submitted"
        if all(s == "COMPLETED" for s in states):
            return "completed"
        if all(s == "CANCELLED" for s in states):
            return "cancelled"
        if any(s == "COMPLETED" for s in states):
            return "partial"
        return "failed"
