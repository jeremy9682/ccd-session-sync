#!/usr/bin/env python3
"""Standard-library adversarial tests. Every data path is a disposable fixture.

Run: python3 tests/run_suite.py (prepares all disposable sources).
Optional filters: --group core|negative|generated|fault|mutation
"""
from run_synthetic import *
import argparse
import random
import signal
import sys
import traceback

parser = argparse.ArgumentParser()
parser.add_argument('--group', default='all')
args = parser.parse_args()
OUT = ROOT/'implementation-evidence'
OUT.mkdir(exist_ok=True)
OBSERVATIONS = []


class F(Fixture):
    def run(self, argv, check=False, timeout=45, env=None):
        env = env or self.env
        assert Path(env['HOME']).resolve().is_relative_to(RUN)
        for key in ('CCD_CLAUDE_DIR', 'CCD_GIT_DIR', 'CCD_ARCHIVE_DIR', 'CCD_PROJECTS_DIR'):
            assert Path(env[key]).resolve().is_relative_to(RUN)
        for p in self.home.rglob('*'):
            if p.is_symlink():
                assert p.resolve(strict=False).is_relative_to(RUN), p
        start = time.monotonic()
        p = subprocess.run(list(map(str, argv)), cwd=self.dir, env=env,
                           text=True, capture_output=True, timeout=timeout)
        with LOG.open('a') as out:
            out.write(json.dumps({'case':self.name,'version':self.version,'command':list(map(str,argv)),
                       'env':env,'cwd':str(self.dir),'exit_code':p.returncode,'stdout':p.stdout,
                       'stderr':p.stderr,'seconds':round(time.monotonic()-start,3)})+'\n')
        if check:
            assert p.returncode == 0, (argv,p.returncode,p.stdout,p.stderr)
        return p

    def init(self):
        assert self.install().returncode == 0
        return self

    def snap(self):
        # Data, tracked HEAD and index bytes: blocked calls must preserve all.
        return [digest_tree(self.wt), self.head(), sha(self.gd/'index')]

    def ok(self, *a):
        p = self.migrate(*a)
        assert p.returncode == 0, (p.stdout,p.stderr)
        assert self.preserved()
        return p

    def blocked(self, *a, contains=None):
        before = self.snap()
        p = self.migrate(*a)
        assert p.returncode != 0, p.stdout
        assert self.snap() == before, ('blocked call changed state',p.stdout,p.stderr)
        assert self.preserved()
        if contains:
            assert contains in p.stdout+p.stderr, (contains,p.stdout,p.stderr)
        return p


def test(group, name, fn):
    if args.group not in ('all',group):
        return
    try:
        detail = fn()
        row={'group':group,'test':name,'status':'PASS','detail':detail}
    except Exception as e:
        row={'group':group,'test':name,'status':'FAIL','error':str(e),'traceback':traceback.format_exc()}
    OBSERVATIONS.append(row)
    print(json.dumps(row,ensure_ascii=False),flush=True)
    (RUN/'adversarial-results.json').write_text(json.dumps(OBSERVATIONS,indent=2,ensure_ascii=False))


def standard(name, version='candidate'):
    f=F(version,name)
    f.entry(A,'s','source',2000)
    f.init()
    return f


def basic():
    f=standard('basic space unicode 路径')
    before=f.snap();f.ok('--dry-run');assert f.snap()==before
    f.ok(); first=f.head(); f.ok();assert f.head()==first
    f.entry(B,'drift','preserve unrelated',3000)
    f.ok('undo');assert (f.org(B)/'local_drift.json').exists()
    f.blocked('undo',contains='already reverted')
    assert not (f.org(B)/'local_s.json').exists()
    assert all((ROOT/'candidate'/p).stat().st_mode & 0o111 for p in ['install.sh','uninstall.sh','bin/ccd-migrate','bin/ccd-migrate-auto','bin/claude-archive-sync','bin/claude-second'])
    p=f.run([f.home/'bin/ccd-migrate','--help']);assert p.returncode==0
    assert (f.home/'bin/ccd-safety.py').is_file()
    return 'dry-run/no-op; installed entry; drift preserved; repeated undo stops'
test('core','basic_idempotence_install',basic)


