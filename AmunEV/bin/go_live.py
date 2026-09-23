#!/usr/bin/env python3
"""go_live.py — ONE COMMAND: search, validate, and write everything production needs.

    python3 go_live.py --engine AmunEV_Engine_V2.py --sports basketball

WHICH DO YOU RUN — THIS, run_m5.py, OR THE ENGINE DIRECTLY?

    This one. The engine is a library: it defines 60-odd modules and never calls
    itself, so `python3 AmunEV_Engine_V2.py` does nothing. run_m5.py is the real
    runner and this script does not replace it -- it EXTRACTS run_m5.py from the
    engine (it ships inside, compressed) so the runner and the engine are always
    the same build, preflights the things that actually break a run, invokes it,
    and then indexes everything that came out.

    Two problems this exists to stop, both of which cost a full run:
      1. run_m5.py finds the engine by globbing `LazarusEV_Engine_v*.py`.
         `AmunEV_Engine_V2.py` does not match that, and without --engine the
         runner dies on os.path.abspath(None) AFTER you have waited for boot.
         This script always passes --engine explicitly.
      2. The engine needs Python >= 3.12 (PEP 701 nested-quote f-strings) and
         pandas 2.x. On 3.11 it dies mid-boot with a SyntaxError that reads like
         an engine fault. Checked here, before anything starts.

WHAT YOU GET, per sport, after one run:

    Workbooks/LAZARUS_<SPORT>_MODE3_<ts>.xlsx     the book you read
    Workbooks/<same>.provenance.json              which engine built it
    production/<sport>/deploy.sql                 ONE transaction: feature columns,
                                                  laz_feature_namespace,
                                                  laz_strategy_registry,
                                                  laz_placement_policy, provenance
    production/<sport>/laz_features_<sport>.py    every term, 1:1 from the engine
    production/<sport>/laz_pca_features_<sport>.py the run's frozen PCA transforms
    production/<sport>/STRATEGY_SPEC.md           every element, in execution order
    production/<sport>/strategies_full.json       the same, machine-readable
    production/<sport>/verify_live.py             re-checks the DB. Reads only.
    production/<sport>/quarantine.csv             validated but not yet settleable
    production/<sport>/RUNBOOK.md                 what to run, in what order
    Logs/ · Rejections/ · DeployKits/ · mode3_<sport>.parquet

NOTHING HERE ADDS A RULE. It sets no threshold, no cap and no filter: every flag
it passes is either the engine's own default or something you typed. GOD-2.
"""
import argparse, ast, base64, glob, hashlib, json, os, shutil, subprocess, sys, time, zlib

HERE = os.path.dirname(os.path.abspath(__file__))
RESET = '\033[0m' if sys.stdout.isatty() else ''
def _c(code, s):
    return f'\033[{code}m{s}{RESET}' if sys.stdout.isatty() else str(s)
OK, BAD, WARN, DIM = (lambda s: _c('32', s)), (lambda s: _c('31', s)), (lambda s: _c('33', s)), (lambda s: _c('2', s))


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


