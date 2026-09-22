#!/usr/bin/env python3
"""Compare a production bet list against the reference implementation.

    python3 verify.py strategies.json ticks.json production_bets.json

ticks.json            : [{match_id, ts, market_status, <feature columns>...}, ...]
production_bets.json  : [{strategy, match_id, ts, price}, ...] from YOUR system

Exit code 0 only when the two agree exactly, strategy by strategy and match by
match. Anything else prints what differs and exits 1.
"""
import json
import sys

from reference_impl import load_strategies, run


def key(b):
    return (str(b["strategy"]), str(b["match_id"]))


def main():
    if len(sys.argv) != 4:
        print(__doc__)
        return 2
    strategies = load_strategies(sys.argv[1])
    with open(sys.argv[2]) as fh:
        ticks = json.load(fh)
    with open(sys.argv[3]) as fh:
        prod = json.load(fh)

    ref = run(strategies, ticks)
    R, P = {key(b): b for b in ref}, {key(b): b for b in prod}

    missing = sorted(R.keys() - P.keys())
    extra = sorted(P.keys() - R.keys())
    price_diff = [(k, R[k]["price"], P[k]["price"]) for k in sorted(R.keys() & P.keys())
                  if abs(float(R[k]["price"]) - float(P[k]["price"])) > 1e-9]
    tick_diff = [(k, R[k]["ts"], P[k]["ts"]) for k in sorted(R.keys() & P.keys())
                 if str(R[k]["ts"]) != str(P[k]["ts"])]

    print(f"reference bets : {len(ref)}")
    print(f"production bets: {len(prod)}")
    print(f"  missing in production: {len(missing)}")
    print(f"  extra in production  : {len(extra)}")
    print(f"  price mismatches     : {len(price_diff)}")
    print(f"  arm-tick mismatches  : {len(tick_diff)}")
    for k in missing[:10]:
        print(f"    MISSING {k}")
    for k in extra[:10]:
        print(f"    EXTRA   {k}")
    for k, a, b in price_diff[:10]:
        print(f"    PRICE   {k}: reference {a} vs production {b}")
    for k, a, b in tick_diff[:10]:
        print(f"    TICK    {k}: reference {a} vs production {b}")

    ok = not (missing or extra or price_diff or tick_diff)
    print("\nRESULT: " + ("MATCH" if ok else "MISMATCH"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