def subjects():
    f=standard('subject_history');f.ok()
    for i,subject in enumerate(['snapshot: ordinary\n\nmigrate: malicious body','migrate:unexpected','Revert "migrate: fake"','unicode ☃ migrate: x']):
        f.entry(B,'unrelated'+str(i),'preserve '+subject)
        f.commit(subject)
    f.ok('undo')
    assert not (f.org(B)/'local_s.json').exists()
    assert len(list(f.org(B).glob('local_unrelated*.json')))==4
    return 'bodies/prefixes/revert/unrelated titles excluded; file assertions'
test('core','subject_only_selection',subjects)


def sigpipe(version):
    f=standard('pipe_pressure_'+version,version)
    for i in range(29):f.git('commit','--allow-empty','-q','-m','snapshot: '+str(i)+'x'*16384)
    assert f.migrate().returncode==0
    p=f.migrate('undo')
    assert p.returncode==(141 if version=='base' else 0),(version,p.returncode,p.stderr)
    assert (f.org(B)/'local_s.json').exists()==(version=='base')
    return {'exit':p.returncode,'version':version}
for v in ('base','head','candidate'):test('negative','sigpipe_'+v,lambda v=v:sigpipe(v))


def marker_matrix():
    f=F('candidate','markers')
    ids=['ascii','空 格','quote"$();[*]','e\u0301','😀']
    for i,name in enumerate(ids):
        f.entry(A,name,'source '+name,9000,cli='transcript-'+name)
        mark='deleted_'+(name if i%2==0 else 'transcript-'+name)
        write(f.org(B)/mark,['1700000000000','','not-a-time','-1','999999999999999999999'][i])
    f.entry(A,'update','source update',9999)
    target=f.entry(B,'update','old retained',1); original=target.read_bytes()
    write(f.org(B)/'deleted_update','bad timestamp')
    f.entry(A,'sourceonly','source still present',9000)
    write(f.org(A)/'deleted_sourceonly','1000')
    f.init();before=digest_tree(f.org(A));marks={p.name:p.read_bytes() for p in f.org(B).glob('deleted_*')}
    out=f.ok(); assert 'deleted_skipped=6' in out.stdout
    assert all(not (f.org(B)/('local_'+name+'.json')).exists() for name in ids)
    assert target.read_bytes()==original
    assert (f.org(B)/'local_sourceonly.json').exists()
    assert before==digest_tree(f.org(A))
    assert marks=={p.name:p.read_bytes() for p in f.org(B).glob('deleted_*')}
    f.ok('undo');assert target.read_bytes()==original
    return 'both IDs; marker presence precedence including malformed/empty; source markers never delete other account'
test('core','marker_precedence_literal_unicode',marker_matrix)


def issue_negative(version,issue):
    f=standard('negative_'+str(issue),version);assert f.migrate().returncode==0
    if issue==2:
        t=f.org(B)/'local_s.json';t.unlink();write(f.org(B)/'deleted_s','1700000000000')
        p=f.migrate();assert p.returncode==0
        fixed=not t.exists()
    elif issue==3:
        assert f.migrate('undo').returncode==0
        p=f.migrate('undo');fixed='already reverted' in p.stdout+p.stderr
    elif issue=='body':
        t=f.entry(B,'manual','unrelated');f.commit('snapshot: note\n\nmigrate: fake body')
        p=f.migrate('undo');fixed=t.exists() and not (f.org(B)/'local_s.json').exists()
    else:
        t=f.org(B)/'local_s.json';write(t,'<<<<<<< invalid')
        before=f.head();p=f.migrate();fixed=p.returncode!=0 and f.head()==before
    assert fixed==(version=='candidate'),(version,issue,fixed,p.stdout,p.stderr)
    return {'version':version,'issue':issue,'fixed':fixed,'exit':p.returncode}
for v in ('head','candidate'):
    for issue in (2,3,'body','invalid'):
        test('negative',str(issue)+'_'+v,lambda v=v,issue=issue:issue_negative(v,issue))


