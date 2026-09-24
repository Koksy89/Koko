"""The shared parse service: one `ast.parse` per (path, content hash) per run.

Five stages used to parse the same target source. On the owner's engine -- one
14,804,021-byte module -- a single parse is 20.8s and 895,811 nodes, so the
redundancy was over a minute of every run. These tests hold the three
properties that make sharing safe rather than merely fast:

* **Identity**, not equality. Two consumers must get the SAME object, or
  nothing was shared and the parse was paid twice.
* **Freshness.** A file whose bytes changed must never be served the old tree,
  even at the same path.
* **No mutation.** A shared tree is only safe while every consumer treats it as
  read-only. `test_real_consumers_do_not_mutate_the_shared_tree` runs the three
  stages that share one and proves, by fingerprint, that the tree they hand
  back is byte-for-byte the tree they were given.

Nothing here imports or executes target or fixture code; sources are written as
text and parsed.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from cascade_map.cascade import CascadeAnalyzer
from cascade_map.contracts.interfaces import (
    Confidence,
    Element,
    ElementKind,
    Method,
    Provenance,
    SourceSpan,
    make_id,
)
from cascade_map.lineage import LineageTracer
from cascade_map.parsecache import (
    DEFAULT_BUDGET_BYTES,
    ParseCache,
    TreeMutatedError,
    tree_fingerprint,
)
from cascade_map.resolve import Resolver

SOURCE = "def f(a):\n    b = a + 1\n    return b\n"


def module_element(module: str, path: str) -> Element:
    return Element(
        id=make_id(module),
        kind=ElementKind.MODULE,
        name=module,
        qualname="",
        module=module,
        span=SourceSpan(path=path, line=1),
        provenance=Provenance(method=Method.AST_DIRECT, confidence=Confidence.RESOLVED),
        content_hash="h",
    )


# -- the cache itself ------------------------------------------------------


def test_second_ask_is_a_hit_and_the_very_same_object() -> None:
    """The whole point: share, do not re-derive. Identity, not equality --
    two structurally equal trees would mean the parse was paid twice."""
    cache = ParseCache()
    first = cache.parse("m.py", SOURCE)
    second = cache.parse("m.py", SOURCE)
    assert first is second
    assert cache.stats.parses == 1
    assert cache.stats.hits == 1
    assert cache.stats.misses == 1


def test_changed_content_forces_a_reparse_at_the_same_path() -> None:
    """A stale tree is worse than a slow one: it describes code that is no
    longer there. The key is the hash, so the same path cannot serve it."""
    cache = ParseCache()
    first = cache.parse("m.py", SOURCE)
    changed = cache.parse("m.py", SOURCE.replace("a + 1", "a + 2"))
    assert changed is not first
    assert cache.stats.parses == 2
    assert cache.stats.hits == 0
    # The original bytes are still held and still answered from memory.
    assert cache.parse("m.py", SOURCE) is first


def test_an_explicit_hash_is_used_instead_of_rehashing() -> None:
    """Ingestion already hashes every file it walks; a caller with a hash
    passes it rather than making the cache hash 14 MB again."""
    cache = ParseCache()
    first = cache.parse("m.py", SOURCE, content_hash="deadbeef")
    assert cache.parse("m.py", SOURCE, content_hash="deadbeef") is first
    assert cache.parse("m.py", SOURCE, content_hash="feedface") is not first
    assert cache.stats.parses == 2


def test_different_paths_do_not_share_a_tree() -> None:
    cache = ParseCache()
    a = cache.parse("a.py", SOURCE)
    b = cache.parse("b.py", SOURCE)
    assert a is not b
    assert cache.stats.parses == 2


def test_budget_evicts_least_recently_used_and_counts_the_thrash() -> None:
    """When the budget cannot hold everything, the oldest tree goes and a
    later ask for it re-parses. Correct, just slower -- and COUNTED, so the
    run report can say the cache thrashed instead of quietly costing time."""
    body = "x = 1\n" * 200  # ~1200 bytes each
    budget = len(body.encode()) * 2 + 10  # room for two, not three
    cache = ParseCache(budget)
    a = cache.parse("a.py", body)
    cache.parse("b.py", body)
    assert cache.stats.evictions == 0
    cache.parse("c.py", body)  # evicts a.py, the least recently asked for
    assert cache.stats.evictions == 1
    assert cache.stats.bytes_held <= budget

    again = cache.parse("a.py", body)
    assert again is not a, "an evicted tree must be re-parsed, not resurrected"
    assert cache.stats.thrash_reparses == 1
    assert "thrashed" in cache.stats.render()


def test_recent_use_protects_a_tree_from_eviction() -> None:
    """LRU, not FIFO: asking again moves a tree to the back of the queue."""
    body = "x = 1\n" * 200
    cache = ParseCache(len(body.encode()) * 2 + 10)
    a = cache.parse("a.py", body)
    cache.parse("b.py", body)
    assert cache.parse("a.py", body) is a  # a.py is now the most recent
    cache.parse("c.py", body)              # so b.py goes, not a.py
    assert cache.parse("a.py", body) is a
    assert cache.stats.thrash_reparses == 0


def test_a_file_larger_than_the_whole_budget_is_never_cached() -> None:
    """Holding it would blow the budget and evicting it on the next ask would
    thrash every time. It is parsed for each asker, exactly as before, and the
    run report says how many files that happened to."""
    body = "x = 1\n" * 200
    cache = ParseCache(10)
    first = cache.parse("big.py", body)
    assert cache.parse("big.py", body) is not first
    assert cache.stats.oversize == 2
    assert cache.stats.bytes_held == 0
    assert "larger than the whole budget" in cache.stats.render()


def test_a_zero_budget_disables_sharing_entirely() -> None:
    """`--parse-cache-mb 0` is the escape hatch: behave exactly as the tool
    did before this module existed."""
    cache = ParseCache(0)
    assert cache.parse("m.py", SOURCE) is not cache.parse("m.py", SOURCE)
    assert cache.stats.parses == 2
    assert cache.stats.hits == 0


def test_the_budget_never_evicts_the_entry_just_inserted() -> None:
    body = "x = 1\n" * 200
    cache = ParseCache(len(body.encode()) + 10)
    cache.parse("a.py", body)
    newest = cache.parse("b.py", body)
    assert cache.parse("b.py", body) is newest


# -- failures --------------------------------------------------------------


def test_a_syntax_error_is_raised_to_every_caller_and_parsed_once() -> None:
    """Each stage maps a failure to its own `Unresolved` record, so the
    exception must reach all of them -- but a broken 14 MB file is not worth
    parsing five times to learn the same thing."""
    cache = ParseCache()
    for _ in range(3):
        with pytest.raises(SyntaxError):
            cache.parse("bad.py", "def (:\n")
    assert cache.stats.parses == 1
    assert cache.stats.failure_hits == 2


def test_the_filename_reaches_the_syntax_error() -> None:
    """Consumers build spans from the failure; the cache must not swallow the
    `filename=` every one of them already passed."""
    cache = ParseCache()
    with pytest.raises(SyntaxError) as caught:
        cache.parse("weird/path.py", "def (:\n")
    assert caught.value.filename == "weird/path.py"


# -- mutation, the property that makes sharing safe ------------------------


def test_fingerprint_notices_an_attribute_attached_to_a_node() -> None:
    """Parent pointers are the classic way a stage quietly claims a tree.
    They leave no trace in `ast.dump`, so the fingerprint reads `__dict__`."""
    tree = ast.parse(SOURCE)
    before = tree_fingerprint(tree)
    tree.body[0].parent = tree  # type: ignore[attr-defined]
    assert tree_fingerprint(tree) != before


def test_fingerprint_notices_a_rewritten_node() -> None:
    tree = ast.parse(SOURCE)
    before = tree_fingerprint(tree)
    tree.body[0].name = "g"  # type: ignore[attr-defined]
    assert tree_fingerprint(tree) != before


def test_strict_mode_refuses_to_hand_out_a_mutated_tree() -> None:
    """The guard that keeps this module honest as the tool grows. A consumer
    that starts mutating fails HERE, loudly, instead of silently corrupting
    whichever stage is handed the tree next."""
    cache = ParseCache(strict=True)
    tree = cache.parse("m.py", SOURCE)
    tree.body[0].parent = tree  # type: ignore[attr-defined]
    with pytest.raises(TreeMutatedError):
        cache.parse("m.py", SOURCE)


def test_an_annotation_lives_beside_the_tree_not_on_its_nodes() -> None:
    """The sanctioned alternative if a stage ever does need to remember
    something per tree."""
    cache = ParseCache(strict=True)
    tree = cache.parse("m.py", SOURCE, content_hash="h")
    cache.annotate("m.py", "h", "parents", {1: 2})
    assert cache.annotation("m.py", "h", "parents") == {1: 2}
    assert cache.parse("m.py", SOURCE, content_hash="h") is tree


def test_real_consumers_do_not_mutate_the_shared_tree(tmp_path: Path) -> None:
    """The audit, executed rather than asserted.

    Resolution, the CFG stage and lineage each walk the same tree. If any of
    them wrote to it, the fingerprint taken before would not match the one
    taken after, and the strict cache would have raised on the second
    hand-out. Both checks are here because they catch different things: the
    cache catches mutation BETWEEN stages, the fingerprint catches mutation
    that a later stage undoes.
    """
    source = (
        "import os\n"
        "THRESHOLD = 3\n"
        "def score(row):\n"
        "    total = row['a'] + THRESHOLD\n"
        "    if total > 10:\n"
        "        return 'high'\n"
        "    elif total > 5:\n"
        "        return 'mid'\n"
        "    return 'low'\n"
        "def main():\n"
        "    return score({'a': os.getpid()})\n"
    )
    (tmp_path / "engine.py").write_text(source, encoding="utf-8")
    elements = [module_element("engine", "engine.py")]

    cache = ParseCache(strict=True)
    resolver = Resolver(tmp_path, parse_cache=cache)
    edges, _ = resolver.resolve(elements)
    tree = cache.parse("engine.py", source)
    before = tree_fingerprint(tree)

    analyzer = CascadeAnalyzer(tmp_path, workers=1, parse_cache=cache)
    analyzer.order(elements, edges, ())
    assert tree_fingerprint(tree) == before, "the CFG stage mutated a shared tree"

    tracer = LineageTracer(tmp_path, parse_cache=cache)
    tracer.trace_values(elements, edges)
    assert tree_fingerprint(tree) == before, "lineage mutated a shared tree"

    # And the sharing actually happened: one parse, many asks.
    assert cache.stats.parses == 1, cache.stats.as_dict()
    assert cache.stats.hits >= 3, cache.stats.as_dict()


def test_stages_built_without_a_cache_still_work(tmp_path: Path) -> None:
    """Every existing caller -- and every test in this suite -- constructs
    these stages with no cache argument. They must keep working, each with a
    private cache, which is still a win: resolution alone asks twice."""
    (tmp_path / "m.py").write_text(SOURCE, encoding="utf-8")
    elements = [module_element("m", "m.py")]
    resolver = Resolver(tmp_path)
    resolver.resolve(elements)
    assert resolver._parse_cache.stats.parses == 1
    assert resolver._parse_cache.stats.hits == 1


def test_the_default_budget_holds_the_largest_file_the_tool_will_parse() -> None:
    """Card 1 will not parse a file over 16 MB, so a default that could not
    hold one would guarantee a miss on exactly the targets this exists for."""
    from cascade_map.ingest.inventory import MAX_FILE_BYTES

    assert DEFAULT_BUDGET_BYTES > MAX_FILE_BYTES
