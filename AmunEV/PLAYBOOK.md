# AmunEV Engine V2 — the hardening playbook

A multi-agent pipeline that finishes, hardens and verifies the engine **from A to Z without asking the owner anything** — every open question is answered in advance in `DECISIONS.md`. It runs in **Claude Code on the owner's Mac** (10 cores), where the engine can actually be exercised; every rule below comes from a defect that reached the owner on 21–22 Sep 2026.

Baseline: engine `8ead3cb87a230c5b` · runner `7a00930ddbe359e6` · launcher with the requirements step (22 Sep).

---

---

## 0. Read this first — what happened on 21–22 Sep, and why this moves to Claude Code

**The crisis.** Across one long chat session the engine was fixed several times, and each fix was delivered as a zip named `AmunEV_V2_FINAL.zip`. Because the name never changed, a browser or a stale `~/Downloads` copy could be picked up in place of the newest one — `ls -t` still finds *a* file, just not necessarily the right one. On 22 Sep this happened twice: a basketball run reported results from an engine that still had a rejection bug (`chain_text_mismatch`) three fixes after that bug was corrected. The owner correctly identified this as unacceptable and gave a standing instruction: **one file, the true latest, never two.**

**The same session also surfaced a second, independent, pre-existing defect** (not introduced by any of the chat's fixes, confirmed present in the owner's own original reference copy): `--reset-book` was supposed to bypass a "don't retry a (base, rung) cell that found nothing last time" memory, but the code recorded that flag into the shared state **after** the check had already read it. So the bypass never worked, on any run, ever — and a disk-backed memory of "nothing here" quietly grew across every rerun. Three basketball runs on the same frame went from 189 candidate cells searched, to 48. This is fixed (V2.31, §6) and is exactly the kind of defect that a stale zip made hard to even notice: was the bad result from the bug, or from running the wrong file? Both were true at once.

**Why Claude Code ends this class of problem.** A zip is a snapshot with no history; comparing "is this the one" requires trusting a filename or a sha the owner has to copy by hand. A git repository is not a snapshot — `git log`, `git diff`, and `git status` on the actual `~/LazarusEngine` folder answer "what is here, and how did it get here" with total certainty, and there is nothing left to download or confuse. **From this point on, `~/LazarusEngine` is the only copy that exists.** Setup below happens once; every change afterward is a commit, never a new zip.

---

## 1. Setup (once) — after this, git is the only place a "version" lives