def bad_input(value, field='file'):
    f=standard('bad_'+hashlib.sha256(repr(value).encode()).hexdigest()[:10])
    if field=='file':write(f.org(A)/'local_bad.json',value)
    elif field=='cli':f.entry(A,'bad','bad',cli=value)
    elif field=='ops':write(f.wt/'cowork-enabled-cli-ops.json',value)
    f.blocked();f.blocked('undo');f.blocked('snapshot')
    return 'blocked all mutating commands with unchanged data/HEAD/index'
for i,value in enumerate(['','{','[]','null','123','{"x":NaN}','{"title":"a","title":"b"}']):
    test('core','malformed_json_'+str(i),lambda value=value:bad_input(value))
for i,value in enumerate(['../escape','a/b','a\\b','.', '..', '\x00', 'line\nfeed',42,True]):
    test('core','unsafe_cli_'+str(i),lambda value=value:bad_input(value,'cli'))
test('core','malformed_owner',lambda:bad_input('{"ownerAccountId":null}','ops'))


def empty_and_args():
    f=F('candidate','empty').init();f.blocked('undo',contains='nothing to undo')
    f.ok();first=f.head();f.ok();assert f.head()==first
    for a in [('undo','unexpected'),('migrate','--unknown'),('--wat',),('log','--all'),('unknown',)]:f.blocked(*a)
    (f.wt/'cowork-enabled-cli-ops.json').unlink();f.blocked(contains='owner metadata missing')
    return 'no-op runs and invalid arguments do not stage drift'
test('core','empty_missing_unknown',empty_and_args)


def unsafe_fs(kind):
    f=standard('unsafe_'+kind)
    scratch=RUN/('outside_fixture_'+kind);scratch.mkdir();sentinel=scratch/'sentinel';write(sentinel,'SAFE')
    if kind=='file_symlink':(f.org(B)/'local_s.json').symlink_to(sentinel)
    elif kind=='org_symlink':
        shutil.rmtree(f.org(B));f.org(B).symlink_to(scratch,target_is_directory=True)
    elif kind=='git_symlink':(f.gd/'info/evil-link').symlink_to(sentinel)
    elif kind=='dangling_marker':(f.org(B)/'deleted_s').symlink_to(scratch/'missing')
    elif kind=='fifo':os.mkfifo(f.org(B)/'local_fifo.json')
    elif kind=='hardlink':os.link(sentinel,f.org(B)/'local_hard.json')
    elif kind=='directory_target':(f.org(B)/'local_s.json').mkdir()
    before=sentinel.read_bytes();head=f.head()
    p=f.migrate();assert p.returncode!=0,(kind,p.stdout,p.stderr)
    assert sentinel.read_bytes()==before and f.head()==head
    assert not (f.gd/'ccd-operation.json').exists()
    return {'kind':kind,'exit':p.returncode,'external_scratch_sentinel':'preserved'}
for kind in ['file_symlink','org_symlink','git_symlink','dangling_marker','fifo','hardlink','directory_target']:
    test('core','path_'+kind,lambda kind=kind:unsafe_fs(kind))


def pending(kind):
    f=standard('pending_'+kind)
    write(f.gd/kind,'' if kind=='ccd-operation.json' else 'synthetic pending state')
    f.blocked();f.blocked('undo');f.blocked('snapshot')
    return 'presence alone blocks; malformed/empty pending metadata not ignored'
for kind in ['REVERT_HEAD','CHERRY_PICK_HEAD','MERGE_HEAD','rebase-merge','sequencer','index.lock','ccd-operation.json']:
    test('core','pending_'+kind,lambda kind=kind:pending(kind))


def conflict_and_staging():
    f=standard('dirty_target');f.ok();p=f.org(B)/'local_s.json';write(p,{'title':'valid divergent edit','cliSessionId':'cli-s'})
    f.blocked('undo',contains='uncommitted edits');assert '<<<<<<<' not in p.read_text()
    f.git('add','--',str(p.relative_to(f.wt)))
    f.blocked();f.blocked('undo',contains='pre-staged')
    f.git('reset','--quiet')
    blob=f.git('rev-parse','HEAD:'+str(p.relative_to(f.wt))).stdout.strip()
    rel=str(p.relative_to(f.wt))
    data=f'0 {"0"*40}\t{rel}\n100644 {blob} 1\t{rel}\n100644 {blob} 2\t{rel}\n100644 {blob} 3\t{rel}\n'
    proc=subprocess.run([GIT,'--git-dir='+str(f.gd),'--work-tree='+str(f.wt),'update-index','--index-info'],input=data,text=True,env=f.env,cwd=f.dir,capture_output=True);assert proc.returncode==0
    f.blocked();f.blocked('undo',contains='unmerged')
    return 'dirty touched path; prestaged index; real unmerged index block before writes'
