"""One parse per (path, content hash) per run.

The owner's design is: open, read ONCE, share, consolidate, move on. Five
stages used to call :func:`ast.parse` on the same target source -- inventory,
resolution (twice: once to summarise, once to resolve bodies), the CFG stage,
lineage, and dependencies. On the owner's engine, one 14,804,021-byte module,
a single ``ast.parse`` is 20.8s and 895,811 nodes. Four redundant parses is
over a minute of a ~350s run spent re-deriving a tree the process already had.

This module holds the trees that the *in-process* path shares.

WHY IN-PROCESS ONLY
    A tree cannot cross a process boundary without being pickled and rebuilt,
    which costs more than re-parsing: the CFG pool measured 37.9s -> 86.2s
    with 75 MB returned per unit, which is why ``_CFG_POOL_ENABLED`` is False.
    The same trap is documented on :class:`~.cascade._PreOpened`. So a
    :class:`ParseCache` is never sent to a pool: a worker would get a *copy*
    that diverges silently. Stages that genuinely run elsewhere (inventory's
    file workers, and the dependencies stage when it is overlapped into a
    second process) keep parsing for themselves, and that is correct.

WHY IT IS BOUNDED
    The measured cost of holding one tree for that 14.6 MB module is 0.62 GB
    of RSS -- roughly forty times the source. The old code paid the parse
    repeatedly precisely so it could ``del tree`` and keep only one alive at a
    time. Sharing means holding, so the holding is capped: see
    :data:`DEFAULT_BUDGET_BYTES`. The budget is charged in SOURCE bytes, which
    is a number that is actually known, rather than in a guess at node count
    or heap size.

WHY MUTATION IS THE RISK
    A shared tree is only safe while every consumer treats it as read-only.
    All five consumers were audited and none of them mutates the AST: no
    ``NodeTransformer``, no parent pointers, no annotation attached to a node,
    no in-place edit of a node's field list. Every one of them is an
    ``ast.walk`` or a ``NodeVisitor`` writing into the tool's own summary
    objects. :meth:`ParseCache.parse` therefore hands back the SAME object.
    Because that property is load-bearing and easy to break later,
    ``strict=True`` fingerprints every tree on the way out and re-checks it on
    the next hand-out, so a consumer that starts mutating fails loudly here
    instead of silently corrupting the next stage's input. The test suite runs
    strict; a run does not, because the fingerprint walks every node.
"""

from __future__ import annotations

import ast
import hashlib
from collections import OrderedDict
from dataclasses import dataclass, field

from .ingest.hashing import sha256_text

__all__ = [
    "DEFAULT_BUDGET_BYTES",
    "ParseCache",
    "ParseCacheStats",
    "TreeMutatedError",
    "tree_fingerprint",
]

#: How many bytes of SOURCE may be held at once, across all cached trees.
#:
#: 32 MB. The largest single file this tool will parse is card 1's 16 MB
#: inventory limit, so the default comfortably holds the biggest file a run
#: can produce plus as much again of headroom -- on the owner's engine that is
#: its 14.8 MB module and room to spare.
#:
#: Read it as source bytes, not as footprint: the measured expansion on the
#: owner's engine is ~40x (14.6 MB of source -> 0.62 GB of RSS), so a budget
#: that is FULL of source at that ratio is on the order of 1.3 GB resident.
#: Raising it trades memory for parses; `--parse-cache-mb` overrides it, and
#: `0` disables caching entirely (every ask re-parses, as before this module).
DEFAULT_BUDGET_BYTES = 32_000_000


class TreeMutatedError(RuntimeError):
    """A cached tree changed between hand-outs.

    Raised only under ``strict=True``. It means some consumer mutated a tree
    it does not own, which would corrupt the next consumer's input. The fix is
    never to relax this check: either give that consumer its own copy, or keep
    its annotation beside the tree rather than on it.
    """


