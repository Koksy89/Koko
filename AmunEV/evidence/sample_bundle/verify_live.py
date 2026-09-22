#!/usr/bin/env python3
"""verify_live.py — prove the live database matches this bundle. READS ONLY.

Run it after deploy.sql. It re-applies every gate the evaluators apply, against what is
actually in the database now, and exits non-zero on the first thing that would break a
sport at load. It opens no market, places no bet and writes nothing.
"""
import json, os, re, sys

COND = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(<=|>=|==|=|<|>)\s*"
                  r"(-?\d+(?:\.\d+)?|True|False)\s*$", re.IGNORECASE)
HERE = os.path.dirname(os.path.abspath(__file__))
MAN = json.load(open(os.path.join(HERE, "MANIFEST.json")))
SPORT = MAN["sport"]
SETTLEABLE = set(MAN["settleable_markets"])
ROLES = set(MAN["resolvable_roles"])


def main(dsn):
    import psycopg2
    bad = []
    with psycopg2.connect(dsn) as conn:
        cur = conn.cursor()
        cur.execute("SELECT feature, column_name FROM laz_feature_namespace WHERE sport=%s",
                    (SPORT,))
        ns = dict(cur.fetchall())
        cur.execute("SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'rt_allsports_laz_features'")
        have = {r[0] for r in cur.fetchall()}
        for f, c in ns.items():
            if c not in have:
                bad.append("G6 %s -> column %s absent from rt_allsports_laz_features" % (f, c))
        cur.execute("SELECT strategy, conditions, market, min_odds, max_odds, bet_role "
                    "FROM laz_strategy_registry WHERE sport=%s AND enabled", (SPORT,))
        rows = cur.fetchall()
    if not rows:
        bad.append("G3 zero enabled %s strategies -- the evaluator refuses to start" % SPORT)
    expected = {s["strategy"]: s for s in MAN["strategies"]}
    for name, conds, market, lo, hi, role in rows:
        if market not in SETTLEABLE:
            bad.append("G4 %s market %r not settleable" % (name, market))
        if not conds:
            bad.append("G10 %s has no conditions -- fires on every match" % name)
        for c in (conds or []):
            m = COND.match(c)
            if not m:
                bad.append("G1 %s unparseable condition %r" % (name, c))
            elif m.group(1) not in ns:
                bad.append("G5 %s term %r has no namespace row" % (name, m.group(1)))
        if role not in ROLES:
            bad.append("G7 %s bet_role %r -- resolve_side answers None, never fires" % (name, role))
        for k, v in (("min_odds", lo), ("max_odds", hi)):
            if v is None or float(v) != float(v):
                bad.append("G9 %s %s is %r -- admits every price" % (name, k, v))
        e = expected.get(name)
        if e is None:
            bad.append("DRIFT %s is enabled in the database but not in this bundle" % name)
        elif sorted(e["conditions"]) != sorted(conds or []):
            bad.append("DRIFT %s conditions differ from the bundle" % name)
    for name in expected:
        if name not in {r[0] for r in rows}:
            bad.append("DRIFT %s is in the bundle but not enabled in the database" % name)
    if bad:
        print("FAIL (%d)" % len(bad))
        for b in bad:
            print("  " + b)
        return 1
    print("OK  %d strategies - %d feature columns - every gate passed" % (len(rows), len(ns)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else os.environ["BETSMITH_DSN"]))