# ── PREFLIGHT ───────────────────────────────────────────────────────────────
# Static only. Nothing here imports or executes the engine: the file is parsed,
# the sealed rules are hashed from its source, and that is all.
def preflight(engine, frames, sports, strict=True):
    fails, notes = [], []

    if sys.version_info < (3, 12):
        fails.append(f'python {sys.version.split()[0]} — the engine needs >= 3.12 '
                     '(its f-strings use PEP 701 nested quotes; on 3.11 it dies mid-boot '
                     'with a SyntaxError that looks like an engine fault)')
    else:
        notes.append(f'python {sys.version.split()[0]}')

    for mod, why in (('numpy', ''), ('pandas', 'must be 2.x'), ('pyarrow', 'reads the frames'),
                     ('openpyxl', 'writes the workbook'), ('joblib', '')):
        try:
            m = __import__(mod)
            v = getattr(m, '__version__', '?')
            if mod == 'pandas' and str(v).split('.')[0] != '2':
                fails.append(f'pandas {v} — the engine is proven on pandas 2.x (pin: pandas==2.3.3)')
            else:
                notes.append(f'{mod} {v}')
        except ImportError:
            fails.append(f'{mod} is not installed under this interpreter{" — " + why if why else ""}')

    if not os.path.exists(engine):
        fails.append(f'engine not found: {engine}')
        return fails, notes, {}

    src = open(engine, encoding='utf-8', errors='ignore').read()
    try:
        compile(src, engine, 'exec')          # compile, not ast.parse: parse accepts
        notes.append('engine compiles')       # duplicate kwargs that compile rejects
    except SyntaxError as e:
        fails.append(f'engine will not compile: line {e.lineno}: {e.msg}')
        return fails, notes, {}

    seals = _read_seals(src)
    for rid, got in sorted(seals.items()):
        if got['ok']:
            notes.append(f'{rid} seal intact ({got["sha"][:16]})')
        else:
            fails.append(f'{rid} HAS BEEN ALTERED. It may be changed only by you, and only '
                         f'by the explicit reply "{got["phrase"]}"')

    rules = _read_owner_rules(src)
    if rules:
        checks = [('min_odds_hard', 1.4, 'the mode-3 floor'),
                  ('min_odds', 1.5, 'the floor for every other mode'),
                  ('min_n_is', 100, ''), ('min_n_oos', 100, ''), ('min_oos_roi', 0.0, 'ROI > 0')]
        for k, want, why in checks:
            got = rules.get(k)
            if got is None:
                fails.append(f'rules.{k} is missing')
            elif float(got) != float(want):
                fails.append(f'rules.{k} is {got!r}, your rule is {want!r}'
                             + (f' ({why})' if why else ''))
        if rules.get('band_hi_ceiling') is not None:
            fails.append(f'rules.band_hi_ceiling is {rules["band_hi_ceiling"]!r} — there is NO '
                         'maximum odds. A ceiling here refuses exactly the long-odds bets the '
                         'search validated (GOD-2 rule 1)')
        bmx = rules.get('band_min_matches_x')
        if bmx is not None:
            notes.append(f'rung floor {bmx} x min_n' + (DIM('  (was 2.0; 1.0 searches every '
                         'rung that can still meet n>=100)') if float(bmx) <= 1.0 else
                         WARN('  <- 2.0 skips thin long-odds rungs')))
        notes.append('rules: min_odds 1.40 (mode 3) / 1.50 (other) · NO maximum · '
                     'n_is>=100 · n_oos>=100 · ROI>0')

    # THE ASSEMBLY INVARIANT. A strategy family list that is consumed -- copied into a
    # registry, or walked to stamp defaults onto every member -- and then CHANGED again
    # further down leaves the consumer holding a snapshot. That is how 220 strategies sat
    # in the file, fully defined and documented, while being invisible to the registry.
    # Checked here so it can never come back silently.
    try:
        import subprocess as _sp
        _aud = os.path.join(os.path.dirname(HERE), 'audit', 'laz_assembly_audit.py')
        if os.path.exists(_aud):
            r = _sp.run([sys.executable, _aud, engine], capture_output=True, text=True)
            head = [l for l in r.stdout.splitlines() if l.strip()][1:2]
            if r.returncode == 0:
                notes.append((head[0].strip() if head else 'assembly invariant holds'))
            else:
                fails.append('ASSEMBLY: ' + (head[0].strip() if head else 'a family list is '
                             'consumed and then changed again — the consumer holds a stale '
                             'snapshot') + f'   (run: python3 {_aud} {engine})')
    except Exception as _e:
        notes.append(f'assembly audit not run ({type(_e).__name__})')

    fdir = os.path.abspath(frames)
    if not os.path.isdir(fdir):
        fails.append(f'frames directory not found: {fdir}')
    else:
        for sp in sports:
            hits = glob.glob(os.path.join(fdir, f'AB_{sp}.parquet'))
            if hits:
                mb = os.path.getsize(hits[0]) / 1e6
                notes.append(f'frame {os.path.basename(hits[0])} {mb:,.0f} MB')
            else:
                fails.append(f'no frame for {sp}: expected {fdir}/AB_{sp}.parquet')

    # THE ONE FILE IN THE FOLDER THAT CAN ACTUALLY HIJACK A RUN.
    # run_m5.py picks its engine by globbing LazarusEV_Engine_v*.py and taking the LAST
    # one alphabetically. This script always passes --engine so it cannot happen here --
    # but the moment you or a script runs run_m5.py by hand without it, a stale engine
    # sitting in the folder is the one that runs, and the log will name it while you read
    # the numbers as if they came from the engine you meant.
    strays = sorted(glob.glob(os.path.join(os.path.dirname(engine) or '.',
                                           'LazarusEV_Engine_v*.py')))
    strays = [p for p in strays if os.path.abspath(p) != os.path.abspath(engine)]
    if strays:
        notes.append(WARN(f'{len(strays)} other engine file(s) match run_m5.py\'s glob: '
                          + ', '.join(os.path.basename(p) for p in strays)
                          + '. Harmless here (--engine is always passed) but whichever '
                            'sorts LAST would win if run_m5.py is ever run by hand'))

    try:
        free = shutil.disk_usage(os.getcwd()).free / 1e9
        (notes if free >= 5 else fails).append(
            f'{free:,.1f} GB free' + ('' if free >= 5 else ' — a run needs several GB for the '
                                      'pool, the workbook and the bundle'))
    except Exception:
        pass
    return fails, notes, rules


