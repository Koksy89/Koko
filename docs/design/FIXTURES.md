# FIXTURES

The corpus every card is graded against. Card 8 builds it; every other card turns the
relevant cases into pytest tests.

## Layout

    tests/fixtures/
      mode_b/<case_id>/          source files
      mode_b/<case_id>/expected.json
      mode_a/<case_id>/          source files
      mode_a/<case_id>/expected.json
      versions/<case_id>/before/ , after/ , expected.json
      sentinel/

## Rules for expectations

1. **Hand-written from reading the fixture.** Never recorded from tool output. A fixture
   that captures current behaviour tests nothing — it will "pass" against a wrong
   implementation and then lock the wrongness in.
2. **Deterministic and sorted**, so a failure diff is readable.
3. **Ambiguity is expressed as a required `Unresolved` record** — its reason, location and
   candidate set — not as a guessed answer. Several cases below exist only to prove the
   tool says "I don't know" in the right place.
4. **Small.** Each case is the smallest program that still exercises the feature.

## Sentinel

`sentinel/` — a module that writes a marker file if imported or executed. Every static
card asserts the marker is absent after a full run. It must be impossible to trip by
accident and trivial to assert on. This is the empirical proof of constraint 1.

## Mode B cases

### Inventory (card 1)

| Case | Proves |
|---|---|
| `inv_kinds` | Every `ElementKind` is produced: module, class, function, method, property, assignment, import, nested function, closure |
| `inv_ids_stable` | Reformatting the file (whitespace, comments, line breaks) changes no ID |
| `inv_redefinition` | A name defined twice yields `m::f` and `m::f#2`, in source order |
| `inv_blob` | A 2 MB embedded string literal becomes an opaque `BLOB` with size and location, and is never decoded |
| `inv_syntax_error` | A `SYNTAX_ERROR` record with location; the run continues |
| `inv_non_utf8` | A `DECODE_ERROR` record; the run continues |
| `inv_empty` | An empty file produces a module element, not a crash |
| `inv_incremental` | Cold and warm runs are byte-identical; a touched-but-unchanged file is not re-analysed; a changed file is |

### Resolution (card 2)

| Case | Proves |
|---|---|
| `res_import_absolute` | `RESOLVED` edge, `IMPORT_ABSOLUTE` |
| `res_import_relative` | Single and multi-level relative imports |
| `res_import_star` | Star import; names resolved via `__all__`, and the residue reported |
| `res_import_conditional` | Import under `if`/`try` and under `TYPE_CHECKING` |
| `res_import_local` | Import inside a function body |
| `res_import_cycle` | A cycle reported as a cycle, not an error |
| `res_reexport` | Re-export through `__init__.py` resolves to the original definition |
| `res_mro` | Method dispatch across a three-level hierarchy, plus `super()` |
| `res_decorator` | Edges to both wrapper and wrapped |
| `res_getattr_literal` | `PROBABLE`, `GETATTR_LITERAL` |
| `res_getattr_computed` | `UNKNOWN` with a candidate set — **not** a guessed edge |
| `res_importlib` | `importlib.import_module` with a literal |
| `res_registry_dict` | A module-level dict of callables |
| `res_registry_decorator` | Decorator-based registration |
| `res_config_wiring` | A class named by string in `wiring.json` resolves, with the config key as evidence |
| `res_config_dangling` | A config key naming nothing produces a finding, not an edge |
| `res_overlink_trap` | Two same-named methods on unrelated classes: **must not** be linked. Guards precision |

### Cascade and decisions (card 3)

| Case | Proves |
|---|---|
| `cfg_shapes` | Branches, loops, `try`/`except`/`finally`, `with`, comprehension, `match`, early return |
| `cfg_shortcircuit` | `and`/`or` produce branch edges, not flat expressions |
| `ord_linear` | A fixed order yields `SEQUENCE` |
| `ord_branching` | A conditional path yields `BRANCH`/`MERGE`, **never** a flattened sequence |
| `ord_unordered` | Two independent calls yield `UNORDERED` |
| `ord_cycle` | Recursion yields `CYCLE` with its member IDs |
| `dec_rule_cascade` | An `if`/`elif` chain: conditions, reads, outcomes |
| `dec_guard_clause` | Early-return guards |
| `dec_sink` | The decision sink is identified and reachability marked |
| `dec_uncertain_edge` | An element reachable only via a `HEURISTIC` edge is marked reachable, with the reason — **not** pruned |

