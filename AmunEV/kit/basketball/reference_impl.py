#!/usr/bin/env python3
"""Reference implementation: tick rows -> bets, in the engine's own gate order.

This is the ARBITER. If production disagrees with this file, production is wrong.
It is deliberately simple and dependency-light so it can be read and checked line
by line.

THE GATE ORDER (do not reorder -- the engine evaluates in exactly this sequence):
  1. every condition holds at the tick; a NaN/None value NEVER satisfies one
  2. the backed-side price is >= the strategy's min odds floor
  3. the market is open at that tick
  4. the FIRST tick of the match that passes 1-3 is the bet; later ticks of that
     match are ignored (one bet per match)

Usage:
    from reference_impl import load_strategies, run
    strategies = load_strategies("strategies.json")
    bets = run(strategies, tick_rows)      # tick_rows: iterable of dicts
"""
import json
import math


def _is_null(v):
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    return False


OPS = {
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


def load_strategies(path):
    with open(path) as fh:
        return json.load(fh)


def conditions_hold(strategy, row):
    """Gate 1. Every condition must hold. A NULL value fails its condition."""
    for c in strategy["conditions"]:
        v = row.get(c["term"])
        if _is_null(v):
            return False
        op = OPS.get(c["op"])
        if op is None:
            return False
        try:
            if not op(float(v), float(c["value"])):
                return False
        except (TypeError, ValueError):
            return False
    return True


def backed_price(strategy, row):
    """The price of the side this strategy backs, at this tick.

    The kit records each strategy's `backed` side and `price_column`. Where the
    book did not record them, the leader/favourite convention below applies --
    CHECK THIS PER STRATEGY before staking.
    """
    col = strategy.get("price_column") or ""
    if col and col in row and not _is_null(row[col]):
        return float(row[col])
    ho, ao = row.get("home_odds"), row.get("away_odds")
    if _is_null(ho) or _is_null(ao):
        return None
    side = str(strategy.get("backed") or "").lower()
    if "home" in side:
        return float(ho)
    if "away" in side:
        return float(ao)
    return max(float(ho), float(ao))


def market_open(row):
    """Gate 3."""
    st = row.get("market_status")
    if st is None:
        return True          # no status recorded = not a gate
    return str(st).strip().lower() in ("open", "1", "true", "active")


def run(strategies, rows):
    """Gates 1-4. Returns [{strategy, match_id, ts, price}], one per match."""
    seen = set()
    out = []
    for row in rows:
        for s in strategies:
            key = (s["strategy"], row.get("match_id"))
            if key in seen:
                continue                                   # gate 4
            if not conditions_hold(s, row):                 # gate 1
                continue
            px = backed_price(s, row)
            if px is None:
                continue
            floor = s.get("min_odds_floor")
            try:
                floor = float(floor) if floor is not None else 1.4
            except (TypeError, ValueError):
                floor = 1.4
            if px < floor:                                  # gate 2
                continue
            if not market_open(row):                        # gate 3
                continue
            seen.add(key)
            out.append(dict(strategy=s["strategy"], match_id=row.get("match_id"),
                            ts=row.get("ts"), price=px))
    return out
