"""`hypx context`: one Markdown document an agent reads before deciding what to do next."""

from __future__ import annotations

from typing import Any

from . import ids
from .config import Config
from .models import Experiment, Hypothesis
from .paths import Paths
from .render import best_metric_line, first_line, md_table
from .store import Store


def estimate_tokens(text: str) -> int:
    """Rough token estimate good enough for a budget cut-off (mixed CJK / ASCII)."""
    return max(len(text) // 3, len(text.split()))


def _subtree(all_hyps: list[Hypothesis], root: str) -> list[Hypothesis]:
    return [h for h in all_hyps if h.id == root or h.id.startswith(root + ".")]


def _tail_note(store: Store, exp_id: str) -> tuple[str, str] | None:
    sections = store.note_sections(exp_id)
    return sections[-1] if sections else None


def _best_line(exp: Experiment) -> str:
    return best_metric_line(exp)


def build_context(
    store: Store,
    cfg: Config,
    paths: Paths,
    hyp_id: str | None,
    recent: int = 5,
    budget: int | None = None,
) -> tuple[str, dict[str, Any]]:
    all_hyps = store.list_hypotheses()
    all_exps = store.list_experiments()
    by_id = {h.id: h for h in all_hyps}

    if hyp_id:
        target = store.get_hypothesis(hyp_id)
        path = [by_id[a] for a in ids.ancestors(hyp_id) if a in by_id]
        parent = target.parent
        siblings = [h for h in all_hyps if h.parent == parent]  # includes target
        scope_hyps: list[Hypothesis] = []
        for s in siblings:
            scope_hyps += _subtree(all_hyps, s.id)
        focus_ids = {h.id for h in _subtree(all_hyps, hyp_id)}
    else:
        target = None
        path = [h for h in all_hyps if h.parent is None]
        scope_hyps = list(all_hyps)
        focus_ids = {h.id for h in all_hyps}

    scope_ids = {h.id for h in scope_hyps}
    scope_exps = [e for e in all_exps if e.hypothesis in scope_ids]
    recent_exps = sorted(scope_exps, key=lambda e: e.created, reverse=True)

    # open items
    open_children = [h for h in scope_hyps if h.id in focus_ids and h.status == "open" and h.id != hyp_id and store.is_leaf(h.id)]
    drafts = [e for e in scope_exps if e.status == "draft"]
    undiscussed = [e for e in scope_exps if e.status in ("completed", "partial") and not store.has_discussion(e.id)]

    def render(n_recent: int, columns: list[str]) -> str:
        lines: list[str] = []
        title = f"# context: {hyp_id}" if hyp_id else "# context: all hypotheses"
        lines += [title, ""]

        lines += ["## hypothesis path" if hyp_id else "## root hypotheses", ""]
        for h in path:
            concl = f" — {first_line(h.conclusion)}" if h.conclusion.strip() else ""
            lines.append(f"- {h.id} [{h.status}] {h.title}{concl}")
            if h.id == hyp_id and h.statement.strip():
                lines.append(f"  - statement: {first_line(h.statement)}")
        if hyp_id:
            others = [h for h in scope_hyps if h.id in focus_ids and h.id != hyp_id]
            if others:
                lines += ["", "children:"]
                for h in others:
                    depth = len(ids.hyp_path(h.id)) - len(ids.hyp_path(hyp_id))
                    lines.append(f"{'  ' * (depth - 1)}- {h.id} [{h.status}] {h.title}")
            sib = [h for h in scope_hyps if h.parent == (target.parent if target else None) and h.id != hyp_id]
            if sib:
                lines += ["", "siblings:"]
                for h in sib:
                    lines.append(f"- {h.id} [{h.status}] {h.title}")

        lines += ["", "## experiments", ""]
        if scope_exps:
            header_map = {"id": "id", "hyp": "hyp", "title": "title", "status": "status", "best": "best"}
            headers = [header_map[c] for c in columns]
            rows = []
            for e in scope_exps:
                cell = {"id": e.id, "hyp": e.hypothesis, "title": e.title, "status": e.status, "best": _best_line(e)}
                rows.append([cell[c] for c in columns])
            lines.append(md_table(headers, rows))
        else:
            lines.append("(none)")

        lines += ["", f"## recent ({min(n_recent, len(recent_exps))} of {len(recent_exps)})", ""]
        if n_recent == 0 or not recent_exps:
            lines += ["(omitted)" if recent_exps else "(none)", ""]
        for e in recent_exps[:n_recent]:
            lines.append(f"### {e.id}: {e.title} [{e.status}]")
            best = _best_line(e)
            if best:
                lines.append(f"- best: {best}")
            tail = _tail_note(store, e.id)
            if tail:
                heading, body = tail
                lines.append(f"- last note ({heading}):")
                for ln in body.splitlines():
                    lines.append(f"  {ln}" if ln.strip() else "")
            lines.append(f"- files: {paths.rel(store.notes_path(e.id))}")
            lines.append("")

        lines += ["## open items", ""]
        any_item = False
        for h in open_children:
            lines.append(f"- open hypothesis without conclusion: {h.id} {h.title}")
            any_item = True
        for e in drafts:
            lines.append(f"- draft experiment not submitted: {e.id} {e.title}")
            any_item = True
        for e in undiscussed:
            lines.append(f"- {e.status} experiment without discussion in notes: {e.id} {e.title}")
            any_item = True
        if not any_item:
            lines.append("(none)")
        return "\n".join(lines).rstrip() + "\n"

    all_columns = ["id", "hyp", "title", "status", "best"]
    n_recent = max(0, recent)
    columns = list(all_columns)
    text = render(n_recent, columns)
    cuts: list[str] = []
    if budget is not None and budget > 0:
        start_recent = n_recent
        while estimate_tokens(text) > budget:
            if n_recent > 0:
                n_recent -= 1
                cuts[:] = [c for c in cuts if not c.startswith("recent")]
                cuts.insert(0, f"recent {start_recent} -> {n_recent}")
            elif "best" in columns:
                columns.remove("best")
                cuts.append("dropped column best")
            elif "title" in columns:
                columns.remove("title")
                cuts.append("dropped column title")
            elif "hyp" in columns:
                columns.remove("hyp")
                cuts.append("dropped column hyp")
            else:
                break
            text = render(n_recent, columns)
        if cuts:
            text += (
                f"\n_truncated to fit --budget {budget} (~{estimate_tokens(text)} tokens): "
                + "; ".join(cuts)
                + "_\n"
            )

    data: dict[str, Any] = {
        "hypothesis": hyp_id,
        "path": [{"id": h.id, "status": h.status, "title": h.title, "conclusion": first_line(h.conclusion)} for h in path],
        "experiments": [
            {"id": e.id, "hypothesis": e.hypothesis, "title": e.title, "status": e.status, "best": _best_line(e)}
            for e in scope_exps
        ],
        "recent": [
            {
                "id": e.id,
                "title": e.title,
                "status": e.status,
                "best": _best_line(e),
                "last_note": (_tail_note(store, e.id) or ("", ""))[1],
                "notes_path": paths.rel(store.notes_path(e.id)),
            }
            for e in recent_exps[:n_recent]
        ],
        "open_items": {
            "open_hypotheses": [h.id for h in open_children],
            "draft_experiments": [e.id for e in drafts],
            "undiscussed_experiments": [e.id for e in undiscussed],
        },
        "truncated": cuts,
        "estimated_tokens": estimate_tokens(text),
    }
    return text, data
