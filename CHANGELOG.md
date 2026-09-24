# CHANGELOG

Every shipped change bumps the version in `VERSION`, and every run records that
version in its history file and `run_meta.json`. So a map can always say which
Metatron produced it — and a change in the ledger can be attributed to the
engine or to the tool, never confused between them.

That distinction is not academic. Version 1.0.0 contains a fix to the
"same logic?" fingerprint which changed every one of those hashes exactly once.
Without a tool version in the record, a ledger spanning that change would have
reported the owner's entire engine as rewritten, with nothing to say otherwise.

Versions before 1.0.0 all reported `0.0.0` and cannot be told apart.

---

## 1.1.0 — read once, share, consolidate

Measured by the lead on the owner's real 14.6 MB engine, 4-core box, idle,
`--workers 4`: **cold 288.4s, warm 264.5s and 268.6s**. Two warm runs produced
21 of 21 artifacts byte-identical. Every number here was run, not estimated.

**Parse once instead of five times.** Five stages each called `ast.parse` on
the same target source. On the owner's engine one parse is 20.79s, 895,811
nodes, 0.62 GB — so four redundant parses were roughly a quarter of the run.
A new `ParseCache` holds one tree per (path, content-hash) under a byte
budget, and the run reports what it did: *4 asks, 1 parse, 3 reused*.

The risk was never speed, it was silent corruption: a shared tree is only safe
if no stage writes to it. `strict=True` fingerprints every tree at INSERT,
before any consumer sees it, and re-checks on each hand-out — so a mutation by
the FIRST stage is caught too. The fingerprint covers rewritten fields, moved
nodes, changed positions, and attributes *attached* to a node (a parent
pointer appears in `__dict__` and in no `ast.dump`). Audited and executed
against real code: 8,348 elements through resolve, cascade and lineage on one
strict cache, 53 parses serving 156 asks, no mutation.

Peak memory **fell 287 MB**. One tree held is cheaper than three overlapping
parse peaks.

Incomplete by design: ingestion runs first so it can only seed the cache, and
the dependency scan is pickled to a second process where a shared tree cannot
reach. A **cold** run therefore still pays for 2 parses; only warm runs see
the whole win.

**Work-stealing pool across every stage**, not just file reading — one shared
queue, an idle worker takes the next unit immediately. Artifacts are
byte-identical at 1, 4 and 8 workers (21 artifacts / 427 MB, compared rather
than asserted), because the unit list is fixed before any worker starts and
results are consolidated in submission order.

Worth stating plainly: this bought **1.17x**, not the leap hoped for. About
96% of the run is sequential by nature. Four further parallelisations were
measured and **rejected** rather than shipped as wins — the CFG pool alone ran
37.9s → 86.2s, because each worker re-parses a 15 MB module and ships back
75 MB. Each rejection states its reason in the run report instead of being
quietly absent. More workers than cores cannot help: 8 workers on 4 cores
simply queue.

**Intent alignment and external-client stubs wired through.** `--intents`,
`--propose-intents`, `intents.jsonl`, `verdicts.jsonl`, `coverage.json`, and
harness stubs in blocked / record / replay modes.

**A silent-wrongness fix, found by that wiring.** Re-reading an artifact did
not restore enum fields, so a stored `REACHES_SINK` came back as plain text
while the code asks `state is ReachabilityState.REACHES_SINK` — always false.
**Mode A was reporting live elements as unreachable, with no error anywhere.**
Fixed across every enum field, nested ones included; an unrecognised value is
left alone so a newer artifact degrades instead of crashing.

**A scaling test that flapped in both directions.** It timed a ~25 ms
operation once against an 8.0x threshold; the same unchanged code measured
0.025s to 0.111s across runs, so the ratio swung 4.6x to 8.9x and failed about
half the time. It now takes the fastest of seven runs — minimum, not mean,
because noise can only add time. Quieter *and* stricter: a true quadratic
costs 16x in every sample including the fastest.

Suite: **1951 tests, 0 failures.** `dist/cascade_map.py` rebuilds byte-identical
and produces output identical to the package.

---

## 1.0.0 — first versioned release

The first version an owner ran against a real engine: 14.8 MB, 15,126 elements,
2m46s.

**Speed.** Five quadratic defects removed, all the same shape — an O(n) call
inside a loop that already ran once per element. Measured on a 14.6 MB single
file: ingestion 202.65s → 1.46s (139x), cascade 2944s → 60s (49x),
documentation records 778s → 6.7s (117x). A full analyze went from roughly 50
minutes to under 5. Every artifact byte-identical before and after — these were
redundant work removed, not answers changed.

**Correctness.** `normalized_body_hash` was interpreter-version dependent: the
same source produced different digests on Python 3.11 and 3.12, because it
hashed CPython's internal token numbers. Any ledger spanning a Python upgrade
would have reported the whole engine as rewritten. Now hashes token names, with
a test that runs under all three interpreters and fails if they disagree.

**Honesty.** Six commands refused to fail: `blueprint` wrote a page for a
directory that did not exist and reported success. They now refuse, with four
distinct messages, and write nothing. A sport-specific view whose sport cannot
be statically separated declares `scope: UNION ACROSS ALL SPORTS` with the
reason rather than presenting blended data as that sport's. Slices carry the
scope they were emitted under, so a file copied out of its workspace can still
say what produced it.

**Features.** Per-script workspace with named history files and per-sport runtime
history; version ledger that never re-analyses a version it has already seen;
sports selectable from the terminal; package-version checking across declared,
installed and used; the blueprint canvas with three tabs and four palettes;
live progress with timestamps and per-stage timings; `doctor`; `history`;
`migrate`.

**Known gaps.** Workers parallelise file parsing only; resolve, cascade,
lineage and records are still single-threaded, and lineage is 68% of a warm-cache
run. DECISION slice scope does not bound a real engine — 6.4 GB on the owner's
first run — and is being fixed. Intent alignment and external client stubbing
are built and tested but not reachable from the command line.
