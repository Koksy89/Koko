# ARCHITECTURE

How CASCADE-MAP is built. `src/cascade_map/contracts/` is binding; this document
explains it. Where the two disagree, the contracts win and this document is the defect.

## Shape

One pipeline, two evidence sources, one graph.

    target_engine/  ──► card 1 inventory ──► card 2 resolution ──┬─► card 3 order + decisions
                                                                 └─► card 4 lineage + slices
                                                                        │
                                          cards 5, 6 findings & diff ◄───┤
                                                                        │
                                          card 16 records + gate ◄───────┤
                                                                        ▼
                                                                  card 15 viewer
                                                                        ▲
    card 11 harness ──► card 12 tracer ──► cards 13, 14 alignment & narrative

Mode A never replaces Mode B. Card 12 emits an **overlay** keyed by the same IDs card 1
minted, and where observation contradicts a static claim the contradiction is a record,
not an edit. The static graph is never rewritten to match what a run happened to do.

## The three decisions everything rests on

### Identity is structural (Q5)

    module::qualname[#n]

`strategy.rules::RuleSet.evaluate`. No line numbers, no offsets, no content hash in the
ID. Reformat a file and every element keeps its ID — which is the only reason card 6 can
diff two versions and card 12 can key a runtime event onto a static element.

The `#n` suffix appears only on a genuine duplicate — a conditional redefinition, a name
bound in two branches — and only from the second occurrence onward, so adding a
redefinition later never renames the first. A collision the scheme cannot separate is an
`ID_COLLISION` unresolved record, never a silent overwrite.

Four namespaces sit beside code IDs so they cannot collide: `@feature:<name>`,
`@file:<path>`, `@file:<path>::<json-pointer>`, and `<module>::@blob#n`.

Features get their own namespace because the owner reasons in features. A column named
in a config file and a column written by a function are one feature and must land on one
node, or the lineage answer is wrong in the exact place it matters.

### Confidence is an ordered enum, composed by minimum (Q6)

`CERTAIN > RESOLVED > PROBABLE > HEURISTIC > UNKNOWN`, and a derived fact takes the
**weakest** of its inputs (`combine`).

An enum, because thirteen builders applying five named levels stay consistent while
thirteen builders inventing floats do not. Minimum, because it is the only composition
rule that cannot launder a guess into a fact: a cascade order resting on one HEURISTIC
call edge is a HEURISTIC ordering, however certain every other step was.

`UNKNOWN` is not a low score. It means nothing is being claimed, and it propagates.

### Artifacts are sorted JSON Lines (Q7)

One record per line, sorted by ID, keys sorted within each record, ASCII-escaped, no
insignificant whitespace, `\n` endings — all of it through one function,
`canonical_dumps`. Determinism is a property of the serializer, not of each builder
remembering to sort.

JSON Lines rather than one document because the target is ~116k lines: records stream
instead of loading whole, and a line-oriented format makes both the determinism check
(`diff` two runs) and version diffing readable.

**Floats are rejected**, not formatted. Their repr varies across platforms and constraint
4 cannot survive that. Emit an integer, or a string you formatted and can explain.

Timestamps, absolute paths, durations and hostnames live in `run_meta.json`, which is
explicitly outside the byte-identical guarantee. Nothing else may carry them.

## Output layout

    out/<label>/
      manifest.json         schema version, tool version, target content hashes,
                            and a content hash per artifact file
      run_meta.json         timestamps and environment — NOT byte-compared
      elements.jsonl        card 1
      unresolved.jsonl      cards 1-4, appended to by whoever cannot resolve something
      edges.jsonl           card 2
      cfg_blocks.jsonl      card 3
      cfg_edges.jsonl       card 3
      order.jsonl           card 3
      decisions.jsonl       card 3
      lineage.jsonl         card 4
      barriers.jsonl        card 4
      slices.jsonl          card 4
      findings.jsonl        card 5
      changes.jsonl         card 6
      impacts.jsonl         card 6
      records.jsonl         card 16
      intents.jsonl         card 13
      runtime/<run_id>/
        run.json            card 11
        events.jsonl        card 12
        verdicts.jsonl      card 13
        narrative.jsonl     card 14

`schema.json` names every file, its record type and its sort key, and is **generated**
from `interfaces.py` so the two cannot drift. Regenerate with
`python -m cascade_map.contracts._generate_schema`; a test fails if it is stale.

## Incrementality

Keyed on file content hash. An unchanged file is not re-parsed. The cache stores the
hash with the records derived from it, so a stale record cannot survive a change: the
key *is* the content.

A cold run and a warm run must produce identical output. That is a test, not an
aspiration — it is the only way to know the cache is not quietly serving stale data.

## Safety

Constraint 1 is enforced in three places, on purpose:

1. **Design** — cards 1-6 and 15-16 use `ast` and never `import`. No card has a code path
   that executes a target file.
2. **The guard hook** — `.claude/hooks/guard_engine.py` denies execution and writes at
   the tool boundary, before a process starts. A tripwire, not a sandbox.
3. **The harness** — card 11 controls the process, its filesystem and its network. This
   is the actual isolation guarantee, and it refuses to start when it cannot make it.

The sentinel fixture proves the first layer empirically on every static run.

### What layer 3 does not cover

Card 11's isolation is built on `sys.audit` (PEP 578), and two paths fire no audit
event at all. Both were demonstrated, not theorised:

1. **A direct call to the interpreter's low-level process-spawn primitive**, bypassing
   `subprocess`. The child runs with no control attached and nothing recorded. Gating the
   module's import does not work: `subprocess.py` imports it unconditionally, so the gate
   would also break the declared-executable path that does work.
2. **A process permitted through `declared_process_names`**, once it is running. It is a
   separate program with none of the harness's controls attached, in both directions.

Constraint 7 says a run refuses when the guarantee cannot be made. Refusing every run
over a limit no run can avoid would make Mode A unusable, so the honest form here is
disclosure: `RunRecord.unguaranteed` names each uncovered path in plain language, and it
travels with every run record — successful and refused alike. An empty tuple is a claim
of complete coverage and must never be the default for a limit that is merely unmeasured.

**Closing these needs isolation underneath the harness** — a container, a sandboxed OS
user, seccomp, or namespaces. That is outside this tool. Before Mode A is ever pointed at
a real engine, run it inside one.

## Auto-detection

Where `TARGET_PROFILE.md` leaves an owner input blank, the affected card detects
candidates **read-only** and reports them with evidence and a confidence. It never
silently picks one.

A missing decision sink degrades the map rather than stopping it: reachability becomes
`UNKNOWN` everywhere instead of wrong everywhere. That distinction is the whole design
principle here — an honest gap beats a confident error, because the owner can act on a
gap.

## Dependencies

stdlib + `networkx`. Everything else is an optional adapter behind a feature check, and
the tool degrades to a usable form without it. Model calls (cards 13, 16) read
`CASCADE_MAP_API_KEY`, are optional, and are fully disabled when no key is set — with
every other part of those cards still working and still tested.
