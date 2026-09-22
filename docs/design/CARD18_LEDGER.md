# Card 18 — METATRON_SETTINGS and the version ledger

Owner-facing goal, in their words: drop each new version of the engine into a folder,
have the tool work out the order, run only what it has not already run, and keep a JSON
history that lets it compare **every single element** between versions without ever
re-analysing an old one.

---

## 1. `METATRON_SETTINGS`

A plain dict at the top of the single file, edited in place. No CLI flags needed for the
common case; flags still override it so scripting stays possible.

```python
METATRON_SETTINGS = {
    # 1 = static only (never executes your engine).
    # 2 = static + runtime tracing inside the harness. 2 always includes 1:
    #     Mode A refuses to start without a completed static map.
    "MODE": 1,

    # The folder holding your versions. One sub-folder per version.
    #   versions/
    #     amun_2026-01-14/        <- a whole engine tree
    #     amun_2026-02-03/
    #     amun_v3/
    "VERSIONS_DIR": "versions",

    # Where maps are written. One sub-directory per version, named by its id.
    "OUT_DIR": "out",

    # The history file. Created on first run, appended to for ever after.
    "LEDGER": "out/metatron_ledger.json",

    # Your final-decision element(s). The single highest-value setting here:
    # everything the tool says about "what drives the decision" is measured
    # against these. Left empty, it guesses by name and labels the guess.
    "SINKS": [],

    # Entry point(s). Detected when empty.
    "ENTRIES": [],

    # Config files that wire components by name, relative to each version root.
    "CONFIGS": [],

    # Card 17: the interpreter whose installed packages to read, as TEXT.
    # Nothing here is ever imported or executed.
    "ENV": ".venv-target",

    # Explicit ordering, for when the tool cannot establish it from the files.
    # Folder names, oldest first. Empty = work it out and report how.
    "ORDER": [],

    # MODE 2 only.
    "SCENARIOS": "scenarios.json",
    "SCENARIO": "baseline",
}
```

**Deliberately absent: worker/core count.** The owner asked whether to set it or let the
code decide. Let the code decide: ingestion parallelises across files, and the right
degree is `os.cpu_count()` minus a little headroom, which the machine knows and a config
file cannot. A fixed number in settings would be wrong on every machine but the one it
was written on. If an override is ever wanted it is one key and one line — say the word.

**Settings are data, not code.** The tool must never `exec` a settings file, and must
validate every key: an unknown key is an error naming the typo, not a silent no-op. A
misspelled `"SINK"` that does nothing is how an owner ends up trusting a map built
without the setting they thought they had applied.

---

## 2. Version discovery, and the honest bit about timestamps

`VERSIONS_DIR` is scanned for sub-directories. Each one is a version.

**Identity is the content hash of the tree, never the folder name and never a
timestamp.** Rename a folder and it is the same version; change one byte and it is a new
one. This is what makes "never re-run what you have already run" reliable.

**Ordering is best-effort and must say which signal it used.** The owner asked the tool
to "intuitively identify creation date and time", and it will try, in this order:

1. a date parsed out of the folder name (`amun_2026-02-03`) — often the only honest
   signal, because it was written by a human on purpose;
2. a git commit date, when the version is a checkout;
3. the newest file mtime in the tree;
4. the directory mtime.

Every `VersionRecord` records `version_time_source` saying which of these it used.

This matters because **filesystem timestamps lie.** Copying a tree rewrites mtime. Some
filesystems have no true creation time at all. Extracting an archive stamps every file
with the moment you unpacked it — so three versions unzipped in one sitting look
simultaneous. Where the tool cannot establish an order it sets `ordinal = -1`, says so
loudly, and asks the owner to fill in `ORDER`. It never presents a guessed history as
fact: a comparison run against the wrong "previous version" produces a confident,
detailed, completely wrong answer, and that is worse than no answer.

---

## 3. The ledger