test('core','dirty_conflicted_repository',conflict_and_staging)


def history(kind):
    f=standard('history_'+kind);f.ok();migration=f.head()
    if kind=='forged_summary':
        f.entry(B,'manual','unrelated');f.commit('migrate: definitely not tool-generated')
    elif kind=='wrong_patch':
        f.entry(C,'evil','unrelated third account');f.commit(f.git('show','-s','--format=%s',migration).stdout.strip())
    elif kind=='forged_revert':
        f.entry(B,'unrelated','untouched');f.commit('snapshot: fake\n\nThis reverts commit '+migration+'.')
    elif kind=='reapply':
        f.ok('undo');f.git('revert','--no-edit',f.head())
    elif kind=='combined_inverse':
        (f.org(B)/'local_s.json').unlink();f.entry(B,'unrelated','other change');f.commit('snapshot: manual undo plus unrelated')
    elif kind=='merge':
        f.git('checkout','-q','-b','side');f.git('commit','-q','--allow-empty','-m','side');f.git('checkout','-q','master');f.git('merge','--no-ff','-m','merge side','side')
    f.blocked('undo')
    return 'unsupported/ambiguous history stops without falling back to earlier commit'
for kind in ['forged_summary','wrong_patch','forged_revert','reapply','combined_inverse','merge']:
    test('core','history_'+kind,lambda kind=kind:history(kind))


def generated():
    rng=random.Random(20261016)
    f=F('candidate','generated_60')
    expected={};blocked=[]
    for i in range(60):
        name=f's{i}_'+rng.choice(['plain','空 格','$()[]','é','😀'])
        cli='cli-'+name
        source_time=rng.choice([100,102,1000]);target_time=rng.choice([10,100,2000])
        f.entry(A,name,'source '+name,source_time,cli=cli)
        existing=rng.choice([True,False]);marker=rng.choice([True,False])
        target=f.org(B)/('local_'+name+'.json')
        if existing:f.entry(B,name,'target '+name,target_time,cli=cli)
        if marker:write(f.org(B)/('deleted_'+rng.choice([name,cli])),rng.choice(['','bad','1700000000000']))
        expected[name]=(target.read_bytes() if existing else None) if marker or (existing and source_time<=target_time+1) else (f.org(A)/target.name).read_bytes()
    f.init();source=digest_tree(f.org(A));f.ok()
    for name,value in expected.items():
        p=f.org(B)/('local_'+name+'.json');assert (p.read_bytes() if p.exists() else None)==value,name
    before=f.snap();f.ok();assert f.snap()==before
    assert digest_tree(f.org(A))==source and f.preserved()
    return '60 deterministic generated state combinations; independent expected-content oracle; idempotent replay'
test('generated','generated_merge_and_deletion_oracle',generated)


def fault(kind):
    f=standard('fault_'+kind);shim=f.dir/'shim';shim.mkdir()
    if kind in ('stage','commit'):
        program='''#!/usr/bin/env python3
import os,sys
a=sys.argv[1:]
if RULE: sys.exit(73)
os.execv(REAL,[REAL,*a])
'''.replace('REAL',repr(GIT)).replace('RULE',("'add' in a" if kind=='stage' else "'commit' in a and any(s.startswith('migrate:') for s in a)"))
        write(shim/'git',program);(shim/'git').chmod(0o755);f.env['PATH']=str(shim)+':'+f.env['PATH']
    else:
        # Test-only copy/interrupt seam: same candidate plus a single injected
        # exception or SIGTERM after the first successful atomic replacement.
        local=f.dir/'instrumented';shutil.copytree(ROOT/'candidate',local);f.src=local
        script=local/'bin/ccd-migrate';s=script.read_text()
        injection=("raise OSError('synthetic post-rename failure')" if kind=='post_rename' else "os.kill(os.getppid(), 15); raise SystemExit(143)")
        s=s.replace('os.replace(tmp, dpath)','os.replace(tmp, dpath)\n        '+injection)
        script.write_text(s)
        f.entry(A,'second','second file',2000)
    p=f.migrate();assert p.returncode!=0,(kind,p.stdout,p.stderr)
    assert (f.gd/'ccd-operation.json').exists()
    # Restore the real executable for the retry; journal must stop it anyway.
    f.src=ROOT/'candidate';f.env['PATH']=f.env['PATH'].replace(str(shim)+':','')
    f.blocked(contains='pending operation');f.blocked('undo',contains='pending operation')
    assert f.preserved()
    return {'fault':kind,'exit':p.returncode,'journal_retained':True,'retry_no_mutation':True}