def tree_fingerprint(tree: ast.AST) -> str:
    """A hash that changes if anything about *tree* changes.

    Covers three kinds of mutation: a rewritten field or a moved node
    (``ast.dump`` with fields), a changed position (``include_attributes``),
    and an attribute *attached* to a node, such as a parent pointer, which
    shows up in the node's ``__dict__`` but in no dump.
    """
    digest = hashlib.sha256()
    for node in ast.walk(tree):
        digest.update(type(node).__name__.encode())
        digest.update(",".join(sorted(node.__dict__)).encode())
        digest.update(b"\x00")
    digest.update(ast.dump(tree, include_attributes=True).encode())
    return digest.hexdigest()


@dataclass
class ParseCacheStats:
    """What the cache did this run. Wall-clock-adjacent and run-shaped, so it
    is reported and never written into an artifact (constraint 4)."""

    #: Trees actually built by `ast.parse`, including re-parses after eviction.
    parses: int = 0
    #: Asks answered from a held tree.
    hits: int = 0
    #: Asks that had to parse.
    misses: int = 0
    #: Trees dropped to stay inside the budget.
    evictions: int = 0
    #: Parses of a key this cache had held and evicted. Non-zero means the
    #: budget is too small for this target and the run paid for it.
    thrash_reparses: int = 0
    #: Files whose source alone exceeds the whole budget. Never cached, so
    #: every stage re-parses them exactly as it did before.
    oversize: int = 0
    #: Asks that raised, answered from a remembered failure rather than by
    #: parsing a known-broken file four more times.
    failure_hits: int = 0
    #: Source bytes currently and ever held.
    bytes_held: int = 0
    peak_bytes_held: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "parses": self.parses,
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "thrash_reparses": self.thrash_reparses,
            "oversize": self.oversize,
            "failure_hits": self.failure_hits,
            "peak_bytes_held": self.peak_bytes_held,
        }

    def render(self) -> str:
        """One line for the run report."""
        asks = self.hits + self.misses
        saved = self.hits
        line = (
            f"parse cache: {asks} asks, {self.parses} parses, "
            f"{saved} reused, peak {self.peak_bytes_held / 1e6:.1f} MB of source held"
        )
        if self.evictions:
            line += (
                f"; {self.evictions} evictions and {self.thrash_reparses} re-parses "
                f"-- the cache thrashed, raise --parse-cache-mb"
            )
        if self.oversize:
            line += (
                f"; {self.oversize} file(s) larger than the whole budget were "
                f"never cached"
            )
        return line


@dataclass
class _Entry:
    charge: int
    tree: ast.Module | None = None
    error: BaseException | None = None
    fingerprint: str = ""
    extras: dict[str, object] = field(default_factory=dict)


