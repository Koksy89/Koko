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
