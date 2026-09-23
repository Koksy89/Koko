# Workspace layout — one folder per script, growing with its versions

Owner's ask: `/Users/usenememacbookpro/Metatron_Engine` becomes the root. Each script gets
its own folder that accumulates every version and every run, so all the history of that
script lives in one place and Metatron can pick up its whole development story from a file
rather than re-deriving it.

---

## The layout

```
Metatron_Engine/                                  <- WORKSPACE. The only path in settings.
    AmunEV_Engine_V2/                             <- one folder per script. Name = PROJECT.
        history/
            AmunEV_Engine_V2_history.json         <- the static story. Open this one.
            AmunEV_Engine_V2_fingerprints.jsonl
            AmunEV_Engine_V2_comparisons.jsonl
            runtime/
                etennis_history.json              <- one per sport. Mode 2 only.
                basketball_history.json
                ebasketball_history.json
        sources/
            a3f19c4e.../                          <- stored ONCE per distinct content
        io/
            runs/2026-09-23T14-02-11Z/            <- the map from that run
                elements.jsonl  edges.jsonl  ...  blueprint.html
            reports/
    LazarusRunner/
        ...
```

Every file carries its script's name, so nothing is ambiguous when a file is copied out,
attached to a message, or opened months later. `PROJECT` is derived from the script being
analysed, so naming the script is the whole instruction — the tool resolves the paths
itself and there is nothing to point at and nothing to mis-point.

### Per sport: runtime only, and here is why

The owner asked for `etennis_history.json`, `ebasketball_history.json` and so on, to avoid
combing through data irrelevant to the run in hand. That is right for runtime and wrong for
the static map, so the split follows the data rather than the folder.

**The static map does not depend on the sport.** Mode 1 never runs anything, so it produces
one map of the whole engine — every sport's code at once. Writing it per sport would store
seven identical copies, make a change appear seven times, and give the owner seven places to
look for one answer. Worse, it would imply the tool knows which code belongs to which sport,
which statically it does not: a sport chosen by a runtime string is exactly the link Mode 1
reports as HEURISTIC rather than claiming.

**Runtime data is entirely per sport.** A Mode 2 run traces one sport. Its events, values,
contradictions and observed order belong to that sport and to no other, and comparing
basketball against basketball across versions is the only comparison that means anything.
So each sport gets its own runtime history, read only when that sport is in play.

That gives the speed gain the owner is after, where the gain is real, without duplicating
the static map seven times.

## Why not literally one JSON file

The owner said "a json file or whatever file format you find optimal", so this is the
choice and the reasoning, stated rather than assumed.

A single JSON holding everything would work for three versions and then stop working. Their
engine is 14.8 MB and will hold on the order of 50,000 elements. One fingerprint per element
per version is 50,000 lines per version; twenty versions is a million. As one JSON that is
hundreds of megabytes which must be **fully parsed to read one field and fully rewritten to
append one row** — the file gets slower exactly as the history gets more valuable.

So the split is by access pattern, not by taste:

| File | Format | Why |
|---|---|---|
| `history.json` | JSON | The story: every version, when, from which signal, its counts, its headline comparison. Stays in the low hundreds of KB even after a hundred versions. This is the file to open, to read, to send, to keep. |
| `fingerprints.jsonl` | JSON Lines | One element, one line. Appending a version appends lines; nothing is rewritten. Streams without loading the whole history into memory. `grep`-able. |
| `comparisons.jsonl` | JSON Lines | Same reason, one line per pair. |

JSON Lines *is* JSON — one object per line. Anything that reads JSON reads it a line at a
time. Nothing is locked away in a private format.

## Sources are stored once, not once per run

A version is identified by the content hash of its tree. The snapshot lands in
`sources/<hash>/` and a re-run of unchanged code adds **nothing**: same hash, same folder,
already there. Re-analysis is skipped for the same reason.

This matters at their scale. Storing a 14.8 MB engine on every run would be ~740 MB after
fifty runs. Storing it once per *distinct content* means it costs 14.8 MB per real change
and nothing at all for a re-run. The owner should still know the number:
**the history file reports the workspace's disk use and what the sources cost**, so it never
grows in silence. `--no-sources` records hashes without keeping the copies, for anyone who
would rather rely on their own version control.

## What a run records

Every run appends to `<PROJECT>_history.json`:

* the version's id (tree hash), its label, its timestamp **and which signal that came from**
  — filename, git, or file mtime, never a guess presented as fact;
* counts: elements, edges, lineage edges, decisions, findings, unresolved, barriers;
* the confidence census — how much of that map is fact and how much is inference;
* per-stage timings, in whole milliseconds, measuring **this tool**, not the engine;
* the Mode A run ids recorded against it, per sport;
* the comparison against its predecessor: what changed, what it touched, and how many of
  those changes move a path to a decision.

## Reading the story back

```
metatron history                                  # every project, newest first
metatron history AmunEV_Engine_V2                 # one script's full development
metatron history AmunEV_Engine_V2 --sport etennis # that sport's runs only
metatron history AmunEV_Engine_V2 --element amun.features::rsi
```

The last one is the point of the whole design: one element's life across every version —
when it appeared, every time its body changed, when it stopped or started reaching a
decision, and which run first observed it executing. That is answerable only because every
version's fingerprints were kept.

## Migration

An existing flat `VERSIONS_DIR` is not abandoned. `metatron migrate` moves it into the new
shape, reusing the version ids it already computed, so no history is lost and nothing is
re-analysed.

## Mode from the terminal

`--mode 1` and `--mode 2` on every command that runs anything — `track`, `analyze`,
`trace` — overriding `MODE` in settings without opening the file. `--mode 2` on a target
with no completed static map refuses and says to run mode 1 first, rather than starting a
run it cannot key to anything.
