#!/usr/bin/env python3
"""Fail-closed checks for ccd-migrate. No automatic recovery or deletion."""
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys


class Unsafe(Exception):
    pass


def fail(message):
    raise Unsafe(message)


def component(value):
    if isinstance(value, str):
        try:
            value.encode('utf-8')
        except UnicodeError:
            return False
    return (isinstance(value, str) and value not in ('', '.', '..')
            and not any(c in value for c in '/\\\x00\r\n')
            and not any(ord(c) < 32 or ord(c) == 127 for c in value))


def no_links(path):
    path = Path(os.path.abspath(path))
    for p in reversed((path, *path.parents)):
        if p.is_symlink():
            fail(f'symlink path refused: {p}')
    return path


def regular_tree(path):
    if not path.exists():
        return
    for root, dirs, files in os.walk(path, followlinks=False):
        for name in dirs + files:
            p = Path(root) / name
            mode = p.lstat().st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                fail(f'symlink or special file refused: {p}')
            if stat.S_ISREG(mode) and p.stat().st_nlink != 1:
                fail(f'hard-linked file refused: {p}')


def json_object(data, label):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                fail(f'duplicate JSON key in {label}: {key}')
            result[key] = value
        return result
    try:
        if isinstance(data, bytes):
            data = data.decode('utf-8')
        if data.startswith('\ufeff'):
            fail(f'UTF-8 BOM is not supported: {label}')
        value = json.loads(data, object_pairs_hook=pairs,
                           parse_constant=lambda s: fail(f'non-finite JSON: {label}'))
    except (ValueError, UnicodeError) as e:
        fail(f'invalid JSON in {label}: {e}')
    if not isinstance(value, dict):
        fail(f'JSON object required: {label}')
    cli = value.get('cliSessionId')
    if cli is not None and not component(cli):
        fail(f'unsafe cliSessionId: {label}')
    return value


GD = WT = None


def git(*args, ok=(0,), data=None):
    env = dict(os.environ, GIT_OPTIONAL_LOCKS='0', GIT_LITERAL_PATHSPECS='1',
               GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_NOSYSTEM='1', GIT_ATTR_NOSYSTEM='1')
    for key in ('GIT_CONFIG_PARAMETERS', 'GIT_CONFIG_COUNT', 'GIT_ATTR_SOURCE'):
        env.pop(key, None)
    p = subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
                        '-c', 'log.showSignature=false', '-c', 'diff.external=', '-c', 'commit.gpgsign=false', '-c', 'core.attributesFile=/dev/null', '-c', 'core.autocrlf=false',
                        '--git-dir='+str(GD), '--work-tree='+str(WT), *args],
                       input=data, capture_output=True, env=env, cwd=WT)
    if p.returncode not in ok:
        fail('git '+args[0]+' failed: '+p.stderr.decode('utf-8', 'replace').strip())
    return p.stdout


def allowed_index(path):
    parts = Path(path).parts
    return (len(parts) == 4 and parts[0] in ('claude-code-sessions', 'local-agent-mode-sessions')
            and all(component(p) for p in parts)
            and not any(c.isspace() for c in parts[2][:8])
            and re.fullmatch(r'[0-9a-f]{8}-[0-9a-f-]+', parts[1]) is not None
            and parts[3].startswith('local_') and parts[3].endswith('.json')
            and len(parts[3]) > len('local_.json'))