### Lineage (card 4)

| Case | Proves |
|---|---|
| `lin_assign_chain` | Assignment, augmented assignment, unpacking, walrus |
| `lin_params` | Parameter binding and return flow across calls, including `**kwargs` |
| `lin_container` | Dict key and attribute writes tracked per key, not per container |
| `lin_dataframe` | `df["x"] = ...`, `assign`, `merge`, `groupby`, `apply`; each column its own node |
| `lin_feature_named` | The same feature named in code and in config lands on one node |
| `lin_closure` | Closure capture and mutation through an alias |
| `lin_barrier` | Flow into `eval`-built code ends in a `Barrier`, not a stitched-across edge |
| `lin_slice_backward` | Backward slice of a decision input is exact |
| `lin_slice_forward` | Forward slice reaches the sink and stops |

### Findings (card 5)

One case per `FindingKind`, plus:

| Case | Proves |
|---|---|
| `fnd_unknown_not_unplugged` | An element reachable only through an unresolved call site is `UNKNOWN`, **not** reported unplugged |
| `fnd_no_false_positive` | A fully live cascade produces **zero** findings. Guards precision |

### Diff (card 6)

| Case | Proves |
|---|---|
| `dif_added_removed` | Basic classification |
| `dif_rename` | A renamed function matched with evidence and confidence |
| `dif_move` | A function moved between modules |
| `dif_format_only` | Reformat and comment change classify as `UNCHANGED` |
| `dif_signature` | Signature change distinguished from body change |
| `dif_wiring` | A config key repointed to a different class |
| `dif_impact_rank` | A one-line change in a decision condition outranks a large unreachable refactor |
| `dif_symmetric` | A→B and B→A agree |

## Mode A cases

Executed **only** by harness and tracer tests. Nothing else runs them.

| Case | Proves |
|---|---|
| `run_linear` | Known execution order observed end to end; every event maps to a static ID |
| `run_branching` | The branch actually taken is recorded at each decision point |
| `run_values` | Captured values match values known by construction |
| `run_large_frame` | A large value is `SUMMARIZED` with `original_size`, never silently truncated |
| `run_redaction` | A value marked sensitive is `REDACTED` at capture time |
| `run_exception` | Raised and swallowed exceptions recorded |
| `run_unmapped` | Dynamically created code produces `UNMAPPED` events, reported not dropped |
| `run_contradiction` | An edge the static graph predicted is never taken → a contradiction record, and the static graph unchanged |
| `run_nondeterministic` | Time and randomness recorded as observed nondeterminism |
| `run_replay` | Replaying a recorded run is byte-identical |

### Adversarial — card 11's proof

| Case | Proves |
|---|---|
| `adv_network` | An outbound socket is blocked and recorded |
| `adv_dns` | A DNS lookup is blocked |
| `adv_write_escape` | Writes via absolute path, `..` and a symlink all land in the sandbox or are blocked |
| `adv_subprocess` | `subprocess`, `os.system` and `os.fork` are blocked |
| `adv_undeclared_client` | Reaching an undeclared external system is a **hard stop**, not a pass-through |
| `adv_refuse_start` | With a control unavailable, the run **refuses to start** and names the guarantee |
| `adv_stale_graph` | A Mode B graph not matching current target hashes is a refusal |

### Alignment and narrative (cards 13, 14)

| Case | Proves |
|---|---|
| `ali_aligned` | A confirmed intent met by observation |
| `ali_misaligned` | Names the specific expectation and the contradicting observation |
| `ali_not_exercised` | An element no scenario ran is `NOT_EXERCISED`, **not** `ALIGNED` |
| `ali_no_intent` | An element with no intent is `NO_INTENT`, not a failure |
| `ali_proposed_not_binding` | A `PROPOSED` intent cannot ground `ALIGNED`/`MISALIGNED` |
| `nar_anchored` | Every narrative step carries element and event IDs |
| `nar_loop_summary` | A 1000-iteration loop is summarised, not transcribed |
| `nar_absence` | Skipped steps and blocked attempts appear in the narrative |
| `nar_deterministic` | The same trace gives byte-identical text |

## Reporting

Cards 2, 4 and 5 report precision and recall against this corpus. Precision is the
priority for all three: a confident wrong edge, a stitched-across slice and a false
unplugged finding each cost the owner more than an honest gap does.
