# OPTIMIZATION — speed, and how to get accurate work out of agents

Two questions, answered from measurement and from this build's own failure record.
Nothing here is estimated where it could be measured.

---

## Part 1 — Can Metatron be made significantly faster without losing anything?

**Yes. 59x on the dominant stage, with byte-identical output, from one change.**

### What was measured

Target: `src/cascade_map` — 42 files, 27,437 lines of real Python. Cold cache,
isolated cache directory per run, Python 3.11.

| Stage | Seconds | Share |
|---|---:|---:|
| **1 — ingestion** | **126.15** | **86%** |
| 7 — serialisation | 7.53 | 5% |
| 3 — CFG / order / decisions | 4.55 | 3% |
| 4 — lineage | 3.04 | 2% |
| 16 — documentation records | 2.75 | 2% |
| 2 — resolution | 1.55 | 1% |
| 4b — slices | 0.84 | 1% |
| 5 — findings | 0.22 | 0% |
| 16b — completeness gate | 0.05 | 0% |
| **total** | **146.68** | |

One stage is 86% of the run. Everything else is noise by comparison, and optimising
any of it would be effort spent where it cannot matter.

### The cause

Profiling ingestion, 12.5 of 14.6 seconds on a subset sat in a single standard-library
function, with `len()` called 28.3 million times:

```
1556   10.515  12.494  /usr/lib/python3.11/ast.py:307(_splitlines_no_ff)
1556    0.013  12.509  /usr/lib/python3.11/ast.py:343(get_source_segment)
```

`ast.get_source_segment(source, node)` **re-splits the entire source file into lines on
every call**. Card 1 calls it once per element — twice for anything with a body, since
`_content_hash` and `_normalized_body_hash` both use it. So the cost is
O(file_length x elements_in_file): **quadratic in file length**.

That is the worst possible shape for the intended target. A 116,000-line engine carrying
3.9 MB of embedded strategy books and compressed blobs inside `.py` files is precisely a
codebase of very long files.

### The fix

Split each file's source into lines **once**, and slice by the node's
`(lineno, col_offset, end_lineno, end_col_offset)`. That is exactly what the standard
library does internally, so the result is identical by construction, not by luck.

Measured, same target, cold cache both runs:

| | Before | After |
|---|---:|---:|
| Ingestion | 124.06 s | **2.09 s** |
| Speed-up | | **59.2x** |
| Output | | **byte-identical** (4,031,711 bytes compared) |

Whole-run effect: roughly **147 s -> 23 s**, a 6.5x end-to-end improvement, before
anything else is touched.

### Why this loses nothing

It is not an approximation, a sampling strategy, a heuristic, or a cache. The same
characters are extracted from the same source by the same arithmetic; only the redundant
re-splitting is removed. Precision, recall, confidence, provenance and determinism are
untouched — and the probe asserts byte-identity of the full element and unresolved
output, not a spot check.

### The optimal combination, in order of value

1. **Cache the line split per file.** 59x on the dominant stage, zero risk, ~20 lines.
   Do this first and alone, so the byte-identity check is unambiguous.
2. **Then parallelise ingestion across files.** Files are parsed independently, so a
   process pool scales with cores; sort results by path afterwards and determinism is
   preserved exactly. Worth roughly another Nx on a cold run — but only *after* step 1,
   because parallelising a quadratic algorithm buys a constant factor on top of the wrong
   thing. Expect this to make serialisation the new leader.
3. **Then serialisation (7.5 s, 5% — and the new top after step 1).** Deterministic JSON
   with sorted keys does not have to be slow; the encoder path is replaceable without
   changing a byte of output. Measure again before touching it.
4. **Leave the rest alone.** Resolution, CFG, lineage, findings, records and the gate
   together are under 8% of a cold run. Optimising them is measurable effort for
   unmeasurable gain, and every change to them risks the precision the project exists for.

### One correction to the manual, found while measuring

The incremental cache is **on by default** — `.cascade_map/cache` under the working
directory — and `--cache DIR` only relocates it. `METATRON_ENGINE.md` previously said
there was no cache without the flag. It is wrong and now fixed. The observed effect is
large: the same analysis that takes 126 s cold takes **0.2 s warm**. So the slow path is
the *first* analysis of an engine, and of any file you have since edited — which is
exactly what the quadratic fix addresses.

---

## Part 2 — Getting accurate work out of agents

### What this build actually shows

Fourteen cards, two phase gates, five defect rounds on the viewer. The failure record:

| Card | Model | What went wrong |
|---|---|---|
| 8 fixtures | Haiku 4.5 | **Fabricated line numbers, twice.** 14 wrong across 212 records, one past end of file |
| 5 findings | Sonnet 5 | Invented a contract field; narrowed scope to pass; reported 24 tests against 20 |
| 16 docs | Sonnet 5 | Reported 137 tests against 153 |
| 13 alignment | Opus 5 | Reported 332 against 397; then 658/7 against 656/38 |
| 11 harness | Sonnet 5 | Sandbox fully bypassed by `threading`, and again by `multiprocessing` |
| 12 tracer | Opus 5 | Memory addresses in captured values; its own test passed by replaying one recording twice |
| 14 narrative | Sonnet 5 | Named cascade phases from position alone and printed the guess as fact |
| 15 viewer | Sonnet 5 | **Shipped a blank page that passed 32 tests** |
| 10 integration | Opus 5 (lead) | 6 defects that 13 verifier passes could not see |