def guard(allow_empty=False):
    global GD, WT
    if not GD.is_absolute() or not WT.is_absolute():
        fail('CCD_GIT_DIR and CCD_CLAUDE_DIR must be absolute paths')
    GD, WT = no_links(GD), no_links(WT)
    if (not GD.is_dir() and not allow_empty) or (GD.exists() and not GD.is_dir()) or not (WT/'claude-code-sessions').is_dir():
        fail('git repo or session root missing; run installer first')
    if GD == WT or GD in WT.parents or WT in GD.parents:
        fail('Git directory and data directory must be disjoint')
    for name in ('GIT_INDEX_FILE', 'GIT_COMMON_DIR', 'GIT_OBJECT_DIRECTORY',
                 'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_NAMESPACE'):
        if name in os.environ:
            fail(f'{name} override refused')
    regular_tree(GD)
    for name in ('claude-code-sessions', 'local-agent-mode-sessions'):
        p = no_links(WT/name)
        regular_tree(p)
    for name in ('cowork-enabled-cli-ops.json', '.gitattributes'):
        no_links(WT/name)
    if (WT/'.gitattributes').exists() or (GD/'info/attributes').exists():
        fail('custom Git attributes require manual review')
    for root in ('claude-code-sessions', 'local-agent-mode-sessions'):
        if any((WT/root).rglob('.gitattributes')):
            fail('custom Git attributes require manual review')
    for name in ('REVERT_HEAD', 'MERGE_HEAD', 'CHERRY_PICK_HEAD', 'rebase-apply',
                 'rebase-merge', 'sequencer', 'BISECT_LOG', 'index.lock',
                 'ccd-operation.json', 'commondir', 'objects/info/alternates'):
        if (GD/name).exists():
            fail(f'pending operation or unsupported state: {name}; inspect manually, no automatic recovery')
    if GD.is_dir():
        if git('ls-files', '-u', '-z'):
            fail('unmerged Git entries; resolve manually before any mutation')
        if git('diff', '--cached', '--name-only', '-z'):
            fail('pre-staged changes; preserve and review them before running ccd-migrate')
        risky = git('config', '--local', '--name-only', '--get-regexp',
                    r'^(filter\.|include\.|includeIf\.|core\.attributesfile|core\.sshCommand|extensions\.)', ok=(0, 1))
        if risky:
            fail('custom repository filters/includes/extensions require manual review')
        flags = git('ls-files', '-v', '-z').split(b'\0')
        if any(row and (row[:1] == b'S' or b'a' <= row[:1] <= b'z') for row in flags):
            fail('assume-unchanged/skip-worktree index flags require manual review')
        if not allow_empty:
            git('rev-parse', '--verify', 'HEAD')
    ops = WT/'cowork-enabled-cli-ops.json'
    if ops.exists():
        if not ops.is_file() or ops.stat().st_nlink != 1:
            fail('unsafe owner metadata file')
        owner = json_object(ops.read_bytes(), str(ops)).get('ownerAccountId')
        if not component(owner):
            fail('invalid ownerAccountId')
    for root in ('claude-code-sessions', 'local-agent-mode-sessions'):
        for p in (WT/root).glob('*/*/local_*.json'):
            if p.name == 'cowork-gb-cache.json':
                continue
            if not p.is_file():
                fail(f'index must be regular file: {p}')
            if p.stat().st_mode & 0o111:
                fail(f'executable index file requires manual review: {p}')
            json_object(p.read_bytes(), str(p))
    for root in ('claude-code-sessions', 'local-agent-mode-sessions'):
        for p in (WT/root).glob('*/*/local_*.json'):
            if not allowed_index(str(p.relative_to(WT))):
                fail(f'unsupported index path: {p}')
    for p in (WT/'claude-code-sessions').glob('*/*/deleted_*'):
        if not p.is_file() or not component(p.name[8:]):
            fail(f'unsupported deletion marker: {p}')
    tombstones = WT/'claude-code-sessions/.tombstones'
    if tombstones.exists():
        if not tombstones.is_file():
            fail('tombstones list must be a regular UTF-8 file')
        try:
            tombstones.read_text(encoding='utf-8')
        except UnicodeError:
            fail('tombstones list must be UTF-8')
    for p in (WT/'claude-code-sessions').rglob('*'):
        if p.is_file():
            if p.name == '.DS_Store':
                continue  # Finder metadata is never staged.
            rel = p.relative_to(WT)
            if not (allowed_index(str(rel)) or str(rel) == 'claude-code-sessions/.tombstones'
                    or (len(rel.parts) == 4 and p.name.startswith('deleted_') and component(p.name[8:]))):
                fail(f'unsupported file in staged session tree: {p}')
    # Never stage arbitrary files or payloads from a compromised tracked history.
    tracked = git('ls-files', '-z').split(b'\0') if GD.is_dir() else []
    for raw in filter(None, tracked):
        path = os.fsdecode(raw)
        parts = Path(path).parts
        allowed = (allowed_index(path) or path in ('cowork-enabled-cli-ops.json', 'claude-code-sessions/.tombstones')
                   or (len(parts) == 4 and parts[0] == 'claude-code-sessions'
                       and parts[3].startswith('deleted_') and component(parts[3][8:])))
        if not allowed:
            fail(f'unsupported tracked path: {path}')
        no_links(WT/path)