One JSON file. An **index, not a copy**: it holds a fingerprint per element per version —
id, kind, content hash, normalised body hash, signature, span, reachability, confidence —
which is everything a comparison needs, so no old version is ever re-read.

```
out/metatron_ledger.json
  schema_version, tool_version
  versions[]      one VersionRecord each: id, label, time + how it was obtained,
                  ordinal, counts, confidence census, per-stage seconds, artifact dir,
                  Mode A run ids
  fingerprints[]  one ElementFingerprint per element per version
  comparisons[]   one VersionComparison per consecutive pair
```

Full artifacts stay on disk under `OUT_DIR/<version id>/`; the ledger points at them.

**Adding a version is incremental.** Hash each tree, skip every id already present,
analyse only the new ones, compare the new one against its predecessor, append. A second
run with nothing new added does no analysis at all and says so.

---

## 4. "Every single element, A to Z" — made checkable

The claim is easy to make and easy to fake, so `VersionComparison` carries the proof:

```
elements_before, elements_after, elements_accounted_for, unaccounted_element_ids
```

Every element in either version must land in exactly one classification — `ADDED`,
`REMOVED`, `RENAMED`, `MOVED`, `SIGNATURE_CHANGED`, `BODY_CHANGED`,
`DECORATORS_CHANGED`, `UNCHANGED` or `AMBIGUOUS`. Anything that does not is **named** in
`unaccounted_element_ids`, and a comparison naming any is reported as incomplete.

`AMBIGUOUS` stays a first-class answer. Where two candidate matches are equally good, both
are listed. Picking one and sounding certain is how a rename gets reported as an unrelated
deletion plus an unrelated addition, and the owner goes hunting a change nobody made.

A reformat must not read as a hundred behaviour changes: that is what
`normalized_body_hash` is for. Same bytes and same logic are different questions and both
are stored.

---

## 5. Performance — what can honestly be measured

The owner asked for "impact on performance". Two different things, and conflating them
would be the most damaging error this card could make.

**What the static map CAN measure:** how long *this tool* took, per stage, per version.
That is a real signal about the target's size and shape — a version that doubles the
analysis time has grown or tangled — but it is a fact about Metatron, not about the
engine's speed. It is stored in `stage_seconds` and its docstring says exactly this.

**What the static map CANNOT measure:** how fast the engine runs. Nothing that refuses to
execute code can time it. A field implying otherwise would be worse than no field.

**Where engine performance actually comes from:** MODE 2. A Mode A run observes real
execution and records per-element timing, so `runtime_delta` compares two versions that
were both traced under a comparable scenario. When both versions do not have such a run,
`runtime_delta` is empty — and empty must be reported as **"not measured"**, never
rendered as "no change". That distinction is this project's most repeated defect class,
four occurrences and counting.

---

## 6. `metatron track`

One command. Reads `METATRON_SETTINGS`, discovers versions, analyses what is new,
compares consecutive pairs, updates the ledger, prints what it did and what it skipped.

```
$ python3 metatron_engine.py track

Ledger out/metatron_ledger.json — 4 versions known, 1 new

  amun_2026-02-03   NEW   analysed in 22.4s   (time from folder name)
  amun_2026-01-14         known, not re-run
  amun_v3                 known, not re-run
  amun_v2                 known, not re-run

amun_2026-01-14 -> amun_2026-02-03
  1,284 elements before, 1,301 after, 1,301 accounted for, 0 unaccounted
  changed        47   (12 BODY_CHANGED, 17 ADDED, 3 REMOVED, 2 RENAMED, 13 MOVED)
  ambiguous       2   <- two equally good matches, both listed, neither claimed
  decision paths  6   <- changes that move a route to your final decision
  reachability    2 flipped
  findings       +3 / -1
  runtime         not measured (MODE 1; set MODE 2 to observe execution)
```

`track --report` writes the whole history; `blueprint --diff` paints any pair on the
canvas.
