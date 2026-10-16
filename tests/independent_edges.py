#!/usr/bin/env python3
"""Separate unittest acceptance surface for untouched data and Git integration."""
from run_synthetic import *
import unittest
import sys


class Edges(unittest.TestCase):
    def fresh(self, name):
        f=Fixture('candidate',name)
        f.entry(A,'s','source',2000)
        self.assertEqual(f.install().returncode,0)
        return f

    def must_block(self,f,*a):
        state=digest_tree(f.wt); head=f.head(); index=sha(f.gd/'index')
        p=f.migrate(*a)
        self.assertNotEqual(p.returncode,0,p.stdout)
        self.assertEqual(digest_tree(f.wt),state)
        self.assertEqual(f.head(),head)
        self.assertEqual(sha(f.gd/'index'),index)
        self.assertTrue(f.preserved())
        return p

    def test_deep_history_and_no_candidate_dirty(self):
        f=self.fresh('deep');self.assertEqual(f.migrate().returncode,0)
        for i in range(35):f.git('commit','--allow-empty','-q','-m',f'snapshot: {i}')
        self.assertEqual(f.migrate('undo').returncode,0)
        self.assertFalse((f.org(B)/'local_s.json').exists())
        f=self.fresh('no_candidate_dirty');f.entry(B,'dirty','do not stage this')
        p=self.must_block(f,'undo');self.assertIn('nothing to undo',p.stderr)

    def test_marker_aliases_and_other_org(self):
        f=self.fresh('alias')
        f.entry(A,'alternate','alias',2000,cli='same-transcript')
        f.entry(A,'another','alias2',2000,cli='same-transcript')
        write(f.org(B)/'deleted_same-transcript','garbage')
        other=f.wt/'claude-code-sessions'/B/'other-org'/'local_old.json'
        write(other,{'title':'preserve other org'},1);before=other.read_bytes()
        f.entry(B,'anchor','anchor destination org',3000)
        self.assertEqual(f.migrate().returncode,0)
        self.assertFalse((f.org(B)/'local_alternate.json').exists())
        self.assertFalse((f.org(B)/'local_another.json').exists())
        self.assertEqual(other.read_bytes(),before)

    def test_null_missing_empty_cli(self):
        for i,entry in enumerate([{'title':'missing'},{'title':'null','cliSessionId':None}]):
            f=self.fresh('cli_absence'+str(i));write(f.org(A)/'local_s.json',entry)
            write(f.org(B)/'deleted_s','')
            self.assertEqual(f.migrate().returncode,0)
            self.assertFalse((f.org(B)/'local_s.json').exists())
        f=self.fresh('cli_empty');write(f.org(A)/'local_s.json',{'cliSessionId':''})
        self.must_block(f)

    def test_payload_remains_and_unrelated_not_staged(self):
        f=self.fresh('payload')
        source=f.wt/'local-agent-mode-sessions'/A/ORG
        write(source/'local_payload.json',{'title':'payload'},2000)
        write(source/'local_payload/data.txt','untracked payload',2000)
        unrelated=f.wt/'untouched.txt';write(unrelated,'not an index')
        self.assertEqual(f.migrate().returncode,0)
        self.assertNotIn('untouched.txt',f.git('ls-files').stdout)
        self.assertEqual(f.migrate('undo').returncode,0)
        target=f.wt/'local-agent-mode-sessions'/B/ORG
        self.assertTrue((target/'local_payload/data.txt').exists())
        self.assertFalse((target/'local_payload.json').exists())
        self.assertEqual(unrelated.read_text(),'not an index')

    def test_filters_attributes_hooks_signing(self):
        f=self.fresh('config_hooks');marker=f.dir/'HOOK_RAN'
        script='#!/bin/sh\nprintf executed > '+shlex.quote(str(marker))+'\n'
        write(f.gd/'hooks/pre-commit',script);(f.gd/'hooks/pre-commit').chmod(0o755)
        signing=f.dir/'signing';write(signing,script);signing.chmod(0o755)
        f.git('config','commit.gpgsign','true');f.git('config','gpg.program',str(signing))
        self.assertEqual(f.migrate().returncode,0)
        self.assertFalse(marker.exists())
        f=self.fresh('filter');f.git('config','filter.test.clean',str(signing));self.must_block(f)
        f=self.fresh('attributes');write(f.wt/'.gitattributes','*.json filter=test\n');self.must_block(f)

    def test_environment_and_account_ambiguity(self):
        f=self.fresh('env_override');f.env['GIT_INDEX_FILE']=str(f.dir/'other-index');self.must_block(f)
        f=self.fresh('bad_prefix');f.env['CCD_FROM']='../../escape';self.must_block(f)
        f=self.fresh('ambiguous_prefix');f.env['CCD_TO']='';write(f.wt/'cowork-enabled-cli-ops.json',{'ownerAccountId':'missing'});self.must_block(f)
        f=self.fresh('same_account');f.env['CCD_TO']=A[:8];self.must_block(f)

    def test_git_modes_unsafe_history(self):
        f=self.fresh('wrong_mode');self.assertEqual(f.migrate().returncode,0)
        p=f.org(B)/'local_s.json';p.chmod(0o755)
        f.commit(f.git('log','-1','--format=%s').stdout.strip());self.must_block(f,'undo')

    def test_dirty_and_committed_overlap(self):
        f=self.fresh('overlap');f.entry(B,'s','old',1000)
        self.assertEqual(f.migrate().returncode,0)
        write(f.org(B)/'local_s.json',{'title':'manual incompatible','cliSessionId':'cli-s'})
        f.commit('snapshot: later edit');self.must_block(f,'undo')
        self.assertNotIn('<<<<<<<',(f.org(B)/'local_s.json').read_text())

    def test_existing_lock_no_mutation(self):
        f=self.fresh('lock');(f.gd/'ccd.lock.d').mkdir();self.must_block(f)
        self.assertTrue((f.gd/'ccd.lock.d').is_dir())

    def test_forged_valid_summary_wrong_filename(self):
        f=self.fresh('forged_path');self.assertEqual(f.migrate().returncode,0)
        title=f.git('log','-1','--format=%s').stdout.strip()
        f.entry(C,'forged','third account');f.commit(title)
        self.must_block(f,'undo')

    def test_empty_unsafe_org_executable_and_surrogate(self):
        f=self.fresh('empty_bad_org');f.org(B).rmdir();(f.org(B).parent/'bad org').mkdir();self.must_block(f)
        f=self.fresh('exec_source');(f.org(A)/'local_s.json').chmod(0o755);self.must_block(f)
        f=self.fresh('surrogate_id');write(f.org(A)/'local_s.json','{"cliSessionId":"\\ud800"}');self.must_block(f)

    def test_diff_failure_retains_journal(self):
        f=self.fresh('diff_failure');shim=f.dir/'shim';shim.mkdir()
        program='#!/usr/bin/env python3\nimport os,sys\na=sys.argv[1:]\nif "diff" in a and "--quiet" in a: sys.exit(74)\nos.execv('+repr(GIT)+',['+repr(GIT)+',*a])\n'
        write(shim/'git',program);(shim/'git').chmod(0o755)
        old=f.head();f.env['PATH']=str(shim)+':'+f.env['PATH'];p=f.migrate()
        self.assertEqual(p.returncode,74)
        self.assertEqual(f.head(),old)
        self.assertTrue((f.gd/'ccd-operation.json').exists())
        self.assertFalse((f.org(B)/'local_s.json').exists())

    def test_launchagent_symlink_refused_without_launching(self):
        f=self.fresh('plist_symlink');la=f.home/'Library/LaunchAgents';la.mkdir(parents=True)
        sentinel=f.dir/'external-scratch-sentinel';write(sentinel,'DO NOT TOUCH')
        before=f.head();index=sha(f.gd/'index');bins=digest_tree(f.home/'bin')
        link=la/'com.ccd-session-sync.migrate-auto.plist';link.symlink_to(sentinel)
        shim=f.dir/'blocked-app-commands';shim.mkdir();called=f.dir/'APP_COMMAND_CALLED'
        for name in ('launchctl','plutil'):
            write(shim/name,'#!/bin/sh\nprintf called > '+shlex.quote(str(called))+'\nexit 77\n');(shim/name).chmod(0o755)
        env=dict(f.env,PATH=str(shim)+':'+f.env['PATH'])
        # Only candidate; both app commands are inert shims as a second boundary.
        p=subprocess.run(['/bin/bash',str(f.src/'install.sh')],env=env,cwd=f.dir,text=True,capture_output=True)
        with LOG.open('a') as log:log.write(json.dumps({'case':f.name,'command':['install.sh (default; inert app shims)'],'env':env,'exit_code':p.returncode,'stdout':p.stdout,'stderr':p.stderr})+'\n')
        self.assertNotEqual(p.returncode,0);self.assertIn('symlink',p.stderr)
        self.assertEqual(sentinel.read_text(),'DO NOT TOUCH');self.assertFalse(called.exists())
        link.unlink()  # Remove only our fixture link before using the strict helper.
        self.assertEqual(f.head(),before);self.assertEqual(sha(f.gd/'index'),index);self.assertEqual(digest_tree(f.home/'bin'),bins)

    def test_auto_same_owner_isolated_import(self):
        f=self.fresh('auto_import');marker=f.dir/'IMPORT_RAN'
        write(f.dir/'json.py','from pathlib import Path\nPath('+repr(str(marker))+').write_text("unsafe")\nraise RuntimeError("planted")\n')
        f.env['PYTHONPATH']=str(f.dir)
        self.assertEqual((f.gd/'last-owner').read_text().strip(),B)
        p=f.run(['/bin/bash',f.src/'bin/ccd-migrate-auto'])
        self.assertEqual(p.returncode,0);self.assertFalse(marker.exists())
        self.assertEqual((f.gd/'last-owner').read_text().strip(),B)


suite=unittest.defaultTestLoader.loadTestsFromTestCase(Edges)
result=unittest.TextTestRunner(verbosity=2).run(suite)
summary={'run_dir':str(RUN),'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
         'candidate_sha256':{str(p.relative_to(ROOT/'candidate')):sha(p) for p in sorted((ROOT/'candidate').rglob('*')) if p.is_file()}}
(RUN/'unittest-results.json').write_text(json.dumps(summary,indent=2))
(ROOT/'implementation-evidence/latest-independent.txt').write_text(str(RUN)+'\n')
print(json.dumps(summary))
sys.exit(0 if result.wasSuccessful() else 1)