def changes(commit):
    parents = git('rev-list', '--parents', '-n', '1', commit).split()
    if len(parents) != 2:
        fail('undo requires a single-parent migration/history commit')
    fields = git('diff-tree', '--no-commit-id', '--raw', '-z', '-r', '--no-abbrev',
                 '--no-renames', parents[1].decode(), commit).split(b'\0')
    rows = []
    for i in range(0, len(fields)-1, 2):
        oldmode, newmode, oldblob, newblob, state = fields[i][1:].decode().split()
        rows.append((os.fsdecode(fields[i+1]), oldmode, newmode, oldblob, newblob, state))
    return parents[1].decode(), rows


def undo_candidate():
    # Capture complete producer output: no early-closing pipe, no body matching.
    fields = git('log', '--first-parent', '--no-decorate', '--format=%H%x00%s%x00').split(b'\0')
    migration = None
    subject = ''
    for i in range(0, len(fields)-1, 2):
        sub = fields[i+1].decode('utf-8', 'replace')
        if sub.split()[:1] == ['migrate:']:
            migration, subject = fields[i].strip().decode(), sub
            break
    if migration is None:
        fail("no 'migrate:' commit found — nothing to undo")
    match = re.fullmatch(r'migrate: src=([^ /]+?)/([^ /]+) dst=([^ /]+?)/([^ /]+) new=\d+ upd=\d+ lam_json=\d+ lam_dir=\d+ tombstone_skipped=\d+(?: deleted_skipped=\d+)?', subject)
    if not match:
        fail('latest migrate: subject is not a recognized tool migration; inspect manually')
    parent, rows = changes(migration)
    if not rows:
        fail('empty migration commit; nothing safe to undo')
    for path, oldmode, newmode, oldblob, newblob, state in rows:
        if not allowed_index(path) or state not in ('A', 'M') or newmode != '100644' or oldmode not in ('000000', '100644'):
            fail('migration contains unsupported changes; inspect manually')
        parts = Path(path).parts
        if parts[1][:8] != match[3] or parts[2][:8] != match[4]:
            fail('migration patch does not match declared destination')
        for blob in (oldblob, newblob):
            if set(blob) != {'0'}:
                json_object(git('cat-file', 'blob', blob), path+' in history')
    inverse = {(p, nm, om, nb, ob) for p, om, nm, ob, nb, st in rows}
    for later in git('rev-list', migration+'..HEAD').decode().splitlines():
        parents = git('rev-list', '--parents', '-n', '1', later).split()
        if len(parents) != 2:
            fail('nonlinear history after migration; inspect manually')
        _, later_rows = changes(later)
        actual = {(p, om, nm, ob, nb) for p, om, nm, ob, nb, st in later_rows}
        body = git('show', '-s', '--format=%B', later).decode('utf-8', 'replace')
        if actual == inverse:
            matches = True
            for path, om, nm, ob, nb, st in rows:
                current = WT/path
                if set(ob) == {'0'}:
                    matches = matches and not current.exists()
                else:
                    matches = matches and current.is_file() and current.read_bytes() == git('cat-file', 'blob', ob)
            if matches:
                fail('latest migration is already reverted; no earlier migration selected. Use log for manual review')
            fail('latest migration was reverted but affected paths changed/reapplied; inspect manually, no earlier migration selected')
        if actual & inverse:
            fail('partial or combined inverse changes after migration; inspect manually')
        if migration in body and ('revert' in body.lower() or 'undo' in body.lower()):
            fail('ambiguous undo reference in later history; inspect manually')
    destinations = {(Path(row[0]).parts[1], Path(row[0]).parts[2]) for row in rows}
    matching = {(a.name, o.name) for a in (WT/'claude-code-sessions').iterdir()
                if a.is_dir() and a.name[:8] == match[3]
                for o in a.iterdir() if o.is_dir() and o.name[:8] == match[4]}
    if len(destinations) != 1 or matching != destinations:
        fail('destination account/org prefix is ambiguous; inspect full paths manually')
    touched = [r[0] for r in rows]
    if git('diff', '--name-only', '-z', '--no-ext-diff', '--no-textconv', '--', *touched):
        fail('undo target has uncommitted edits; preserve and inspect them manually')
    patch = git('diff', '--binary', '--no-ext-diff', '--no-textconv', parent, migration, '--')
    git('apply', '--reverse', '--check', '--index', '--whitespace=nowarn', data=patch)
    print(migration)


