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
| `.exps/hypotheses.yaml` | metadata of every hypothesis in one file, keyed by id: parent, title, status, dates, evidence |
| `.exps/hypotheses/` | one Markdown per hypothesis (`H1.1.md`): statement, rationale, conclusion |
| `.exps/experiments.yaml` | metadata of every experiment in one file, keyed by id: hypothesis, title, status, code, submit, runs, summary |
| `.exps/experiments/exp-NNN/` | `notes.md` (humans and agents), `submit.log`, `results/` (pulled, gitignored) |
| `.claude/skills/hypotrack/SKILL.md` | the Claude Code skill explaining the workflow |
| `.claude/settings.json` | `permissions.deny` for `ssh` / `scp` / `sftp` / `rsync` (merged into an existing file), so agents reach the cluster only through `hypx` |
| git remote `cluster` | `ssh://hpc/~/proj/repo`, receives `refs/exps/*` |

The deny rules are Claude Code's Bash pattern matching: they keep agents on the `hypx` path but are
not a cluster-side defence, and `hypx` itself still uses your own ssh key and config.

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

Note that the submit command runs inside the per-experiment worktree (`work_root/<exp-id>`), so a
relative `outputs/` lands there, while `hypx pull` reads `output_root/<exp-id>`. Either make the
Hydra directory absolute (`${oc.env:HYPX_OUTPUT_ROOT}/...`), or have the submit script symlink the
worktree's `outputs/` to `output_root/<exp-id>` before calling `sbatch`.

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

`status` is derived from `runs[].state` after `hypx status`: all `COMPLETED` (or `FINISHED`) → `completed`;
some `COMPLETED`, some failed → `partial`; none completed → `failed`; anything `RUNNING` →
`running`; `hypx cancel` → `cancelled`. `pull` works for `partial` and `failed` too and fetches
the failed runs' Slurm logs (all runs with `--include-logs`, last N lines with `--tail N`).

States come from `sacct` by default (`status_backend: sacct` in `config.yaml`). On a cluster without
accounting storage (`sacct` answers "Slurm accounting storage is disabled"), `hypx status --backend
squeue` reads the queue instead. `squeue` lists queued and running jobs only, so a submitted job that
no longer appears has finished: its runs are recorded as `FINISHED` (counts as a success for the
experiment status, but the exit code is unknown, so check the logs). Correct a run by hand with
`hypx status --mark 2801=FAILED` (repeatable, no cluster access; any Slurm state name is accepted).
Set `status_backend: squeue` to make that the default.

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
   the `runs[]` of `exp-001` in `.exps/experiments.yaml`.
4. `hypx status`, then `hypx pull exp-001 --include-logs`. If Slurm logs are not found, set
   `slurm_log_pattern` in `config.yaml` (relative to the worktree, `{job_id}` and `{exp_id}` are
   substituted; an absolute or `~` path is used as-is).
5. If `metrics.jsonl` uses another step key or file name, set `metrics_step_key` /
   `metrics_glob`, then `hypx results exp-001`.
6. Use `hypx --verbose ...` or read `.exps/hypotrack.log` to see every git/ssh/rsync command.

Phase 2 (not implemented): `hypx gc`, `hypx serve`, `hypx list --all-projects`, and queries
against `index.sqlite` (`hypx reindex` already rebuilds it).
