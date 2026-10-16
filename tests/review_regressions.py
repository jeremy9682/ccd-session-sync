#!/usr/bin/env python3
"""Reproduce reviewers' concrete counterexamples against before/after candidates."""
from run_synthetic import *
import sys
import unittest


class ReviewRegressions(unittest.TestCase):
    def f(self, version, name):
        f=Fixture(version,name);f.entry(A,'s','source',2000)
        self.assertEqual(f.install().returncode,0)
        return f

    def test_encoding_guard(self):
        for version in ('pre-review-fixes','candidate'):
            for label,data in [('bom',b'\xef\xbb\xbf{"title":"x"}'),('utf16','{"title":"x"}'.encode('utf-16'))]:
                with self.subTest(version=version,encoding=label):
                    f=self.f(version,'encoding_'+label);(f.org(A)/'local_z.json').write_bytes(data)
                    before=f.head();p=f.migrate();self.assertNotEqual(p.returncode,0)
                    if version=='candidate':
                        self.assertEqual(f.head(),before);self.assertFalse((f.org(B)/'local_s.json').exists());self.assertFalse((f.gd/'ccd-operation.json').exists())
                    else:self.assertTrue((f.gd/'ccd-operation.json').exists());self.assertNotEqual(f.head(),before)

    def test_lam_validation_staging_parity(self):
        for version in ('pre-review-fixes','candidate'):
            for account in ('notauuid','skills-plugin'):
                with self.subTest(version=version,account=account):
                    f=self.f(version,'lam_'+account)
                    write(f.wt/'local-agent-mode-sessions'/account/'org'/'local_x.json',{})
                    before=f.head();p=f.migrate()
                    if version=='candidate':
                        self.assertNotEqual(p.returncode,0);self.assertEqual(f.head(),before);self.assertFalse((f.gd/'ccd-operation.json').exists())
                    else:self.assertTrue(f.head()!=before or (f.gd/'ccd-operation.json').exists())

    def test_literal_org_globs(self):
        for version in ('pre-review-fixes','candidate'):
            with self.subTest(version=version):
                f=self.f(version,'glob')
                for org,title,when in [('[ab]','literal bracket',4000),('a','decoy a',3000),('b','decoy b',1000)]:
                    write(f.wt/'claude-code-sessions'/A/org/'local_glob.json',{'title':title,'cliSessionId':'glob'},when)
                p=f.migrate();self.assertEqual(p.returncode,0,p.stderr)
                value=json.loads((f.org(B)/'local_glob.json').read_text())['title']
                self.assertEqual(value=='literal bracket',version=='candidate')

    def test_global_filter_isolation(self):
        for version in ('pre-review-fixes','candidate'):
            with self.subTest(version=version):
                f=self.f(version,'global_filter');marker=f.dir/'FILTER_RAN';program=f.dir/'filter.py'
                write(program,'#!/usr/bin/env python3\nimport sys\nfrom pathlib import Path\nPath('+repr(str(marker))+').write_text("ran")\nsys.stdout.write(sys.stdin.read())\n');program.chmod(0o755)
                attributes=f.home/'attributes';write(attributes,'*.json filter=pwn\n')
                config=f.home/'.gitconfig';write(config,'[core]\n attributesFile = '+str(attributes)+'\n[filter "pwn"]\n clean = '+str(program)+'\n required = true\n')
                f.env['GIT_CONFIG_GLOBAL']=str(config)
                p=f.migrate();self.assertEqual(p.returncode,0,p.stderr)
                self.assertEqual(marker.exists(),version=='pre-review-fixes')

    def test_assume_unchanged_and_skip_worktree(self):
        for version in ('pre-review-fixes','candidate'):
            for flag in ('--assume-unchanged','--skip-worktree'):
                with self.subTest(version=version,flag=flag):
                    f=self.f(version,flag[2:]);t=f.entry(B,'s','old',1000);f.commit('snapshot: baseline')
                    f.git('update-index',flag,'--',str(t.relative_to(f.wt)))
                    before=t.read_bytes();head=f.head();p=f.migrate()
                    if version=='candidate':
                        self.assertNotEqual(p.returncode,0);self.assertEqual(t.read_bytes(),before);self.assertEqual(f.head(),head)
                    elif flag=='--assume-unchanged':self.assertNotEqual(t.read_bytes(),before)
                    else:
                        # This Git rejects explicit add of skip-worktree paths;
                        # the old candidate stops late, after opening a journal.
                        self.assertNotEqual(p.returncode,0)
                        self.assertTrue((f.gd/'ccd-operation.json').exists())

    def test_reinstall_retained_journal_and_staged_ownership(self):
        for version in ('pre-review-fixes','candidate'):
            for kind in ('journal','staged'):
                with self.subTest(version=version,kind=kind):
                    f=self.f(version,'reinstall_'+kind)
                    if kind=='journal':write(f.gd/'ccd-operation.json','');f.entry(B,'partial','partial state')
                    else:
                        write(f.wt/'unrelated.txt','user staged');f.git('add','-f','--','unrelated.txt')
                    before=f.head();index=sha(f.gd/'index');binbefore=digest_tree(f.home/'bin');p=f.install()
                    if version=='candidate':
                        self.assertNotEqual(p.returncode,0);self.assertEqual(f.head(),before);self.assertEqual(sha(f.gd/'index'),index);self.assertEqual(digest_tree(f.home/'bin'),binbefore)
                    else:self.assertNotEqual(f.head(),before)

    def test_title_type_preview_parity(self):
        for version in ('pre-review-fixes','candidate'):
            f=self.f(version,'title_type');write(f.org(A)/'local_s.json',{'title':42,'cliSessionId':'s'})
            before=digest_tree(f.home);p=f.migrate('--dry-run')
            self.assertEqual(p.returncode==0,version=='candidate');self.assertEqual(digest_tree(f.home),before)

    def test_existing_destination_identity_marker(self):
        f=self.f('candidate','target_identity')
        t=f.entry(B,'s','old',1000,cli='old-transcript');write(f.org(B)/'deleted_old-transcript','1');before=t.read_bytes()
        p=f.migrate();self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(t.read_bytes(),before)

    def test_prefix_ambiguity_refused(self):
        f=self.f('candidate','prefix_collision');self.assertEqual(f.migrate().returncode,0)
        other=f.wt/'claude-code-sessions'/'bbbbbbbb-9999-9999-9999-999999999999'/ORG
        other.mkdir(parents=True);write(other/'local_another.json',{'title':'other account'})
        before=digest_tree(f.wt);head=f.head();p=f.migrate('undo')
        self.assertNotEqual(p.returncode,0);self.assertIn('ambiguous',p.stderr);self.assertEqual(digest_tree(f.wt),before);self.assertEqual(f.head(),head)

    def test_python_import_and_byte_restore(self):
        f=self.f('candidate','python_import');marker=f.dir/'IMPORT_RAN'
        write(f.dir/'json.py','from pathlib import Path\nPath('+repr(str(marker))+').write_text("unsafe")\nraise RuntimeError("planted module")\n')
        f.env['PYTHONPATH']=str(f.dir);p=f.migrate('status');self.assertEqual(p.returncode,0);self.assertFalse(marker.exists())
        self.assertEqual(f.install().returncode,0);self.assertFalse(marker.exists())
        t=f.entry(B,'s','old',1000);original=b'{"title":"old","cliSessionId":"s"}\r\n';t.write_bytes(original);os.utime(t,(1000,1000))
        config=f.home/'.gitconfig';write(config,'[core]\n autocrlf = true\n');f.env['GIT_CONFIG_GLOBAL']=str(config)
        self.assertEqual(f.migrate().returncode,0);self.assertEqual(f.migrate('undo').returncode,0)
        self.assertEqual(t.read_bytes(),original)

    def test_loose_empty_account_and_tombstone_encoding(self):
        f=self.f('candidate','loose_account')
        loose=f.wt/'claude-code-sessions'/(B+'-zz')/ORG;loose.mkdir(parents=True)
        newline=f.wt/'claude-code-sessions'/(B+'\n')/ORG;newline.mkdir(parents=True)
        self.assertEqual(f.migrate().returncode,0)
        self.assertTrue((f.org(B)/'local_s.json').exists());self.assertFalse(list(loose.iterdir()));self.assertFalse(list(newline.iterdir()))
        f=self.f('candidate','tombstone_encoding');(f.wt/'claude-code-sessions/.tombstones').write_bytes(b'\xff')
        before=f.head();p=f.migrate();self.assertNotEqual(p.returncode,0);self.assertEqual(f.head(),before);self.assertFalse((f.gd/'ccd-operation.json').exists())

    def test_finder_metadata_not_staged(self):
        f=self.f('candidate','finder');finder=f.org(B)/'.DS_Store';write(finder,'opaque Finder metadata')
        self.assertEqual(f.migrate().returncode,0);self.assertNotIn('.DS_Store',f.git('ls-files').stdout)
        self.assertEqual(finder.read_text(),'opaque Finder metadata')

    def test_agent_setup_failure_does_not_block_valid_registry(self):
        f=self.f('candidate','agent_failure');shim=f.dir/'inert-app-tools';shim.mkdir()
        for name in ('plutil','launchctl'):
            write(shim/name,'#!/bin/sh\nexit 77\n');(shim/name).chmod(0o755)
        env=dict(f.env,PATH=str(shim)+':'+f.env['PATH'])
        p=f.run(['/bin/bash',f.src/'install.sh'],env=env)
        self.assertEqual(p.returncode,77);self.assertFalse((f.gd/'ccd-operation.json').exists())
        self.assertEqual(f.migrate().returncode,0);self.assertTrue(f.preserved())


suite=unittest.defaultTestLoader.loadTestsFromTestCase(ReviewRegressions)
result=unittest.TextTestRunner(verbosity=2).run(suite)
summary={'run_dir':str(RUN),'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
         'candidate_sha256':{str(p.relative_to(ROOT/'candidate')):sha(p) for p in sorted((ROOT/'candidate').rglob('*')) if p.is_file()}}
(RUN/'review-regressions.json').write_text(json.dumps(summary,indent=2))
(ROOT/'implementation-evidence/latest-review-regressions.txt').write_text(str(RUN)+'\n')
print(json.dumps(summary))
sys.exit(0 if result.wasSuccessful() else 1)