def main():
    global GD, WT
    GD, WT = map(Path, sys.argv[1:3])
    action = sys.argv[3]
    if action == 'stage':
        # Internal operation stage: guard already ran before the journal was
        # created. Literal paths avoid wildcard/pathspec interpretation.
        paths = {os.fsdecode(p) for p in git('ls-files', '-z').split(b'\0') if p}
        for root in ('claude-code-sessions', 'local-agent-mode-sessions'):
            paths.update(str(p.relative_to(WT)) for p in (WT/root).glob('*/*/local_*.json') if p.is_file())
        paths.update(str(p.relative_to(WT)) for p in (WT/'claude-code-sessions').glob('*/*/deleted_*') if p.is_file())
        for name in ('cowork-enabled-cli-ops.json', 'claude-code-sessions/.tombstones'):
            if (WT/name).is_file():
                paths.add(name)
        paths = sorted(paths)
        for i in range(0, len(paths), 200):
            git('add', '-A', '--', *paths[i:i+200])
        return
    guard(allow_empty=action == 'preinstall')
    if action == 'preinstall':
        if not (WT/'cowork-enabled-cli-ops.json').is_file():
            fail('owner metadata missing; refusing installation before any mutation')
        for i, path in enumerate(sys.argv[4:]):
            p = no_links(Path(path))
            if p.exists() and (p.is_dir() if i < 3 else p.is_file()) is False:
                fail(f'unexpected install target type: {p}')
            if p.exists() and p.is_file() and p.stat().st_nlink != 1:
                fail(f'hard-linked install target: {p}')
    if action == 'undo':
        undo_candidate()
    elif action == 'plan':
        ops = WT/'cowork-enabled-cli-ops.json'
        if not ops.is_file():
            fail('owner metadata missing')
        owner = json_object(ops.read_bytes(), str(ops)).get('ownerAccountId')
        accounts = [p for p in (WT/'claude-code-sessions').iterdir()
                    if p.is_dir() and re.fullmatch(r'[0-9a-f]{8}-[0-9a-f-]+', p.name)]
        dest_filter, source_filter = os.environ.get('CCD_TO', ''), os.environ.get('CCD_FROM', '')
        for value in (dest_filter, source_filter):
            if value and not re.fullmatch('[0-9a-f-]+', value):
                fail('account prefix must contain hexadecimal characters and hyphens')
        dests = [p for p in accounts if p.name.startswith(dest_filter)] if dest_filter else [p for p in accounts if p.name == owner]
        if len(dests) != 1:
            fail('destination account absent or ambiguous')
        sources = [p for p in accounts if p.name.startswith(source_filter)] if source_filter else [p for p in accounts if p != dests[0]]
        if not sources or (source_filter and len(sources) != 1) or dests[0] in sources:
            fail('source absent, ambiguous or same account')
        for account in [*dests, *sources]:
            for org in account.iterdir():
                if org.is_dir() and (not component(org.name) or any(c.isspace() for c in org.name[:8])):
                    fail('org name cannot be represented safely in migration history')
        if not any(p.is_dir() for p in dests[0].iterdir()) or not any(p.is_dir() for a in sources for p in a.iterdir()):
            fail('source/destination org directory missing')


if __name__ == '__main__':
    try:
        main()
    except (Unsafe, OSError, ValueError) as e:
        print('ERROR: '+str(e), file=sys.stderr)
        sys.exit(1)