def _read_seals(src):
    """Hash each sealed rule straight out of the source. No execution."""
    out = {}
    tree = ast.parse(src)
    consts = {}
    for n in tree.body:
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            name = n.targets[0].id
            if name.startswith('LAZ_GOD'):
                try:
                    consts[name] = ast.literal_eval(n.value)
                except Exception:
                    pass
    for rid, text_k, sha_k, phrase in (('GOD-1', 'LAZ_GOD_RULE', 'LAZ_GOD_RULE_SHA', 'Unchained'),
                                       ('GOD-2', 'LAZ_GOD2_RULE', 'LAZ_GOD2_RULE_SHA',
                                        'Unchained approves')):
        if text_k in consts and sha_k in consts:
            got = hashlib.sha256(consts[text_k].encode()).hexdigest()
            out[rid] = dict(ok=(got == consts[sha_k]), sha=consts[sha_k], phrase=phrase)
    return out


def _read_owner_rules(src):
    """LAZ_OWNER['rules'], read from the source. No execution."""
    for n in ast.parse(src).body:
        if isinstance(n, ast.Assign) and any(getattr(t, 'id', '') == 'laz_owner__LAZ_OWNER'
                                             for t in n.targets):
            for k, v in zip(n.value.keys, n.value.values):
                if getattr(k, 'value', None) == 'rules':
                    try:
                        return ast.literal_eval(v)
                    except Exception:
                        return {}
    return {}


def extract_runner(engine, home):
    """Write the runner that ships INSIDE this engine. Nothing is executed.

    NAMED FOR THE ENGINE, NOT `run_m5.py`. The first version of this wrote
    `<home>/run_m5.py` and silently overwrote whatever was already there. If you
    keep your own run_m5.py -- with your own flags, paths or edits -- it was gone,
    with no message and no backup, and the next run used a runner you did not
    write. The file is now named for the engine's hash, so it can never collide
    with yours and you can see at a glance which engine it came from.

    Returns (path, note). note is set when an existing run_m5.py differs, so you
    are told rather than left to find out.
    """
    sha8 = sha256(engine)[:8]
    dest = os.path.join(home, f'run_m5_{sha8}.py')
    for n in ast.parse(open(engine, encoding='utf-8', errors='ignore').read()).body:
        if isinstance(n, ast.Assign) and any(getattr(t, 'id', '') == 'laz_runner___BLOB'
                                             for t in n.targets):
            src = zlib.decompress(base64.b64decode(ast.literal_eval(n.value))).decode('utf-8')
            with open(dest, 'w', encoding='utf-8') as fh:
                fh.write(src)
            note = ''
            yours = os.path.join(home, 'run_m5.py')
            if os.path.exists(yours):
                same = (open(yours, encoding='utf-8', errors='ignore').read() == src)
                note = ('your run_m5.py is identical to the one in this engine — '
                        'nothing to reconcile' if same else
                        f'your run_m5.py DIFFERS from the one in this engine. Yours was NOT '
                        f'touched and NOT used; this run used {os.path.basename(dest)}. '
                        f'diff them if you meant to keep your changes')
            return dest, note
    return None, ''


