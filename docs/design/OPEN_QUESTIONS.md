# OPEN_QUESTIONS

Questions blocking progress, and decisions already settled. Nothing settled here is
re-opened without a new entry saying why.

## Open — owner

**Q1. What is the final decision sink?**
Which function returns, or which variable holds, the engine's final decision? Cards 3,
4, 5 and 6 all grade relevance by reachability to this point. Without it the map still
builds, but "which elements drive the final decision" — the project's stated purpose —
cannot be answered. _Blocks: 3, 4, 5, 6._

**Q2. What is the entry point?**
The project memory gives `run_m5.py:main` as an example. Is that the real launcher, and
is there more than one? _Blocks: 3._

**Q3. Which config files wire components?**
Paths inside the engine whose contents name classes or functions by string. Card 2
resolves those strings to elements; anything not listed is found only by heuristic, at
lower confidence. _Degrades rather than blocks: 2._

**Q4. Mode A scenarios and external systems.**
Not needed for Mode B. Needed before card 11 starts. _Blocks: 11, 12._

## Settled

**S4. Element ID scheme — was Q5.**
`module::qualname[#n]`, with `#n` only on a genuine duplicate and only from the second
occurrence onward, so a later redefinition never renames the first. No line numbers or
offsets: a reformat must not change an ID, or card 6 cannot diff and card 12 cannot key
runtime events onto the graph. Separate namespaces for features, files, config keys and
blobs. An inseparable collision is an `ID_COLLISION` record, never a silent overwrite.
See `make_id` in the contracts. _2026-09-21._

**S5. Confidence is an ordered enum composed by minimum — was Q6.**
`CERTAIN > RESOLVED > PROBABLE > HEURISTIC > UNKNOWN`. An enum because thirteen builders
applying five named levels stay consistent where thirteen builders inventing floats do
not. Minimum because it is the only rule that cannot launder a guess into a fact. See
`combine`. _2026-09-21._

**S6. Artifacts are sorted JSON Lines through one serializer — was Q7.**
`canonical_dumps` / `canonical_jsonl`: sorted keys, sorted lines, ASCII, no insignificant
whitespace. Determinism becomes a property of the serializer rather than of each
builder's diligence. Floats are rejected outright — their repr varies across platforms
and constraint 4 cannot survive it. Timestamps and absolute paths live in `run_meta.json`,
outside the byte-identical guarantee. _2026-09-21._

**S7. schema.json is generated from interfaces.py.**
One source of truth. `python -m cascade_map.contracts._generate_schema` regenerates it,
and `tests/test_contracts.py` fails if the committed file is stale. _2026-09-21._

**S1. The guard hook denies unknown command heads that name a protected path.**
Rather than enumerating dangerous commands, the guard keeps a read-only allowlist and
denies everything else that names a protected path. This produces occasional false
positives for read-only tools not yet on the list. Accepted: the fix is one line in
READ_ONLY, and a builder must report it rather than route around the guard. _2026-09-21._

**S2. The verifier has no write tools.**
It is configured with Read, Bash, Grep and Glob, and no Write or Edit. It cannot patch
what it is judging, so a PASS means the code passed as written. _2026-09-21._

**S3. Heredoc bodies are data, not commands.**
The guard strips heredoc bodies before parsing. The redirect on the introducing line is
still checked, so a heredoc written into a protected path is still denied. Found by the
guard blocking the write of this very file. _2026-09-21._
