#!/usr/bin/env python3.12
"""GOD-1: the documentation law. Extracted by AST; the engine is never run."""
import ast, hashlib, json, os, re, sys, types, tempfile

ENGINE='AmunEV_Engine_V2.py'
src=open(ENGINE,encoding='utf-8').read(); tree=ast.parse(src)
WANT=['laz_god__verify','laz_featdoc__document_now','laz_featdoc__gate']
ns={}
# the module-level constants the rule depends on
for n in tree.body:
    if isinstance(n,ast.Assign) and any(getattr(t,'id','') in
        ('LAZ_GOD_RULE','LAZ_GOD_RULE_ID','LAZ_GOD_RULE_SHA','laz_featdoc__REGISTRY','laz_featdoc__REQUIRED')
        for t in n.targets):
        exec(compile(ast.Module(body=[n],type_ignores=[]),ENGINE,'exec'), ns)
for n in tree.body:
    if getattr(n,'name',None) in WANT:
        exec(compile(ast.Module(body=[n],type_ignores=[]),ENGINE,'exec'), ns)
ns['_m']=lambda x: None
ns['laz_sink__swallow']=lambda *a, **k: None
ns['globals']=lambda: ns
print('extracted GOD-1 by AST — engine never imported\n')

verify, doc, gate = ns['laz_god__verify'], ns['laz_featdoc__document_now'], ns['laz_featdoc__gate']
fails=[]
def check(n,c,d=''):
    print(f'  {"PASS" if c else "FAIL"}  {n}'+(f'  — {d}' if d and not c else ''))
    if not c: fails.append(n)

print('the rule is sealed and untouchable')
check('the seal verifies', verify() is True)
check('the seal is a real sha256 of the text',
      hashlib.sha256(ns['LAZ_GOD_RULE'].encode()).hexdigest()==ns['LAZ_GOD_RULE_SHA'])
check('the rule names all seven requirements',
      all(k in ns['LAZ_GOD_RULE'] for k in ('NAME','ORIGIN','RECIPE','INPUTS','CAUSALITY','RESOLVES_AT','REPLICATION STEPS')))
check('the rule states it may only change on "Unchained"', 'Unchained' in ns['LAZ_GOD_RULE'])
check('the rule states there is no UNKNOWN', 'There is no UNKNOWN' in ns['LAZ_GOD_RULE'])
# tamper with it
_keep=ns['LAZ_GOD_RULE']; ns['LAZ_GOD_RULE']=_keep.replace('untouchable','advisory')
try:
    verify(); tampered_raised=False
except RuntimeError as e:
    tampered_raised='ALTERED' in str(e) and 'Unchained' in str(e)
ns['LAZ_GOD_RULE']=_keep
check('weakening the rule text is DETECTED and refused', tampered_raised)
check('the seal verifies again once restored', verify() is True)

print('\ndocumentation at creation')
ns['laz_featdoc__REGISTRY'].clear()
r=doc('pc_0', origin='PCA composite', recipe='((X-mu)/sdv) @ w', inputs=['a','b'],
      replication_steps=['1. compute a,b','2. standardise','3. project'])
check('a complete record is complete', r['complete'] is True, str(r['missing']))
check('it is recorded at creation', r['documented_at']=='creation')
check('it carries the rule id', r['rule']=='GOD-1')
check('it is keyed by the POOL NAME', 'pc_0' in ns['laz_featdoc__REGISTRY'])
r2=doc('bad_term', origin='', recipe='', inputs=[], replication_steps=[])
check('an incomplete record is flagged', r2['complete'] is False)
check('and names exactly what is missing',
      set(r2['missing'])>={'origin','recipe','inputs','replication_steps'}, str(r2['missing']))
raised=False
try: doc('worse', origin='x', recipe='', inputs=[], replication_steps=[], strict=True)
except RuntimeError as e: raised='cannot be rebuilt in production' in str(e)
check('strict=True refuses at creation', raised)

print('\nthe pre-sweep gate')
ns['laz_featdoc__REGISTRY'].clear()
doc('good', origin='o', recipe='r', inputs=['x'], replication_steps=['1. x'])
cwd=os.getcwd(); tmp=tempfile.mkdtemp(); os.chdir(tmp)
try:
    logs=[]
    res=gate({'good':1,'undocumented_term':2}, 'basketball', log=logs.append, strict=False)
    check('the gate finds the undocumented term', res['undocumented']==['undocumented_term'], str(res))
    check('it names it in the log', any('UNDOCUMENTED undocumented_term' in l for l in logs))
    check('it reports the documented ratio', any('1/2 pool terms fully documented' in l for l in logs), str(logs[:3]))
    check('it writes the documentation to disk',
          os.path.exists('laz_feature_documentation_basketball.json'))
    j=json.load(open('laz_feature_documentation_basketball.json'))
    check('the file names the rule and the gap', j['rule']=='GOD-1' and j['undocumented']==['undocumented_term'])
    check('the file carries the full registry', 'good' in j['registry'])
    raised=False
    try: gate({'good':1,'undocumented_term':2}, 'basketball', log=lambda *a: None, strict=True)
    except RuntimeError as e: raised='GOD-1 REFUSES THIS RUN' in str(e)
    check('strict=True REFUSES the run', raised)
    check('a fully documented pool passes',
          gate({'good':1}, 'basketball', log=lambda *a: None, strict=True)['undocumented']==[])
finally:
    os.chdir(cwd)

print('\nwired into the engine at the points that failed')
check('PCA documents at creation, under the pool name',
      "laz_featdoc__document_now(\n                                                        f'pc_{_ci}'" in src
      or "f'pc_{_ci}'," in src.split('laz_featdoc__document_now')[1][:200])
check('the gate runs before the sweep dispatches',
      src.index('laz_featdoc__gate(pool, sport')<src.index('_tasks.sort(key=lambda t: -t[1])'))
check('the PCA record carries frozen mu/sdv/w', 'mu=[float(x) for x in _mu]' in src)
check('the PCA record carries replication steps', 'standardise each input' in src)

print(f'\n{"ALL PASS" if not fails else "FAILURES: "+", ".join(fails)}')
sys.exit(1 if fails else 0)
