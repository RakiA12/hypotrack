# hypotrack

`hypx` is a context-management CLI for hypothesis-driven ML experiments. It keeps the
**hypothesis → experiment → result → next hypothesis** loop in plain files under `.exps/` in your
repository, snapshots the exact code of every submission into a hidden Git ref, submits through
your existing Slurm script over ssh, pulls the lightweight results back with rsync, and renders
everything as Markdown / JSON that a coding agent (Claude Code) can read directly.

The tool never calls an LLM, never interprets numbers, and never talks to the wandb API.

## Install (editable, from a local clone)

Requires Python 3.11+, git, ssh, rsync. Not published on PyPI.

```bash
git clone <this repo> ~/src/hypotrack
uv tool install --editable ~/src/hypotrack     # puts `hypotrack` and `hypx` on PATH
hypx --version
```

Changes in the clone take effect immediately.

## Set up a project

Run at the root of the experiment repository (the one with your Hydra configs and submit script).
The cluster must have a clone of the same repository.

```bash
hypx init --cluster-host hpc --remote-repo ~/proj/repo --metric val_loss
hypx init --claude-md >> CLAUDE.md
```

This creates:

| Path | Purpose |
| --- | --- |
| `.exps/config.yaml` | cluster host, remote paths, metrics glob, default metrics, rsync excludes |
| `.exps/hypotheses/` | one YAML per hypothesis (`H1.yaml`, `H1.1.yaml`, …) |
| `.exps/experiments/exp-NNN/` | `meta.yaml` (tool-written), `notes.md` (humans and agents), `results/` (pulled, gitignored) |
| `.claude/skills/hypotrack/SKILL.md` | the Claude Code skill explaining the workflow |
| git remote `cluster` | `ssh://hpc/~/proj/repo`, receives `refs/exps/*` |

Defaults derived from `--remote-repo ~/proj/repo`: worktrees in `~/proj/.exp-work/<exp-id>`,
Hydra outputs in `~/proj/outputs/<exp-id>`. Override with `--work-root` / `--output-root` or edit
`.exps/config.yaml`.

### One change in the experiment repository

Hydra must write under `outputs/$EXP_ID/…`. `hypx run` exports `EXP_ID=exp-012`; manual runs fall
back to `outputs/manual/`:

```yaml
hydra:
  run:
    dir: outputs/${oc.env:EXP_ID,manual}/${now:%Y-%m-%d_%H-%M-%S}
  sweep:
    dir: outputs/${oc.env:EXP_ID,manual}/${now:%Y-%m-%d_%H-%M-%S}
    subdir: ${hydra.job.num}
```

Also make sure your submit script passes the environment to `sbatch` (`--export=ALL`, or
`export EXP_ID` inside the script) and that its stdout contains Slurm's `Submitted batch job N`
lines (one per job; all are captured).

## Daily loop

```bash
hypx context --hyp H1.1                      # where things stand (agents read this first)
hypx hyp new --parent H1.1 --title "dropout 0.2 generalizes better" --body "statement"
hypx exp new --hyp H1.1.3 --title "dropout sweep" --body "why we run this"
hypx run exp-014 -- bash scripts/submit.sh -m model.dropout=0.1,0.2,0.3
hypx status                                  # sacct → runs[].state, experiment status
hypx pull --all-completed                    # rsync .hydra/, metrics.jsonl, failed-run logs
hypx results exp-014 --metric val_loss --best min
hypx diff exp-012 exp-014 --stat
hypx note exp-014 --body "0.2 is best; 0.3 underfits"
hypx hyp conclude H1.1.3 --status supported --evidence exp-014 --body "..."
```

Every command accepts `--json`. Text bodies come from `--body TEXT`, from stdin with `--body -`,
or from stdin when something is already piped in. Paths in the output are repository-relative.

### What `hypx run` does

1. Refuses unless the experiment is `draft` (`--resubmit` to override).
2. Snapshots the working tree, dirty files included, into `refs/exps/exp-014` (`.gitignore`
   respected, `.exps/` excluded). A clean tree points the ref at `HEAD` without a new commit.
3. Aborts if the tree hash and overrides equal an earlier submission (`--force` to continue).
4. Pushes the ref straight to the `cluster` remote, never via `origin`.
5. `git worktree add --detach ~/proj/.exp-work/exp-014 refs/exps/exp-014` on the cluster.
6. Runs `cd <worktree> && EXP_ID=exp-014 <your command>` and saves stdout to `submit.log`.
7. Records every `Submitted batch job N`; with none, the experiment goes back to `draft`.

The running job keeps using its worktree, so you can keep editing locally.

Reproduce or compare later with plain git: `git worktree add /tmp/exp-014 refs/exps/exp-014`,
`git diff refs/exps/exp-012 refs/exps/exp-014`.

## Status model

`status` is derived from `runs[].state` after `hypx status`: all `COMPLETED` → `completed`;
some `COMPLETED`, some failed → `partial`; none completed → `failed`; anything `RUNNING` →
`running`; `hypx cancel` → `cancelled`. `pull` works for `partial` and `failed` too and fetches
the failed runs' Slurm logs (all runs with `--include-logs`, last N lines with `--tail N`).

## Development

```bash
uv sync --dev
uv run pytest -q
```

ssh, rsync and sacct are mocked in the tests; Git operations run against real temporary
repositories (including a bare "cluster" remote).

### Verifying on a real cluster

No cluster was available while this was written, so `run` / `status` / `pull` / `cancel` have
only been exercised against mocks. Checklist for the first real cycle:

1. `hypx init --cluster-host <host> --remote-repo <path of the clone>`; confirm
   `git push cluster HEAD:refs/exps/smoke` works and `ssh <host> true` needs no password
   (ControlMaster recommended).
2. In the experiment repo, confirm the submit script propagates `EXP_ID` to the job and prints
   `Submitted batch job N` once per job. If one `sbatch` runs several Hydra jobs (job array),
   `pull` maps every run dir to that single job id and warns.
3. `hypx run exp-001 -- <submit command>`; check `.exps/experiments/exp-001/submit.log` and
   `meta.yaml` `runs[]`.
4. `hypx status`, then `hypx pull exp-001 --include-logs`. If Slurm logs are not found, set
   `slurm_log_pattern` in `config.yaml` (relative to the worktree, `{job_id}` and `{exp_id}` are
   substituted; an absolute or `~` path is used as-is).
5. If `metrics.jsonl` uses another step key or file name, set `metrics_step_key` /
   `metrics_glob`, then `hypx results exp-001`.
6. Use `hypx --verbose ...` or read `.exps/hypotrack.log` to see every git/ssh/rsync command.

Phase 2 (not implemented): `hypx gc`, `hypx serve`, `hypx list --all-projects`, and queries
against `index.sqlite` (`hypx reindex` already rebuilds it).
