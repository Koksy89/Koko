#!/usr/bin/env python3.12
"""laz_version_gate.py — did the engine change without the version moving?

The owner's rule is +1 on every change. A rule nobody checks is a rule that gets
skipped on the busy day, and then an output cannot be traced to a build. So:

  * compare the engine on disk with the last COMMITTED engine
  * if the bytes are identical, pass
  * if the bytes differ and LAZ_ENGINE_VERSION is the same, FAIL
  * also fail if the handover inside the docs blob does not name the current
    version, because prove_docs asserts exactly that and would fail the build later

Nothing is executed. With no git history available it reports that and passes,
rather than blocking work it cannot judge.
"""
import base64, json, os, re, subprocess, sys, zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(HERE),
                                                            'AmunEV_Engine_V2.py')
HANDOVER = 'LAZARUS_HANDOVER_v191.md'


def version_of(src):
    m = re.search(r"^LAZ_ENGINE_VERSION = '([^']+)'", src, re.M)
    return m.group(1) if m else None


def handover_of(src):
    m = re.search(r"^laz_docs___BLOB = '([A-Za-z0-9+/=]+)'", src, re.M)
    if not m:
        return None
    try:
        return json.loads(zlib.decompress(base64.b64decode(m.group(1))).decode()).get(HANDOVER)
    except Exception:
        return None


cur = open(ENGINE, encoding='utf-8').read()
ver = version_of(cur)
print(f'{os.path.basename(ENGINE)}  ·  LAZ_ENGINE_VERSION = {ver}')

fails = []
hand = handover_of(cur)
if hand is None:
    print('  handover not readable from the docs blob — cannot check prove_docs')
elif ver and ver in hand:
    print(f'  ok    the handover names {ver}, so prove_docs will pass')
else:
    fails.append(f'the handover inside the docs blob does not name {ver}. prove_docs '
                 f'asserts `LAZ_ENGINE_VERSION in docs[{HANDOVER}]` and will fail the build. '
                 f'Run: python3 tools/laz_bump_version.py "<what changed>"')

try:
    # RUN GIT FROM THE ENGINE'S OWN DIRECTORY, and ask it for the path RELATIVE to the
    # repo root. The first version passed an absolute path with a cwd of the same
    # directory; `git ls-files` returned nothing, the lookup raised, and the gate fell
    # through to "no git comparison available" AND PASSED. A gate that degrades to a
    # pass is not a gate -- a forgotten bump sailed straight through it in testing.
    d = os.path.dirname(os.path.abspath(ENGINE)) or '.'
    top = subprocess.run(['git', 'rev-parse', '--show-toplevel'], cwd=d,
                         capture_output=True, text=True)
    if top.returncode != 0:
        raise FileNotFoundError('not inside a git repository')
    root = top.stdout.strip()
    rel = os.path.relpath(os.path.abspath(ENGINE), root)
    prev = subprocess.run(['git', 'show', f'HEAD:{rel}'], cwd=root, capture_output=True)
    if prev.returncode != 0:
        raise FileNotFoundError(f'{rel} has no committed version to compare against')
    prev_src = prev.stdout.decode('utf-8', 'ignore')
    if prev_src == cur:
        print('  ok    engine is byte-identical to the last commit — nothing to bump')
    else:
        pv = version_of(prev_src)
        if pv == ver:
            fails.append(f'THE ENGINE CHANGED SINCE THE LAST COMMIT BUT THE VERSION DID NOT '
                         f'(still {ver}). The owner\'s rule is +1 on every change, so an '
                         f'output can be traced to the build that made it. '
                         f'Run: python3 tools/laz_bump_version.py "<what changed>"')
        else:
            print(f'  ok    engine changed and the version moved: {pv} -> {ver}')
except Exception as e:
    print(f'  --    no git comparison available ({e}); the version rule is not checked here')

for f in fails:
    print(f'  FAIL  {f}')
sys.exit(1 if fails else 0)
