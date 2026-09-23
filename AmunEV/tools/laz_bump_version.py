#!/usr/bin/env python3.12
"""laz_bump_version.py — +1 to the engine version, and everything that depends on it.

    python3 tools/laz_bump_version.py "what changed, in one line"

WHY THIS IS A TOOL AND NOT A ONE-LINE EDIT. Three things have to move together, and
skipping any one of them fails the build or silently loses the trail:

  1. LAZ_ENGINE_VERSION            the constant every output stamps itself with
  2. the handover, INSIDE the docs blob   prove_docs asserts
                                        `LAZ_ENGINE_VERSION in docs[handover]`.
                                        Bump the constant alone and the build fails.
                                        THIS is why the label sat frozen at V2.9:
                                        the handover named V2.3-V2.9 and nothing
                                        taught it the next one.
  3. CHANGELOG.md                  the same entry in plain text, so the history is
                                   readable without decoding a base64 blob

ONE CONSEQUENCE, STATED PLAINLY. The feature-library sidecar is keyed on
(frame, builder source, LAZ_ENGINE_VERSION) — `engine = LAZ_ENGINE_VERSION -> a new
engine rebuilds`. So the FIRST run after a bump rebuilds the library, 8-12 minutes
per sport. That is the price of the version actually meaning something. LAZ_SIDECAR
already governs that cache if you ever want it keyed on the builder hash alone.
"""
import argparse, ast, base64, datetime, hashlib, json, os, re, sys, zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(os.path.dirname(HERE), 'AmunEV_Engine_V2.py')
HANDOVER = 'LAZARUS_HANDOVER_v191.md'


def current_version(src):
    m = re.search(r"^LAZ_ENGINE_VERSION = '([^']+)'", src, re.M)
    return (m.group(1), m.span(1)) if m else (None, None)


def next_version(cur):
    """V2.9 -> V3, V3 -> V4, V10 -> V11. The owner's rule: +1 on every change.

    A trailing .9 is not a minor number to carry; the owner asked for an integer
    that goes up by one, so the major is incremented and any minor is dropped.
    """
    m = re.match(r'^(.*?_?V)(\d+)(?:\.(\d+))?$', cur or '')
    if not m:
        raise SystemExit(f'cannot parse version {cur!r}')
    return f'{m.group(1)}{int(m.group(2)) + 1}'


def read_blob(src, name):
    m = re.search(rf"^{name} = '([A-Za-z0-9+/=]+)'", src, re.M)
    if not m:
        raise SystemExit(f'{name} not found or not a plain literal')
    return json.loads(zlib.decompress(base64.b64decode(m.group(1))).decode('utf-8')), m.span(1)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('note', help='one line: what changed in this version')
    ap.add_argument('--engine', default=ENGINE)
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args(argv)

    src = open(a.engine, encoding='utf-8').read()
    cur, span = current_version(src)
    if cur is None:
        raise SystemExit('LAZ_ENGINE_VERSION not found')
    new = next_version(cur)
    today = datetime.date.today().isoformat()
    entry = f'{new} ({today}) — {a.note.strip()}'
    print(f'  {cur}  ->  {new}')
    print(f'  {entry}')
    if a.dry_run:
        return 0

    # 1. the constant
    src = src[:span[0]] + new + src[span[1]:]

    # 2. the handover, inside the docs blob — prove_docs reads THIS, not the file
    docs, dspan = read_blob(src, 'laz_docs___BLOB')
    if HANDOVER not in docs:
        raise SystemExit(f'{HANDOVER} is not in the docs blob; prove_docs would fail')
    marker = '\n## ENGINE VERSIONS\n'
    if marker not in docs[HANDOVER]:
        docs[HANDOVER] += ('\n\n' + marker.strip() + '\n\n'
                           'Every change to the engine bumps LAZ_ENGINE_VERSION by one, on the\n'
                           "owner's instruction, so an output can always be traced to the build\n"
                           'that produced it. prove_docs asserts the current version is named here.\n\n')
    docs[HANDOVER] += f'- {entry}\n'
    packed = base64.b64encode(zlib.compress(
        json.dumps(docs, ensure_ascii=False).encode('utf-8'), 9)).decode('ascii')
    src = src[:dspan[0]] + packed + src[dspan[1]:]

    # 3. verify before writing: it must compile, and prove_docs' assertion must hold
    compile(src, a.engine, 'exec')
    v2, _ = current_version(src)
    d2, _ = read_blob(src, 'laz_docs___BLOB')
    assert v2 == new, 'the constant did not take'
    assert v2 in d2[HANDOVER], 'prove_docs would fail: the handover does not name the version'
    open(a.engine, 'w', encoding='utf-8').write(src)

    # 4. the plain-text changelog, readable without decoding anything
    ch = os.path.join(os.path.dirname(a.engine), 'CHANGELOG.md')
    head = ('# Engine versions\n\n'
            'One bump per change, newest first. `LAZ_ENGINE_VERSION` is stamped into every\n'
            'workbook, provenance sidecar and deployment bundle, so any output names the\n'
            'build that produced it.\n\n')
    old = open(ch, encoding='utf-8').read() if os.path.exists(ch) else head
    body = old[len(head):] if old.startswith(head) else old
    sha = hashlib.sha256(open(a.engine, 'rb').read()).hexdigest()
    open(ch, 'w', encoding='utf-8').write(head + f'- **{entry}**  \n  `sha256 {sha[:16]}`\n' + body)
    print(f'  engine   {sha[:16]}')
    print(f'  handover names {new}: yes')
    print(f'  CHANGELOG.md updated')
    return 0


if __name__ == '__main__':
    sys.exit(main())
