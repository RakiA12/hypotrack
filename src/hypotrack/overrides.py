"""Hydra override handling: extraction from submit commands, per-run .hydra/overrides.yaml, varying vs fixed."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

# key=value, +key=value, ++key=value, ~key, ~key=value  (Hydra override grammar, loosely)
OVERRIDE_RE = re.compile(r"^(\+{0,2}|~)([A-Za-z_][\w./@\-\[\]]*)(?:=(.*))?$")
SKIP_FLAGS = {"-m", "--multirun", "-cn", "--config-name", "-cp", "--config-path", "-cd", "--config-dir"}


def is_override(token: str) -> bool:
    if token.startswith("-") and not token.startswith("~"):
        return False
    m = OVERRIDE_RE.match(token)
    if not m:
        return False
    # bare `foo` (no '=') is only an override when it is a deletion `~foo`
    return m.group(3) is not None or m.group(1) == "~"


def extract_overrides(argv: list[str]) -> list[str]:
    out: list[str] = []
    skip_next = False
    for tok in argv:
        if skip_next:
            skip_next = False
            continue
        if tok in SKIP_FLAGS and tok not in ("-m", "--multirun"):
            skip_next = True
            continue
        if is_override(tok):
            out.append(tok)
    return out


def config_name_from(argv: list[str]) -> str | None:
    for i, tok in enumerate(argv):
        if tok in ("-cn", "--config-name") and i + 1 < len(argv):
            return argv[i + 1]
        if tok.startswith("--config-name="):
            return tok.split("=", 1)[1]
    return None


def split_override(s: str) -> tuple[str, str]:
    m = OVERRIDE_RE.match(s)
    if not m:
        if "=" in s:
            k, v = s.split("=", 1)
            return k, v
        return s, ""
    prefix, key, value = m.group(1), m.group(2), m.group(3)
    if prefix == "~" and value is None:
        return key, "~"
    return key, value if value is not None else ""


def overrides_dict(overrides: list[str]) -> dict[str, str]:
    d: dict[str, str] = {}
    for o in overrides:
        k, v = split_override(o)
        d[k] = v
    return d


def read_run_overrides(run_dir: Path) -> list[str]:
    p = run_dir / ".hydra" / "overrides.yaml"
    if not p.exists():
        return []
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return []
    if isinstance(data, list):
        return [str(x) for x in data]
    return []


def varying_and_fixed(per_run: list[list[str]]) -> tuple[list[str], dict[str, str]]:
    """Split override keys into those that differ across runs and those constant for all runs.

    Keys missing from some runs count as varying. Order follows first appearance.
    """
    dicts = [overrides_dict(o) for o in per_run]
    order: list[str] = []
    for d in dicts:
        for k in d:
            if k not in order:
                order.append(k)
    varying: list[str] = []
    fixed: dict[str, str] = {}
    for k in order:
        values = [d.get(k) for d in dicts]
        if len(dicts) > 1 and len({v for v in values}) > 1:
            varying.append(k)
        elif values and values[0] is not None:
            fixed[k] = values[0]
    return varying, fixed


def override_diff(a: list[str], b: list[str]) -> list[tuple[str, str | None, str | None]]:
    """(key, value_in_a, value_in_b) for every key whose value differs."""
    da, db = overrides_dict(a), overrides_dict(b)
    keys: list[str] = []
    for k in list(da) + list(db):
        if k not in keys:
            keys.append(k)
    return [(k, da.get(k), db.get(k)) for k in keys if da.get(k) != db.get(k)]
