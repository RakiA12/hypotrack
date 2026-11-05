---
name: hypotrack
description: Use whenever this repository's ML experiments are involved - planning a hypothesis, submitting a run to the cluster, checking status, reading results, or recording conclusions. The `hypx` CLI stores the hypothesis -> experiment -> result loop under .exps/ so you never lose the thread between sessions. Read `hypx context` before touching experiments.
---

# hypotrack (`hypx`)

`hypx` records hypotheses, experiments, code snapshots and results under `.exps/` in this repository.
Every command prints Markdown by default and JSON with `--json`. Paths it prints are repository-relative, so you can open them with Read.

## Three rules

1. **Before starting experiment work, run `hypx context --hyp <H..>`** (or plain `hypx context`). It gives the hypothesis path, related experiments, recent notes and open items.
2. **After looking at results, record your reading with `hypx note <exp-id>`.** The tool never interprets numbers; "improved" / "regressed" is your call and must be written down.
3. **Settle hypotheses with `hypx hyp conclude`.** A hypothesis stays `open` until you record `supported | refuted | inconclusive | abandoned` with `--evidence`.

## Command cheat sheet

| Goal | Command |
| --- | --- |
| See where things stand | `hypx context --hyp H1.1 [--recent 5] [--budget 8000]` |
| Browse the hypothesis tree | `hypx hyp tree [--status open]` |
| Create a hypothesis | `hypx hyp new --parent H1 --title "..." [--body "statement"]` (body also via stdin) |
| Create an experiment (draft) | `hypx exp new --hyp H1.1.2 --title "..." --body "why we run this"` |
| Submit to the cluster | `hypx run exp-014 -- bash scripts/submit.sh -m model.dropout=0.1,0.2` |
| Sync job states | `hypx status [exp-014]` |
| Fetch results back | `hypx pull [exp-014] [--include-logs] [--tail 200] [--all-completed]` |
| Read metrics | `hypx results exp-014 [--metric val_loss --best min] [--last] [--at-step N] [--raw]` |
| Everything about one experiment | `hypx show exp-014` |
| Compare two experiments | `hypx diff exp-012 exp-014 [--stat]` |
| Write an observation | `hypx note exp-014 --body "..."` (or pipe text on stdin) |
| Record a conclusion | `hypx hyp conclude H1.1.2 --status supported --evidence exp-014 --body "..."` |
| Stop an experiment | `hypx cancel exp-014` |
| List experiments | `hypx list [--hyp H1] [--status running]` |

## Typical loop

1. `hypx context --hyp H1.1` to load the current state.
2. If a new leaf hypothesis is needed: `hypx hyp new --parent H1.1 --title ...`.
3. `hypx exp new --hyp H1.1.3 --title ... --body "<intent>"`. Never submit without recording the intent first.
4. Edit code and Hydra configs, then `hypx run exp-014 -- <the repository's submit command>`.
   `hypx run` snapshots the whole working tree (dirty files included) into `refs/exps/exp-014`, pushes it to the cluster clone, checks it out in its own worktree and runs the submit command there with `EXP_ID=exp-014`. The job keeps running on that snapshot even if you keep editing.
5. Later: `hypx status && hypx pull --all-completed`.
6. `hypx results exp-014`, `hypx diff exp-012 exp-014`, then `hypx note exp-014 --body "<what you observed and what it means>"`.
7. When the evidence settles the hypothesis: `hypx hyp conclude H1.1.3 --status supported --evidence exp-014 --body "..."`.
8. Back to 1.

## Boundaries

- Files under `.exps/` may be edited directly, except the `code`, `submit` and `runs` sections of `meta.yaml`, which only `hypx` writes.
- Experiments attach to leaf hypotheses only. Siblings share a parent; that parent is the relation between them.
- When you need raw data, use `hypx results <exp> --raw` to get the `metrics.jsonl` paths and read them yourself. Large logs: `tail` them, do not cat.
- `hypx run` refuses a non-draft experiment (use `--resubmit` deliberately) and refuses a submission identical in code and overrides to an earlier one (`--force` to override).
- `hypx` never calls wandb or an LLM. wandb URLs are recorded in `meta.yaml` for humans.