class ParseCache:
    """Hands out one tree per (path, content hash), newest asks keeping it.

    Not thread-safe, and deliberately so: everything that shares it runs in
    one process on one thread. The dependencies stage either runs in a second
    *process* (which gets no cache at all) or inline on this thread after
    lineage has finished; neither is concurrent with anything.
    """

    def __init__(
        self,
        budget_bytes: int = DEFAULT_BUDGET_BYTES,
        *,
        strict: bool = False,
    ) -> None:
        #: Source bytes that may be held at once. 0 disables the cache.
        self.budget_bytes = max(0, int(budget_bytes))
        #: Fingerprint every tree on hand-out and re-check it on the next ask.
        self.strict = strict
        self.stats = ParseCacheStats()
        self._entries: "OrderedDict[tuple[str, str], _Entry]" = OrderedDict()
        #: Keys this cache held and dropped, so a later ask for one can be
        #: counted as thrash rather than as an ordinary first parse.
        self._evicted: set[tuple[str, str]] = set()

    # -- the one method every stage calls ---------------------------------

    def parse(
        self, path: str, source: str, content_hash: str | None = None
    ) -> ast.Module:
        """The tree for *source*, parsed at most once per (path, hash).

        *path* is only the ``filename=`` every consumer already passed, so the
        spans and the messages in a ``SyntaxError`` are unchanged. Pass
        *content_hash* when the caller already has one -- ingestion hashes
        every file it walks -- otherwise the same ``sha256_text`` used
        everywhere else in this tool is applied to *source*. A file whose
        content changed hashes differently and so can never be served a stale
        tree, even at the same path.

        Raises exactly what ``ast.parse`` raises, so each stage keeps its own
        mapping from failure to :class:`Unresolved`. A failure is remembered
        too: a syntactically broken 14 MB file is not worth parsing five times
        to learn the same thing.

        Never imports, executes, ``exec``s, ``eval``s or unpickles anything.
        """
        key = (path, content_hash if content_hash is not None else sha256_text(source))
        entry = self._entries.get(key)
        if entry is not None:
            self._entries.move_to_end(key)
            if entry.error is not None:
                self.stats.hits += 1
                self.stats.failure_hits += 1
                raise entry.error
            assert entry.tree is not None
            self._check(key, entry)
            self.stats.hits += 1
            return entry.tree

        self.stats.misses += 1
        if key in self._evicted:
            self.stats.thrash_reparses += 1
        charge = len(source.encode("utf-8", errors="surrogateescape"))

        self.stats.parses += 1
        try:
            tree = ast.parse(source, filename=path)
        except BaseException as exc:  # re-raised below; remembered if cacheable
            if self._cacheable(charge):
                self._insert(key, _Entry(charge=charge, error=exc))
            raise

        if not self._cacheable(charge):
            return tree
        new = _Entry(charge=charge, tree=tree)
        if self.strict:
            new.fingerprint = tree_fingerprint(tree)
        self._insert(key, new)
        return tree

    # -- notes that belong BESIDE a tree, never attached to its nodes ------

    def annotate(self, path: str, content_hash: str, name: str, value: object) -> None:
        """Store *value* under *name* for a cached tree.

        The escape hatch for a stage that wants to remember something about a
        tree -- the place a parent map or a per-node marking goes if one is
        ever needed. It lives here, keyed alongside the tree, precisely so it
        never becomes an attribute set on a shared node.
        """
        entry = self._entries.get((path, content_hash))
        if entry is not None:
            entry.extras[name] = value

    def annotation(self, path: str, content_hash: str, name: str) -> object | None:
        entry = self._entries.get((path, content_hash))
        return None if entry is None else entry.extras.get(name)

    # -- internals ---------------------------------------------------------

    def _cacheable(self, charge: int) -> bool:
        if self.budget_bytes <= 0:
            return False
        if charge > self.budget_bytes:
            self.stats.oversize += 1
            return False
        return True

    def _insert(self, key: tuple[str, str], entry: _Entry) -> None:
        self._entries[key] = entry
        self.stats.bytes_held += entry.charge
        self._evicted.discard(key)
        self._evict_to_budget()
        self.stats.peak_bytes_held = max(
            self.stats.peak_bytes_held, self.stats.bytes_held
        )

    def _evict_to_budget(self) -> None:
        """Drop least-recently-asked-for trees until inside the budget.

        Never drops the entry just inserted: an ask that evicted its own answer
        would re-parse on the very next ask for the same file, which is the
        one behaviour worse than not caching. A file too big for the whole
        budget is refused by :meth:`_cacheable` before it ever gets here.
        """
        while self.stats.bytes_held > self.budget_bytes and len(self._entries) > 1:
            old_key, old = self._entries.popitem(last=False)
            self.stats.bytes_held -= old.charge
            if old.error is None:
                self.stats.evictions += 1
                self._evicted.add(old_key)

    def _check(self, key: tuple[str, str], entry: _Entry) -> None:
        if not self.strict or not entry.fingerprint:
            return
        assert entry.tree is not None
        now = tree_fingerprint(entry.tree)
        if now != entry.fingerprint:
            raise TreeMutatedError(
                f"the shared AST for {key[0]} changed between hand-outs: a "
                f"consumer mutated a tree it does not own. Give that stage its "
                f"own copy, or keep its annotation beside the tree "
                f"(ParseCache.annotate) rather than on its nodes."
            )

    def clear(self) -> None:
        """Drop every held tree. The stats survive, because what the run did
        is still true after the memory is handed back."""
        self._entries.clear()
        self.stats.bytes_held = 0