This package (one zip, one folder) contains **everything**: the engine already carrying the 22 Sep fixes through **V2.31** (`AmunEV_Engine_V2.py`, sha stated in HANDOVER.md's newest entry — check it once, here, and never again by re-downloading), the runner, the launcher, the preflight script, `requirements.txt`, and the hardening kit (`CLAUDE.md`, `DECISIONS.md`, this file, `.claude/`, `tools/`). Unzip it directly over `~/LazarusEngine` — it replaces the engine files with the fixed ones and adds the kit files beside them, in one step:

```bash
cd ~/LazarusEngine
mkdir -p ~/LazarusBackup && cp AB_frames/*_games.parquet AB_frames/*h2h_player_performance* ~/LazarusBackup/   # the irreplaceable input, backed up once
Z=$(ls -t ~/Downloads/LazarusEngine_*.zip | head -1); rm -rf /tmp/hk; unzip -o -q "$Z" -d /tmp/hk
cp -R /tmp/hk/*/. .          # engine + launcher + kit, all in one copy
shasum -a 256 AmunEV_Engine_V2.py | cut -c1-16      # confirm this matches HANDOVER.md's newest entry before doing anything else
[ -x .laz_venv/bin/python ] || WORKERS=8 bash run_deploy_az.sh etennis
git init -q; git add -A; git -c user.name=owner -c user.email=owner@local commit -qm "baseline $(shasum -a 256 AmunEV_Engine_V2.py | cut -c1-16) — V2.31"
bash tools/laz_gate.sh L1     # expect: 1 FAIL (S08, the 18 known file handles = packet W1) — this is the ONLY expected finding
bash tools/laz_gate.sh L2     # expect: 15 PASS · 0 FAIL
```

**From here there is no more zip, ever, for this project.** Every future change is made to the files already in `~/LazarusEngine` and recorded with `git commit`. If a chat session ever again produces a "new version," it is applied as an edit to these same files, not as a fresh download — `git diff` shows exactly what changed, which is the whole point.

**Start it** — in the terminal (the CLI; the VS Code extension has been reported to ignore project permission rules), with the Mac kept awake for the hours it takes:

```bash
cd ~/LazarusEngine && caffeinate -dimsu claude --model claude-opus-5
```

The first time, Claude Code shows the **workspace-trust** prompt for this folder — accept it: that is what activates `.claude/settings.json`. Then paste the kickoff prompt from §8 and leave it.

**What makes it unattended.** `.claude/settings.json` sets `defaultMode: dontAsk`: every action on its allow list runs without a prompt, anything else is refused instead of asked — including questions to the owner. The allow list covers the engine's Python environment, the gate and harness tools, git (no push, no hard reset, no clean) and edits to the code, the work records and `tools/`; the deny list covers deleting, `sudo`, network downloads, the frames, the deploy kits, and the rules files themselves (`CLAUDE.md`, `DECISIONS.md`, `.claude/`). A Bash rule matches the command text, so it is not a security wall — the backup above is.

**Models.** The agents pin `claude-opus-5`, `claude-sonnet-5` and `claude-haiku-4-5-20251001`. **Leave `CLAUDE_CODE_SUBAGENT_MODEL` unset:** set to `inherit`, it has been reported to force every subagent onto the main session's model and silently undo this allocation.

---

## 2. Roles and token allocation

| agent | model | share of tokens | does | never does |
|---|---|---|---|---|
| **laz-orchestrator** | Opus 5 | ~15% | backlog, packets, assignments, D0 decisions, checkpoints | writes engine code; reads the engine in bulk |
| **laz-implementer** | Sonnet 5 | ~65% | one packet: trace → minimal edit → repack → audits → evidence | changes doctrine; runs a full sport unless told |
| **laz-auditor** | Opus 5 | ~15% | independent verification of every packet; adversarial checks | edits engine files; trusts the implementer's summary |
| **laz-clerk** | Haiku 4.5 | ≤ 5% | command-produced lists, counts, log excerpts, pasted verbatim | interprets, diagnoses, answers, edits — ever |

Why this split: correctness here depends on judgment (tracing a value from the search to production, knowing when a change touches doctrine), so the two judgment roles — planning and auditing — get Opus, and the volume work (editing, re-running checks) gets Sonnet. Haiku is kept to tasks whose output a command can check; given room to explain, it tends to fill gaps with plausible inventions, so its prompt forbids anything but pasting command output.

**Escalation.** A packet that fails the audit twice goes back to the orchestrator to be split or re-planned (on Opus). Any doubt about doctrine goes to the owner (§5.3), never guessed.

---

## 3. The invariants — what "correct" means

Each is checked mechanically. The layer (§4) says where.

| # | invariant | how it is verified | layer |
|---|---|---|---|
| I1 | every requirement installed at the pinned version | step 0/4 table: `RESULT: ALL REQUIRED PACKAGES PRESENT` | every run |
| I2 | engine + runner parse; embedded runner = disk runner; docs count = source; launcher/preflight pinned to these files | static audit S02–S05, S12 | L1 |
| I3 | no cross-module bare name, no literal disk read outside the memory class, no unclosed file, no undefined name in live code | static audit S06–S09; `audit_full` at the gate | L1, L3 |
| I4 | settlement is the owner's rule (score, else last validated odds ≤ 1.35, else unsettled) | S10.6; log line `SETTLEMENT (owner rule) — score: … · last validated odds row <= 1.35: … · UNSETTLED …` | L1, L4 |
| I5 | nothing forward-looking reaches a condition; no leg arms before every term is knowable | S10.7; log lines `quarantine holds`, `TRUNCATION: … none read the future`; `arming_window` rejections | L1, L4 |
| I6 | a leg's shipping text (conditions + band) rebuilds exactly its measured bets | S10.1–S10.5; `chain_text_mismatch` in the rejection histogram names every leg refused | L1, L4 |
| I7 | production computes every term the search used, bit-exact | `[prove_forward_only] … N terms exact · 0 failed` | L4 |
| I8 | production arms on the same tick the search measured | `ARMING_TRACE · N/N legs traced to the exact ledger tick` | L4 |
| I9 | every kit is complete and self-verifying | 13 files + PREFLIGHT `FAILED 0` + README/INDEX/evidence; proven subset when only I7 fails | L2, L4 |
| I10 | the launcher works under macOS bash 3.2 | S11 + the harness (15 assertions, mutation-tested) | L1, L2 |
| I11 | the proof battery passes | `gates: 33 proofs PASS, 0 FAIL` (cached per engine+runner sha) | L3 |
| I12 | production install is clean | `install.ps1` dry run ends `DRY RUN PASSED` | release |

---

## 4. The verification ladder — cheapest first, every time

| layer | what | cost | when |
|---|---|---|---|
| **L1** | `bash tools/laz_gate.sh L1` (the static audit) | seconds | after **every** edit |
| **L2** | `bash tools/laz_gate.sh L2` (the launcher harness) | seconds | after every edit; always before L3 |
| **L3** | `bash tools/laz_gate.sh start L3` → `wait <id>` (the battery) | ~15 min, then cached for that exact engine+runner | once per merged group of packets (orchestrator's checkpoint) |
| **L4** | `bash tools/laz_gate.sh start L4 etennis` → `wait <id>` (smoke); `basketball` when a packet touches spreads/totals/quarters | 6 / ~30 min | after packets that touch the search, the release or the kit |
| **L5** | `bash tools/laz_gate.sh start L5` → `wait <id>` (all eight sports; eFootball in batches) | hours | once, at delivery (DECISIONS D7) |

A packet is not done until L1 and L2 are all PASS and the auditor has signed it. Nobody climbs to L3+ on a red L1/L2. `wait` returns within ~9 minutes (RUNNING, with the last progress line) so no tool call ever times out; call it again. `bash tools/laz_gate.sh compare <A> <B>` gives per-sport deltas and says whether two runs' bet ledgers are identical row for row.

---

## 5. How work moves

### 5.1 The packet (the orchestrator writes it to `packets/<id>.md`)

```
ID:          W2.3
Title:       production recipes for pts_last_60s, opp_pts_last_60s, backed_pts_last_60s
Why:         basketball 22 Sep — [prove_forward_only] FAIL … generated nan (kit NOT READY)
Scope:       laz_featuregen body dict + FEATURE_PROVENANCE; nothing else
Doctrine:    none touched (production must reproduce the search; the search is unchanged)
Acceptance:  L1 + L2 all PASS; replay-only on basketball reports these 3 terms exact
Checkpoint:  none (batched into the W2 group's L3/L4)
Assigned:    laz-implementer · audit: laz-auditor
```

### 5.2 Definition of done
1. `evidence/<id>.md` (implementer): trace of every reader/writer, the diff, each command with its output, the acceptance result quoted from the output.
2. `evidence/<id>.AUDIT.md` (auditor): PASS, with the RESULT lines it reproduced and its adversarial findings.
3. `WORKLOG.md` updated; `HANDOVER.md` gains one line per packet (the repack `--note` does the version line).

### 5.3 Decisions
Nothing goes to the owner. `DECISIONS.md` answers every question the backlog raises; anything it doesn't cover is decided by its rule D0 and written to `DECISIONS_LOG.md` (context, options, choice, why, how to reverse).

---

## 6. The backlog (from the 21–22 Sep evidence)

### Where the time goes — measured, basketball 22 Sep (engine 75cf80f6, 124 min)

| stage | min | share |
|---|---|---|
| **the sweep** (189 base × rung tasks on 8 workers) | **107.7** | **86.7%** |
| BaseFinder | 5.4 | 4.4% |
| release (export, feature file, replay proof) | 3.1 | 2.5% |
| first frame load (column classification — since remembered across runs) | 2.6 | 2.1% |
| learning | 1.7 | 1.3% |
| everything else | 3.8 | 3.0% |

Inside the sweep, two measured facts drive the time:
- **The luck check re-runs the whole search on shuffled outcomes, once per shuffle, for every accepted strategy.** A strong strategy pays the full count, because only weak ones stop early. That run used 200 shuffles (reverted to the configured 50 since); with 261 strategies accepted against 4,511 search attempts, the luck check could have cost several times the search itself.
- **The tail.** The progress lines show 17.1 minutes in which one task finished (12:02 → 12:19), 11.1 minutes for three (11:28 → 11:39) and 6.8 for one. Heavy tasks — the ones that accept several strong strategies and grind through their shuffles — were still running at the end while most workers had nothing left.

So S1, S2, S3, S5 and S6 together can save at most about 15 minutes; **S8 and S4 are the levers that matter.** The exact split between search and luck check was measured on every run and discarded (the parallel sweep silences worker logs); engine `8ead3cb87a230c5b` keeps it: `[sweep-prof] TOTAL … search … luck check … heaviest …`, recorded by every gate run. The baseline L4 basketball on that engine measures the real split before any speed packet starts.


Order: owner decisions first, then what blocks production, then speed, then breadth.

| id | item | evidence | model | acceptance |
|---|---|---|---|---|
| **W0** | **withdrawn** — the search's semantics stay as the owner built them (DECISIONS D1) | — | — | — |
| **W1** | 18 live functions open a file without `with` | S08: `laz_batch__plan:86122`, `laz_featuregen___hazard_covariates`, `…hazard_literal`, `…motif_literal`, `…motif_window`, `laz_trace__manifest`, `laz_startup__prepare__impl`, `laz_genome__load_handwritten`, `laz_mode3___sweep_one_base`, `laz_spawn__write_state`, `laz_spawn__boot`, `laz_ledger__export`, `laz_xl__write`, `laz_production__implementation_docs` (×2), `laz_production__export`, `laz_audit__gate`, `laz_docs__write_all` | Sonnet | S08 PASS; L3 PASS. **Do this first** — mechanical, and it proves the pipeline end to end. |
| **W2.0** | a `--replay-only <sport>` runner command: build the pool (prepare memo), write `laz_features_<sport>.py`, run the replay proof, print every term's result. Nothing else. | the replay proof only runs at the end of a full sport today | Sonnet (Opus reviews the design) | runs in minutes on basketball; output matches the last full run's FAIL list |
| **W2.1…** | production recipes for the **59** terms that do not replay exactly (basketball 22 Sep) — batches of ~8, grouped by family: trailing windows (`pts_last_60s`, `lead_delta_last_120s`, `margin_range_300s`, `lead_changes_300s` …), leader/trailer prices (`leader_drift`, `trailer_price`, `leader_runmax`, `loser_runmin` …), totals/line (`tot_vs_line`, `line_vel`, `needed_rate_for_over`, `pace_vs_line_pct` …), the `u_*` family (`u_price`, `u_jump`, `u_overround`, `u_ratio_open`, `u_hazard`), `lead_m15/m30/m75`, `lead_age_s`, `motif_1`, `dow`, `prog`, raw columns (`point_spread_home`, `total_points_over`). Plus the 7 `pc_*` composites and 8 terms "not in pool" (SKIP). | the basketball run log, `[prove_forward_only]` lines | Sonnet per batch | replay-only: each term exact; the proven subset grows accordingly |
| **W3** | seeds that carry no text (tick hotspots, BaseFinder bases) are refused by the round-trip; give them exact text so their legs can ship | `_seed_txt.append(None)` for tick/base seeds; `chain_text_mismatch` counts | Opus designs, Sonnet builds | on the smoke sport, legs from those seeds appear and pass the round-trip |
| **W4** | the registry's top-rung ceiling uses the OOS max odds × 1.25, so in-sample bets above it would not be placed in production | `laz_release__registry_sql` ceiling | Sonnet | ceiling from every measured bet's odds; round-trip unchanged |
| **W5** | **not done** — only on the owner's written instruction (DECISIONS D1) | — | — | — |
| **W6** | arming trace to N/N (74/114 on basketball) — re-measure after W2, W3, W5; fix what remains | trace log line | Sonnet | `N/N` |
| **W7** | → moved to **S4** below | | | |
| **S8** | **speed — the biggest lever** · the luck check as its own stage after the sweep. At acceptance a task records each strategy's null job (its seed mask, packed; its base and rung; its deterministic random key); after the merge, the jobs run on all workers, handed out dynamically, heaviest first. Same shuffles (`laz_mode3___np_random_for(key)`), same early-stop rule, same n_perm (50, `LAZ_N_PERM`), so every `perm_p`, `n_nulls`, `p_resolution` and `null_kind` is identical. The sweep's tasks become search only, short and even. `perm_p` gates nothing and is not in the registry. | the code: `CAS.optimise_strategy(...)` inside `for _k in range(_npm)` at acceptance in `laz_mode3___sweep_one_base`; the 17-minute tail | Opus designs (and first proves `REG` is read-only during the sweep, or snapshots it), Sonnet builds, Opus audits | D10 on etennis and basketball, **plus** the legs file's `perm_p`, `n_nulls`, `p_resolution`, `null_kind` identical for every strategy; the `luck_check_min` and `task_min` compared before/after |
| **S1** | **speed** · BaseFinder gets the run's worker count — its call site passes none, so it runs on 4 processes in an 8-worker run | basketball log: `[workers] index: 319/319 tasks · 4 procs`; BaseFinder span 5.4 min (esports ~1.5) · `BF.find(sport, books=[book], stride=_bf_stride, top=8, …)` in the Mode 3 supply | Sonnet | D10; the `basefinder` stage faster |
| **S2** | **speed** · the learning stage off the critical path — it only writes `Learning/…` ("the pool is not modified by this stage"); run it in a forked child while the sweep runs | 100 s on basketball · `laz_learning__feature_learning(...)` call in `laz_mode3__find` | Sonnet | D10 + the learning parquet identical row for row; `learning` stage ~0 on the critical path |
| **S3** | **speed** · the replay proof in parallel by match — its state is per match, so each worker replays whole matches in order and the rows are merged by index | ~3 min on basketball (`[featuregen] wrote` → `[prove_forward_only]`) · `laz_featuregen__prove` steps every tick serially | Sonnet | D10 + the per-term PASS/FAIL report identical, message for message; `replay` stage faster |
| **S4** | **speed** · dynamic dispatch in `laz_workers___fork_map`: tasks handed out as workers free up, **heaviest first** — each task's time from the last run's `Logs/laz_sweep_prof_<sport>.jsonl` (engine 7935…) — results reassembled by task index, so order and outputs are unchanged | the tail above; round-robin fixes each worker's task list in advance | Opus designs, Sonnet builds, Opus audits | D10 on etennis **and** basketball; `sweep` wall time vs `task_min / workers` (the ideal) reported |
| **S5** | **speed** · the battery's boot gate (audit360, ~6 min) and the parallel proofs run concurrently; a pass is recorded only when both finish PASS | the battery on a new engine ≈ 11 min, the boot gate alone ≈ 6 | Sonnet | the same 33 verdicts; the battery's wall time lower; the pass stamp written only on all-PASS |
| **S6** | **speed** · verification runs skip the cross-sport bundle (~2–3 min each) | **done in the kit**: launcher `SKIP_BUNDLE=1`, used by `laz_gate.sh` for L4 and for all but the last L5 invocation | — | already verified: the harness passes; the default path still builds the bundle |
| **S7** | **speed** · investigate `lookahead-warm` (100 s on basketball, in the `[profile]` line): if its result is a pure function of (frame, engine, parameters), cache it on disk keyed by all three | the `[profile]` line | Opus investigates, Sonnet builds only if pure | D10; otherwise a written finding |
| **M0** | ~~pre-existing defect, FIXED~~ · `--reset-book` never reached the cell-memory skip check (order-of-operations: the flag was recorded into shared state AFTER the check read it) — every rerun silently narrowed, 189 -> 48 tasks after 3 runs | confirmed present in the owner's own reference copy (bc39725f09f9c0d3); `laz_mode3__find`, the `PAR['reset_book']` assignment moved before the read | Sonnet, verified via static audit + harness | done — L1/L2 clean; confirm on the next real sweep: no `[memory] N cells skipped` line when `--reset-book` is passed on a frame with prior history |
| **M1** | **learning (defect)** · seed outcomes recorded as `seed<index>` but looked up by seed name — never match, so every seed is "untried" every run | `record_seed(sport, f'seed{_si}', …)` vs `order_seeds(sport, [str(n) …])` in `laz_mode3__find` | Sonnet | DECISIONS D11 |
| **M2** | **learning** · keep the seed history across `--reset-book` (the reset's purpose is the registry, not the history) | `laz_book__reset` renames `laz_seed_history.jsonl` on every run | Sonnet | D11 |
| **M3** | **learning + speed (D10)** · seed-result cache keyed by the exact inputs — unchanged seed on unchanged data reuses its exact result | the book grows every run (228 → 403) and the next sweep grows with it (684 → 857 seeds) | Opus designs, Sonnet builds, Opus audits | D11: identical registry + ledger on a repeat run, faster second sweep, full recompute on a changed frame |
| **W8** | the library gap: per sport ~76 terms with no builder and ~29 whose builder produced nothing | `<sport>_library_gap.csv` | clerk extracts the list; Sonnet builds only terms whose definition already exists (D3); the rest go to DEFERRED.md | each built term replays exactly |
| **W9** | legacy region hygiene: 50 undefined names, 26 unclosed files | S08.info, S09.info | clerk lists; Sonnet proves which are reachable from the Mode 3 path first; only those are fixed | reachable ones clean; the rest documented |
| **W10** | combinations: the 2-leg tier realised −34% vs modelled +34% (basketball) — why | the combina log lines | Opus | a written finding in FINDINGS.md; no change (D6) |
| **W11** | book strategies set aside for terms the engine cannot build (basketball 129–290, eFootball up to 1,411) | `[supply] … SET ASIDE` lines | clerk lists by term family; terms with an existing definition are built (D3), the rest listed in DEFERRED.md | the list, with what was built |

---

## 7. The game plan (autonomous)

**Phase A — baseline.** Setup (§1). The orchestrator records L1, L2, L3, L4 etennis and L4 basketball on the untouched engine; every later number is compared with these.

**Phase B0 — speed and learning first (D10, D11).** W1, then **M1, M2, M3** (the engine learns and stops repeating work), **S8, S4** (the sweep: ~87% of a run), then S1, S3, S2, S5 — every later checkpoint runs faster for it. Each proven identical on etennis and basketball with `compare` before it is merged.

**Phase B — make production faithful.** W2.0 → W2.1…n in batches → W4 → W3. After each group: L3, L4 (basketball for W2). The proven subset grows as W2 lands.

**Phase C — alignment, measured.** W6: re-measure the arming trace after Phase B and report which strategies arm on a different tick than measured (they stay non-deployable until fixed without changing any rule).

**Phase D — breadth.** S7, W8 and W11 within D3, W9, W10 (report only).

**Phase E — the three audits, then delivery.** The final whole-diff audit (§10, audit 2); L5 (§10, audit 3); `DELIVERY_REPORT.md` (D7): per sport the READY or `_PROVEN` kit to install, COMPLETE or PARTIAL. The owner installs the kits himself (D9): `install.ps1` dry run, then `-Apply`; staking small first and comparing each strategy's live results with its ledger (`evidence/mode3_<sport>.parquet` in each kit).

---

## 8. Prompts

### 8.1 Kickoff — the only prompt the owner pastes (session started as in §1)

> You are the orchestrator for the AmunEV engine hardening, running the whole job from A to Z by yourself: do not ask me anything and do not wait for me. Read CLAUDE.md, DECISIONS.md, PLAYBOOK.md and HANDOVER.md in full before anything else, then follow your agent instructions (laz-orchestrator) and PLAYBOOK §7 phase by phase: baseline, packets through laz-implementer, every packet audited by laz-auditor, checkpoints through `bash tools/laz_gate.sh`, the final whole-diff audit, the L5 delivery run, and DELIVERY_REPORT.md. Decide everything DECISIONS.md does not cover by its rule D0 and log it. Never change the doctrine (D2). Report every failure plainly in WORKLOG.md, first. When DELIVERY_REPORT.md is written, stop.

The orchestrator hands work to the subagents itself, with the prompts below.

### 8.2 Handing a packet to the implementer

> Use the laz-implementer subagent. Packet: `packets/<id>.md`. Follow CLAUDE.md and your agent instructions exactly; write `evidence/<id>.md`; report the acceptance result quoted from the command output.

### 8.3 Handing a finished packet to the auditor

> Use the laz-auditor subagent. Audit packet `<id>`: `packets/<id>.md`, `evidence/<id>.md`, the diff since the previous commit. Reproduce every RESULT yourself, try to break the change, check doctrine, and write `evidence/<id>.AUDIT.md` with PASS or FAIL.

### 8.4 The clerk (only when a command can check the output)

> Use the laz-clerk subagent. Run `<command>` and write its output verbatim to `<file>`, ending with a count computed by `<count command>`. Do not interpret anything.

---

## 9. What went wrong on 21–22 Sep, and the check that now stops each

| defect that reached the owner | stopped now by |
|---|---|
| a reused `np.errstate` context raised on every feature after the first; 1 feature built instead of ~90, silently | the battery (`prove_generative`) at L3; rule: context managers fresh per call |
| the battery blocked itself — a timings file read by literal path | S07 (CLASS 5 mirror) |
| a cross-module call by bare name blocked the gate | S06 (CLASS 3e mirror) |
| basketball's sweep took 107 minutes (200 shuffles per leg; per-candidate quantiles) | the auditor's adversarial cost check on anything in the sweep's inner loop; per-stage timings in every gate run, compared with the baseline (`compare`) |
| 165 of 175 strategies would have shipped without their seed's conditions | I6 round-trip at acceptance (S10.1–S10.3); registry refuses unparsable clauses |
| thresholds and bands written rounded (6 digits / 2 decimals) | S10.4–S10.5; the round-trip |
| a here-document inside `$( )` — bash 3.2 cannot parse it | S11 compat check; the harness (mutation-tested) |
| bash helpers overwrote the caller's kit and verdict | the harness's parent-row assertion (mutation-tested) |
| VERIFY_ONLY checked a kit against the wrong log | the harness's log-match assertion |
| the launcher replaced while a run was using it | CLAUDE.md hard rule |
| a zip named identically across many fixes let a stale download silently replace the newest engine | §0/§1: one package, one unzip-over-the-live-folder, git commit as the only version record from here on |
| `--reset-book` never reached the cell-memory skip (order-of-operations bug); every rerun narrowed the search | V2.31: the flag is recorded before the check reads it |
| a doctrine rule (settlement) overridden without asking | DECISIONS D2 (the doctrine is frozen) + the auditor's doctrine check |
| batch children rebuilt their slice from the full export | L4 on a batch sport: each batch's `[frame] … ticks · … matches` must be its slice |
| draw sports had no winner column; every stage raised | the runner's fail-fast on a NOT READY frame |

---

## 10. The three audits — all three must pass for delivery

| audit | who | what | when |
|---|---|---|---|
| **1 · machine** | the tools | L1 static audit (22 checks, including mirrors of the two gate rules that blocked real runs) + L2 launcher harness (15 assertions under bash 3.2, mutation-tested) after every edit; L3 battery (33 proofs, including audit360 and audit_full) at every checkpoint | continuously |
| **2 · independent review** | laz-auditor (Opus) | every packet re-derived from the code, rerun, attacked, doctrine-checked — and at the end the **whole diff since the baseline**, for interactions between packets | per packet, and once at the end |
| **3 · behaviour** | the gate | L4 per group and the final L5, read against the invariants (§3: settlement line, round-trip rejections, replay, trace N/N, kit completeness) and **compared with the baseline** runs (`compare`: strategies, legs, replay, trace, runtime, ledgers row for row where D4 applies) | per group, and the delivery run |

Nothing is reported as done on the strength of fewer than all three.
