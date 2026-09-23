# Running the engine

```bash
python3 bin/go_live.py --engine AmunEV_Engine_V2.py --sports basketball
```

## Which do you run?

**`bin/go_live.py`.** Not the engine directly, and not `run_m5.py` by hand.

- **The engine is a library.** `AmunEV_Engine_V2.py` defines ~60 modules and never
  calls itself. `python3 AmunEV_Engine_V2.py` does nothing at all.
- **`run_m5.py` is the real runner**, and it ships *inside* the engine as a compressed
  blob. `go_live.py` extracts it from the engine you are about to run, so the runner and
  the engine are always the same build — no version skew.
- **`go_live.py` does not replace it.** It preflights, invokes it, and indexes the output.

## The two failures it exists to stop

Both cost you a full run before you find out.

1. `run_m5.py` finds the engine by globbing `LazarusEV_Engine_v*.py`.
   `AmunEV_Engine_V2.py` does not match, and without `--engine` the runner dies on
   `os.path.abspath(None)` *after* you have waited through boot. `go_live.py` always
   passes `--engine`.
2. The engine needs **Python ≥ 3.12** (PEP 701 nested-quote f-strings) and **pandas 2.x**.
   On 3.11 it dies mid-boot with a `SyntaxError` that reads like an engine fault.

## Preflight — static, nothing is executed

```bash
python3 bin/go_live.py --preflight-only --sports basketball
```

Checks the interpreter and the stack, that the engine **compiles** (not just parses —
`ast.parse` accepts a duplicate keyword argument that `compile()` rejects, and that
exact defect would have failed the engine at import), that the **GOD-1 and GOD-2 seals
are intact**, that `LAZ_OWNER['rules']` still matches your rules — min odds 1.40 in
mode 3 / 1.50 elsewhere, **no `band_hi_ceiling`**, `n_is ≥ 100`, `n_oos ≥ 100`,
`ROI > 0` — that the frames exist, and that there is disk.

## What you get, per sport

| path | what |
|---|---|
| `Workbooks/LAZARUS_<SPORT>_MODE3_<ts>.xlsx` | the book you read |
| `Workbooks/<same>.provenance.json` | which engine built it |
| `production/<sport>/deploy.sql` | **one transaction**: feature columns → `laz_feature_namespace` → `laz_strategy_registry` → `laz_placement_policy` → provenance |
| `production/<sport>/laz_features_<sport>.py` | every term, 1:1 from the engine's own builders |
| `production/<sport>/laz_pca_features_<sport>.py` | the run's frozen PCA transforms |
| `production/<sport>/STRATEGY_SPEC.md` | every element, in execution order, with exact triggers |
| `production/<sport>/strategies_full.json` | the same, machine-readable |
| `production/<sport>/verify_live.py` | re-checks the live DB against the bundle. **Reads only** |
| `production/<sport>/quarantine.csv` | validated, not yet settleable, with the exact reason |
| `production/<sport>/RUNBOOK.md` | what to run, in what order |
| `Logs/`, `Rejections/`, `DeployKits/`, `mode3_<sport>.parquet` | the run's own records |

## Do I need to delete anything from the folder?

**No. Leave it all where it is.** `go_live.py` is built not to collide:

| file | safe? | why |
|---|---|---|
| your **`run_m5.py`** | **yes, and it is never touched** | the runner extracted from the engine is written as `run_m5_<engine-sha8>.py`, so yours is not overwritten. If yours *differs* from the engine's, preflight says so and tells you to diff them — it does not decide for you. |
| **`run_deploy_az.sh`** or any other script of yours | **yes** | `go_live.py` runs nothing but `run_m5_<sha8>.py`. It never invokes, sources or reads your scripts. If that script deploys to Azure, it is a *later* step — run it after `verify_live.py` passes, not instead of it. |
| an old **`LazarusEV_Engine_v*.py`** | **yes here, risky elsewhere** | `go_live.py` always passes `--engine`, so a stray engine can never be picked up. But `run_m5.py` on its own globs `LazarusEV_Engine_v*.py` and takes the **last one alphabetically** — so if you or a script ever call `run_m5.py` by hand, a stale engine wins and the numbers you read came from the wrong build. Preflight names any stray it finds. |
| output from previous runs | **yes** | every artefact is stamped. Anything older than the moment the run started is reported as **STALE**, is never counted as READY, and its deploy commands are never printed. |

**The one that used to be dangerous, and is now fixed:** before this change, a run that
died before writing its bundle left last week's `production/<sport>/` sitting there — and
the report read it, called the sport READY, and printed the `psql` line. You would have
deployed a registry built by an engine you had already replaced. That cannot happen now.

## Passing flags through

Anything after `--` goes straight to `run_m5.py`:

```bash
python3 bin/go_live.py --sports basketball -- --workers 8 --target-new 200
```

## It adds no rules

`go_live.py` sets no threshold, no cap and no filter. Every flag it passes is either the
engine's own default or something you typed. GOD-2.