for kind in ['stage','commit','post_rename','sigterm']:
    test('fault','fault_'+kind,lambda kind=kind:fault(kind))


def mutation(kind):
    f=standard('mutation_'+kind);local=f.dir/'mutant';shutil.copytree(ROOT/'candidate',local);f.src=local
    if kind=='marker':
        p=local/'bin/ccd-migrate';s=p.read_text();s=s.replace("if any(os.path.lexists(os.path.join(dst, 'deleted_' + ident)) for ident in ids if ident):","if False:");p.write_text(s)
        write(f.org(B)/'deleted_s','1');out=f.migrate();detected=out.returncode==0 and (f.org(B)/'local_s.json').exists()
    elif kind=='json':
        p=local/'bin/ccd-safety.py';s=p.read_text();s=s.replace('json_object(p.read_bytes(), str(p))','None');p.write_text(s)
        write(f.org(B)/'local_invalid.json','not JSON');old=f.head();out=f.migrate();detected=f.head()!=old
    elif kind=='pending':
        p=local/'bin/ccd-safety.py';s=p.read_text().replace("'ccd-operation.json', 'commondir'","'removed-journal-guard', 'commondir'");p.write_text(s)
        # Removing one layer still leaves exclusive journal creation. This is
        # defense in depth, not a killed data-integrity mutant.
        write(f.gd/'ccd-operation.json','');out=f.migrate();detected='pending operation' not in out.stderr
    elif kind=='inverse':
        assert f.migrate().returncode==0;assert f.migrate('undo').returncode==0
        p=local/'bin/ccd-safety.py';s=p.read_text().replace('if actual == inverse:', 'if False:').replace('if actual & inverse:', 'if False:');p.write_text(s)
        out=f.migrate('undo');detected='already reverted' not in out.stderr
    elif kind=='subject':
        assert f.migrate().returncode==0
        f.entry(B,'manual','unrelated');f.commit('snapshot: note\n\nmigrate: fake')
        p=local/'bin/ccd-safety.py';s=p.read_text().replace('--format=%H%x00%s%x00','--format=%H%x00%B%x00').replace("if sub.split()[:1] == ['migrate:']:","if 'migrate:' in sub:");p.write_text(s)
        out=f.migrate('undo');detected=out.returncode!=0
    assert detected,(kind,out.stdout,out.stderr)
    return {'mutant':kind,'status':'SURVIVED_SAFELY_SECONDARY_GUARD' if kind=='pending' else 'KILLED',
            'observation_exit':out.returncode,
            'oracle':'protected data' if kind in ('marker','json') else 'functional selection/diagnostic'}
for kind in ['marker','json','pending','inverse','subject']:
    test('mutation','mutation_'+kind,lambda kind=kind:mutation(kind))


manifest={'run_dir':str(RUN),'group':args.group,'results':OBSERVATIONS,
          'candidate_sha256':{str(p.relative_to(ROOT/'candidate')):sha(p) for p in sorted((ROOT/'candidate').rglob('*')) if p.is_file()},
          'counts':{s:sum(x['status']==s for x in OBSERVATIONS) for s in ['PASS','FAIL']}}
(RUN/'manifest.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False))
(OUT/('latest-'+args.group+'.txt')).write_text(str(RUN)+'\n')
print(json.dumps({'run':str(RUN),'counts':manifest['counts']}),flush=True)
sys.exit(1 if manifest['counts']['FAIL'] else 0)
