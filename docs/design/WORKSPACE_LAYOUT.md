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

### Per sport, all the way down — and what that actually requires

The owner's correction, which is right: each sport follows a different cascade and a
different path, so a single blended map distorts the answer. If the tool reports that an
element reaches the final decision and that is only true for basketball, someone reading it
while working on etennis has been misled. Cascade ORDER, REACHABILITY and the
UNREACHABLE/DECISION_IRRELEVANT findings all genuinely differ by sport.

Copying one blended map into seven files does not fix that — it is the same distorted data
in seven places. So the sport becomes a parameter of the ANALYSIS, not of the filing:

```
metatron track --sport basketball     # analysed AS basketball
metatron track --all-sports           # one pass per sport
```

Each pass constrains the graph to that sport's entry point and that sport's configuration,
so `basketball_history.json` and `etennis_history.json` hold different orders, different
reachability and different findings — because they were computed differently, not because
they were filed differently.

**What is shared and what is not.** Elements and their content hashes are properties of the
source text and do not vary by sport; they stay in the script-level files, once. Everything
derived from a path through the code — order, reachability, decision relevance, slices, the
findings that depend on them — is computed per sport and stored per sport. A sport's file
references the shared elements by id rather than copying them.

**The honesty rule, which is the whole point of the owner's objection.** Static analysis can
separate the sports only where the selection is statically resolvable: a sport-named module,
a registry keyed by a literal, a config string the owner passes with `--config`. Where the
sport is chosen by a runtime value the tool cannot follow, it CANNOT produce a genuinely
sport-specific map — and in that case the file must say so:

    scope: UNION ACROSS ALL SPORTS
    reason: the sport is selected at runtime from argv; no static rule separates the
            branches. Order and reachability below are the union over every sport.
            Run mode 2 for this sport to get its real path.

It must never present blended data as sport-specific. That is exactly the credibility the
owner is protecting, and the rule is: separate it where it can be separated, and say plainly
where it cannot. Mode 2 resolves what Mode 1 cannot, because it watches one sport actually
run.

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
