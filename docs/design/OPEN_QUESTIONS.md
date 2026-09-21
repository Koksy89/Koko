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

## Open — lead

**Q5. Element ID scheme.**
Card 1 mints the IDs every other card keys off. The scheme must survive reformatting and
unrelated edits in the same file, and must disambiguate redefinitions, overloads and
nested scopes. To be settled in ARCHITECTURE.md before card 1 starts. _Blocks: 1._

**Q6. Confidence scale.**
Every fact carries a confidence. An enum (EXACT / PROBABLE / HEURISTIC) is easier to keep
consistent across 13 builders; a number composes better along a chain of inferences.
To be settled in the contracts before card 2. _Blocks: 2._

**Q7. Artifact format.**
What cards 1-6 write to disk and card 15 reads back. Must be deterministic with sorted
keys. To be settled in schema.json before card 1. _Blocks: 1._

## Settled

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
