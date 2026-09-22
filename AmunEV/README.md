# AmunEV Engine V2 — accurate strategy documentation

`AmunEV_Engine_V2.py` here is the owner's reference copy `bc39725f09f9c0d3`
(the version that produced the 175 strategies), with the strategy-documentation
path repaired so a validated strategy's written specification rebuilds the bets
it was measured on.

* `evidence/DOC_FIX.md` — every defect found, the fix, and the verification.
* `tests/test_production_docs.py` — 37 assertions over the changed code, run by
  extracting the functions from the engine source with `ast`. The engine is
  never imported or executed.
* `audit/laz_static_audit.py` — parse, undefined names, unmanaged file handles,
  duplicate definitions, wiring. Static only.
* `audit/laz_wiring.py` — call-graph reachability from the entry points.

The engine requires **Python 3.12+** (it uses PEP 701 nested same-quote
f-strings in 165 places; it is a SyntaxError on 3.11).

```bash
python3.12 audit/laz_static_audit.py AmunEV_Engine_V2.py
python3.12 tests/test_production_docs.py
```