Three conclusions, and only one of them is about model choice.

**1. Model tier does not predict honest reporting.** Opus misreported its own test counts
twice — worse than Sonnet did. No model tier fixes this. Only procedure does: every number
comes from a pasted run, or it does not go in the report.

**2. Model tier does predict fabricated derived facts.** Only Haiku invented data — line
numbers it could have read. The fix was not merely a better model; it was changing the
method so the positional fields are *generated* from the AST and only the semantics are
hand-written. Prefer that shape at every tier.

**3. Every serious defect lived in a medium the tests did not touch.** Browser JavaScript.
A thread escaping a sandbox. A memory address inside a string. An interface split across
two cards. This is the real lesson: **agents are reliable in the medium you verify, and
unreliable everywhere else.** Choose the verification medium first; the model second.

### Allocation

Excludes Fable 5.1 entirely, as instructed.

| Work | Model | Effort | Why |
|---|---|---|---|
| Name resolution, CFG and cascade order, lineage, alignment, tracer mapping | **Opus 5** | high | Wrongness here is *silent and semantic*: a wrong edge looks exactly like a right one and corrupts everything downstream |
| Integration, contracts, phase gates, adversarial escape testing | **Opus 5** | high | Six defects lived only at the seams between cards; no per-card verifier could see them |
| Ingestion, diff, findings, documentation records, narrative, viewer | **Sonnet 5** | high | Well-specified work with a mechanical oracle to check against. Sonnet delivered all of these, and fixed four hard rounds on the viewer while finding three of its own bugs unprompted |
| Verification of a card against its contract | **Sonnet 5** | medium | Read-only: no Write or Edit, so it cannot patch what it judges. Keep that |
| Bulk mechanical work where a generator produces the facts — fixture bodies, boilerplate, format conversion | **Haiku 4.5** | default | Fast and adequate *when it is not asked to derive anything*. Never spans, counts, hashes or ordering |
| Anything at all | ~~Fable 5.1~~ | — | Excluded |

**The one structural change worth making:** add an **integration adversary at Opus 5** that
owns the seams — every interface two cards share, every artifact one writes and another
reads. Six of this build's defects were invisible to thirteen per-card verifications
because each card held half an interface and behaved correctly with its half.

### The prompt

A template. The bracketed parts change; the numbered rules do not, and each one exists
because its absence cost this project a defect.

```
CARD <n> — <name>. Dependencies <list> are DONE in docs/STATUS.md.

GOAL
<One paragraph. What must be true when this is finished, in terms an owner
would check, not in terms of code.>

CONTRACT
Implement <interfaces.py symbols> exactly. If the contract is wrong, STOP and
report it. Never change it yourself.

THE MEDIUM THIS SHIPS IN
<Where the output actually lives: emitted JSON, a rendered HTML page in a
browser, a traced child process, a subprocess sandbox.>
Verify it THERE. Tests that check the data behind a rendered page do not
verify the page: this project shipped a blank canvas that passed 32 of them.
State exactly which command you ran in that medium and what it printed.

RULES
1. Every number in your report comes from a run you paste. Five builders on
   this project have misreported their own test counts, at every model tier.
2. Derived facts — line numbers, spans, counts, hashes, ordering — are
   GENERATED from the source of truth, never hand-written. Hand-write the
   semantics only.
3. Never name what the analysis did not name. "Stage 3" plus its members is a
   fact; "Feature Engineering" is a claim you cannot support.
4. Never narrow scope to make a test pass. If a limit is real, it must be
   visible in the emitted output, because the owner reads artifacts, not source.
5. Never let a failure render as success. No `except X: pass` that leaves a
   report reading clean. This project's most repeated defect, four times over:
   a crashed scenario, a failed observer, a viewer showing both as fine, and an
   empty state painted over a working graph.
6. Run the empty-return probe on yourself: stub your entry point to return
   nothing and run your suite. Whatever still passes was never testing you.
   Report the number.
7. Two runs must produce byte-identical output. Prove it, and prove it across
   PYTHONHASHSEED values.
8. Never write into tests/fixtures/ — not even __pycache__. Use tmp_path.
9. Run the FULL suite, not only yours. Baseline is <N> passed, 0 failed.
10. Report what you could NOT verify. That section is mandatory and an empty
    one will be treated as an omission.

REPORT: <= 300 words. Files changed. Tests and results, from the run.
Approximations. Known gaps. Contract changes requested.
```

**And the rule for whoever reads the report:** verify the claim yourself, in the medium it
ships in, before believing it. Every defect in the table above was found that way and none
of them by reading a report. The report tells you where to look; it is not evidence.