# ── THE RUN ─────────────────────────────────────────────────────────────────
def run(engine, runner, sports, frames, extra, home, log_to):
    """Invoke run_m5.py. --engine is ALWAYS passed: the runner globs for
    LazarusEV_Engine_v*.py and would otherwise die on abspath(None) after boot."""
    cmd = [sys.executable, runner,
           '--engine', os.path.abspath(engine),
           '--frames', os.path.abspath(frames),
           '--sports', ','.join(sports),
           '--mode', '3'] + list(extra)
    env = dict(os.environ)
    env['LAZ_OUTPUT_DIR'] = home
    env.setdefault('VECLIB_MAXIMUM_THREADS', '1')
    print(DIM('  ' + ' '.join(cmd)))
    t0 = time.time()
    with open(log_to, 'w', encoding='utf-8') as lf:
        p = subprocess.Popen(cmd, cwd=home, env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in p.stdout:
            sys.stdout.write(line)
            lf.write(line)
            lf.flush()
        p.wait()
    return p.returncode, time.time() - t0


# ── WHAT CAME OUT ───────────────────────────────────────────────────────────
def index(home, sports, since):
    """Find every artefact, and refuse to count anything this run did not write.

    THE FAILURE THIS ENDS. The first version read production/<sport>/MANIFEST.json
    with no idea how old it was. If a run died before writing its bundle, LAST
    week's bundle was still sitting there -- and this script read it, called the
    sport READY, and printed the psql command to deploy it. You would have loaded
    a registry built by an engine you had already replaced, believing it was the
    run you just watched.

    So every file is stamped, and anything older than the moment the run started
    is STALE: never counted, never READY, and the deploy commands are never
    printed for it.
    """
    rows, ready = [], {}
    for sp in sports:
        d = os.path.join(home, 'production', sp)
        man = os.path.join(d, 'MANIFEST.json')
        m, stale = {}, False
        if os.path.exists(man):
            stale = os.path.getmtime(man) < since
            try:
                m = json.load(open(man))
            except Exception:
                m = {}
        ready[sp] = dict(dir=d, manifest=m, stale=stale,
                         ok=(bool(m) and not stale and m.get('preflight') == 'PASS'
                             and m.get('n_deployable', 0) > 0))
        for f in sorted(glob.glob(os.path.join(d, '*'))):
            if os.path.isfile(f):
                rows.append((sp, 'bundle', f, os.path.getmtime(f) >= since))
    for kind in ('Workbooks', 'Logs', 'Rejections', 'DeployKits', 'Propositions', 'Learning'):
        for f in sorted(glob.glob(os.path.join(home, kind, '*'))):
            if os.path.isfile(f):
                rows.append(('', kind, f, os.path.getmtime(f) >= since))
    for f in sorted(glob.glob(os.path.join(home, 'mode3_*.parquet'))
                    + glob.glob(os.path.join(home, 'laz_pca_*.json'))
                    + glob.glob(os.path.join(home, 'LAZ_BOOK*'))):
        rows.append(('', 'run', f, os.path.getmtime(f) >= since))
    return rows, ready


def report(home, sports, rows, ready, secs, rc):
    print()
    print('=' * 78)
    print(f'  RUN {"FINISHED" if rc == 0 else "FAILED (exit %d)" % rc} in {secs/60:,.1f} min'
          f'   ·   {home}')
    print('=' * 78)
    for sp in sports:
        r = ready[sp]
        m = r['manifest']
        if not m:
            print(f'\n  {BAD("NO BUNDLE")}  {sp}  — production/{sp}/MANIFEST.json was not written. '
                  f'The workbook may still be there; see the log.')
            continue
        if r['stale']:
            print(f'\n  {BAD("STALE")}  {sp.upper()}  — production/{sp}/ is from an EARLIER run '
                  f'(written {time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(os.path.join(r["dir"], "MANIFEST.json"))))}). '
                  f'This run wrote no bundle for it. DO NOT DEPLOY IT — it was built by a '
                  f'different engine. See the log for why the run produced nothing.')
            continue
        tag = OK('READY') if r['ok'] else WARN('CHECK')
        print(f'\n  {tag}  {sp.upper()}')
        print(f'         {m.get("n_deployable", 0)} live  ·  {m.get("n_withheld", 0)} registered '
              f'not-live  ·  {m.get("n_validated", 0)} validated this run')
        print(f'         {m.get("n_feature_columns", 0)} feature columns  ·  '
              f'{m.get("n_pca", 0)} PCA composites  ·  preflight {m.get("preflight")}')
        gap = m.get('terms_the_generator_could_not_write') or []
        if gap:
            print(WARN(f'         {len(gap)} term(s) the feature generator could not write — '
                       f'those strategies load but will not fire: {", ".join(gap[:6])}'
                       + (' …' if len(gap) > 6 else '')))
        for f in m.get('preflight_failures', [])[:5]:
            print(BAD(f'         {f.get("rule")} {f.get("name")}: {f.get("production_error")}'))
    print()
    fresh = [r for r in rows if r[3]]
    old = [r for r in rows if not r[3]]
    print(f'  {len(fresh)} file(s) written by THIS run'
          + (WARN(f'   ·   {len(old)} older file(s) in the same folders, ignored') if old else ''))
    seen = set()
    for sp, kind, f, _ in fresh:
        k = f'{sp or "-"}/{kind}'
        if k in seen:
            continue
        seen.add(k)
        n = sum(1 for a, b, _p, _fr in fresh if (a or '-') + '/' + b == k)
        print(f'    {kind:12} {("("+sp+")") if sp else "":14} {n:>3} file(s)   '
              f'{DIM(os.path.dirname(f))}')
    live = [sp for sp in sports if ready[sp]['ok']]
    if live:
        print()
        print('  ' + OK('TO GO LIVE') + ', per sport:')
        for sp in live:
            d = os.path.relpath(ready[sp]['dir'], home)
            print(f'    psql "$BETSMITH_DSN" -v ON_ERROR_STOP=1 -f {d}/deploy.sql')
            print(f'    cp {d}/laz_features_{sp}.py {d}/laz_pca_features_{sp}.py  /opt/betsmith/')
            print(f'    python3 {d}/verify_live.py "$BETSMITH_DSN"')
            print(f'    {DIM("read " + d + "/RUNBOOK.md first — it says how to wire the two modules in")}')
    print()


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='Search, validate, and write everything production needs — one command.',
        epilog='Anything after -- is passed straight to run_m5.py, e.g.  '
               '-- --workers 8 --target-new 200')
    ap.add_argument('--engine', default=None,
                    help='the engine file (default: AmunEV_Engine_V2.py beside this script '
                         'or in the current directory)')
    ap.add_argument('--sports', default='basketball',
                    help='comma-separated; e.g. basketball,ebasketball,football')
    ap.add_argument('--frames', default='frames', help='directory holding AB_<sport>.parquet')
    ap.add_argument('--out', default=None,
                    help='run home; everything is written under here (default: cwd)')
    ap.add_argument('--preflight-only', action='store_true',
                    help='check everything and stop. Touches nothing, runs nothing.')
    ap.add_argument('--force', action='store_true',
                    help='run even if preflight failed. Only for a failure you have read '
                         'and decided to accept.')
    a, passthrough = ap.parse_known_args(argv)
    if passthrough and passthrough[0] == '--':
        passthrough = passthrough[1:]

    engine = a.engine
    if engine is None:
        for c in (os.path.join(HERE, '..', 'AmunEV_Engine_V2.py'),
                  os.path.join(HERE, 'AmunEV_Engine_V2.py'), 'AmunEV_Engine_V2.py'):
            if os.path.exists(c):
                engine = os.path.abspath(c)
                break
    if engine is None:
        print(BAD('No engine given and AmunEV_Engine_V2.py is not beside this script '
                  'or in the current directory. Pass --engine.'))
        return 2
    engine = os.path.abspath(engine)
    sports = [s.strip() for s in a.sports.split(',') if s.strip()]
    home = os.path.abspath(a.out or os.getcwd())
    os.makedirs(home, exist_ok=True)

    print()
    print(f'  engine   {os.path.basename(engine)}')
    print(f'  sha256   {sha256(engine)}')
    print(f'  sports   {", ".join(sports)}')
    print(f'  home     {home}')
    print()
    print('  PREFLIGHT' + DIM('  (static — the engine is parsed, never executed)'))
    fails, notes, _rules = preflight(engine, a.frames, sports)
    for n in notes:
        print(f'    {OK("ok")}   {n}')
    for f in fails:
        print(f'    {BAD("FAIL")} {f}')
    if fails and not a.force:
        print()
        print(BAD(f'  {len(fails)} check(s) failed. Nothing was run. '
                  'Fix them, or pass --force if you have read them and accept the risk.'))
        return 1
    if a.preflight_only:
        print()
        print(OK('  preflight only — nothing was run.'))
        return 0

    runner, note = extract_runner(engine, home)
    if runner is None:
        print(BAD('  could not extract run_m5.py from the engine (laz_runner___BLOB missing)'))
        return 2
    print(f'    {OK("ok")}   {os.path.basename(runner)} extracted from this engine '
          + DIM(f'({sum(1 for _ in open(runner))} lines) — runner and engine are the same build'))
    if note:
        print(f'    {WARN("note")} {note}')

    stamp = time.strftime('%Y%m%d_%H%M%S')
    log_to = os.path.join(home, f'go_live_{stamp}.log')
    print()
    print(f'  RUNNING  mode 3' + DIM(f'   · full output also in {os.path.basename(log_to)}'))
    since = time.time()
    rc, secs = run(engine, runner, sports, a.frames, passthrough, home, log_to)
    rows, ready = index(home, sports, since)
    report(home, sports, rows, ready, secs, rc)
    if rc != 0:
        print(BAD(f'  run_m5.py exited {rc}. The log is {log_to}'))
    return rc


if __name__ == '__main__':
    sys.exit(main())
