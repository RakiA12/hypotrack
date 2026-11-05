"""ID conventions: hypotheses are paths (H1, H1.2, H1.2.3); experiments are exp-NNN."""

from __future__ import annotations

import re
from collections.abc import Iterable

from .errors import HypxError

HYP_RE = re.compile(r"^H\d+(\.\d+)*$")
EXP_RE = re.compile(r"^exp-(\d+)$")


def validate_hyp_id(hyp_id: str) -> str:
    if not HYP_RE.match(hyp_id):
        raise HypxError(f"invalid hypothesis id {hyp_id!r} (expected H1, H1.2, H1.2.3 ...)")
    return hyp_id


def hyp_path(hyp_id: str) -> tuple[int, ...]:
    validate_hyp_id(hyp_id)
    return tuple(int(x) for x in hyp_id[1:].split("."))


def hyp_sort_key(hyp_id: str) -> tuple[int, ...]:
    return hyp_path(hyp_id)


def parent_of(hyp_id: str) -> str | None:
    parts = hyp_path(hyp_id)
    if len(parts) == 1:
        return None
    return "H" + ".".join(str(p) for p in parts[:-1])


def ancestors(hyp_id: str) -> list[str]:
    """Root-first list of ancestor ids, including hyp_id itself."""
    parts = hyp_path(hyp_id)
    return ["H" + ".".join(str(p) for p in parts[: i + 1]) for i in range(len(parts))]


def is_child_of(child: str, parent: str | None) -> bool:
    return parent_of(child) == parent


def next_child_id(parent: str | None, existing: Iterable[str]) -> str:
    """Allocate the next sibling number under parent (None = new root hypothesis)."""
    if parent is not None:
        validate_hyp_id(parent)
    siblings = [h for h in existing if is_child_of(h, parent)]
    max_n = max((hyp_path(h)[-1] for h in siblings), default=0)
    n = max_n + 1
    return f"H{n}" if parent is None else f"{parent}.{n}"


def validate_exp_id(exp_id: str) -> str:
    if not EXP_RE.match(exp_id):
        raise HypxError(f"invalid experiment id {exp_id!r} (expected exp-NNN)")
    return exp_id


def exp_number(exp_id: str) -> int:
    m = EXP_RE.match(exp_id)
    if not m:
        raise HypxError(f"invalid experiment id {exp_id!r}")
    return int(m.group(1))


def next_exp_id(existing: Iterable[str]) -> str:
    max_n = max((exp_number(e) for e in existing), default=0)
    return f"exp-{max_n + 1:03d}"
