# ccd-session-sync

Keep your **Claude Desktop** sidebar session list intact when you switch between Claude accounts on macOS — plus a transcript backup that survives the 30‑day cleanup, all git‑versioned and reversible.

> Solves the problem in [anthropics/claude-code#48511](https://github.com/anthropics/claude-code/issues/48511) ("session history lost when switching accounts", closed *not planned*) and [#74662](https://github.com/anthropics/claude-code/issues/74662) (request for a local‑sessions view). If Anthropic ships a native fix, you can uninstall this and lose nothing.

## The problem

Claude Desktop stores the sidebar session list **per logged‑in account**:

```
~/Library/Application Support/Claude/claude-code-sessions/<accountUUID>/<orgUUID>/local_*.json
```

Each `local_*.json` is a tiny index entry that points (via `cliSessionId`) at the real transcript. When you log into a **different** account, the app reads that account's (empty) directory and your sidebar looks wiped — even though:

- your **conversations are safe** — transcripts live account‑agnostically in `~/.claude/projects/**/*.jsonl`, and
- `claude --resume` (press **Ctrl+A** to show all) can reopen any local session under **any** login.

Only the Desktop **sidebar index** is account‑scoped. This tool re‑points that index so both accounts show the same list.

## What it does

| Component | Job |
|---|---|
| `ccd-migrate` | Git‑versioned, tombstone‑aware, **newer‑wins** merge of the other account's index entries into the current account's dir. Subcommands: `--dry-run`, `status`, `log`, `undo`, `snapshot`. |
| `ccd-migrate-auto` + launchd | Watches `cowork-enabled-cli-ops.json`; when the logged‑in account changes it runs `ccd-migrate` automatically and posts a macOS notification. |
| `claude-archive-sync` + launchd | Daily `rsync` of `~/.claude/projects` transcripts to `~/ClaudeArchive` (**no `--delete`**, so anything the app later prunes stays archived). The real long‑term data‑loss risk is the `cleanupPeriodDays` retention pruning, not account switching. |
| `claude-second` | Optional: launches a **second isolated Claude Desktop instance** (`open -n --user-data-dir=…`) so you can run one account per instance and stop switching entirely. |

## Safety by design

- **Never touches your conversations or credentials.** It only copies the small index JSONs and (optionally) backs up transcripts read‑only. It never reads or writes the Keychain, OAuth tokens, or `oauthAccount`.
- **The session index is git‑versioned.** Every change to the small `local_*.json` index files is a commit in `~/.ccd-sessions-git`; `ccd-migrate log` shows history and `ccd-migrate undo` reverts the last migration. (Large local‑agent‑mode *payload* directories are copied but intentionally **not** tracked in git — so `undo` restores the index, not those directories. They're additive/copy‑once and never deleted.)
- **Newer‑wins, never destructive.** Merges index entries by modification time with atomic temp‑then‑rename writes (Claude never reads a half‑written file); `rsync` runs **without `--delete`**; `--dry-run` previews everything and writes nothing.
- **Reversible install.** `uninstall.sh` removes the agents/scripts and keeps your backups by default.

## Install

```bash
git clone https://github.com/jeremy9682/ccd-session-sync
cd ccd-session-sync
./install.sh              # scripts -> ~/bin, bootstrap git repo, load launchd agents
# or: ./install.sh --no-agents   (manual `ccd-migrate` only, no background automation)
# or: ./install.sh --dry-run     (print every step, change nothing)
```

Requirements: macOS, `git`, `python3`, `rsync`, and Claude Desktop launched at least once. Put `~/bin` on your `PATH`.

## Use

```bash
ccd-migrate status      # accounts, per-dir entry counts, git state
ccd-migrate --dry-run   # preview what would be merged
ccd-migrate             # merge now  (the auto agent also does this on account switch)
ccd-migrate log         # migration history
ccd-migrate undo        # revert the last migration
```

After any migration, **fully quit and relaunch Claude Desktop** — it reads the session index only at startup.

Finding an old session across accounts without migrating? `claude --resume` in the project directory, then **Ctrl+A**.

### More than two accounts

Auto‑detection picks the current account as destination and the most‑recently‑active other account as source. With 3+ accounts, force direction:

```bash
CCD_FROM=<uuid-prefix> CCD_TO=<uuid-prefix> ccd-migrate
```

### The zero‑switching alternative

If you juggle accounts constantly, run two Desktop instances instead of switching:

```bash
claude-second           # opens a 2nd instance with an isolated data dir
```

Log each instance into its own account **with the other instance fully quit** (the `claude://` login deep‑link goes to an arbitrary running instance — the #1 pitfall). Then both stay logged in, each with its own sidebar, and account switching disappears. Don't run Cowork/local‑agent mode in the second instance unless you have several GB of free disk (it pulls its own VM bundles).

## Uninstall

```bash
./uninstall.sh          # remove agents + scripts, KEEP git history & archive
./uninstall.sh --purge  # also delete ~/.ccd-sessions-git and ~/ClaudeArchive (irreversible)
```

## Privacy

Your **local** state is private data — do not paste it into bug reports or commit it anywhere:

- `~/.ccd-sessions-git` (the git history) tracks your real session index entries, including account/org UUIDs and session **titles**.
- `~/ClaudeArchive` holds your actual transcripts; the logs and `last-owner` file contain account UUIDs.
- `ccd-migrate status` / `log` and commit messages print account/org identifiers.

None of that is in *this* repo, and `.gitignore` keeps it out of any clone. Only the tooling is published.

## Known limitations (it's experimental)

- **Unofficial / version‑sensitive.** Operates on Claude Desktop's *internal, undocumented* file layout. A future app update could change it and break this tool — worst case it errors out; your data is untouched and git‑backed. No schema/version detection. Not affiliated with or endorsed by Anthropic; no warranty; you are responsible for your own backups and for compliance with Claude's terms.
- **Heuristic direction.** Destination = current account; source = most‑recently‑active other account. With 3+ accounts, or an account that has more than one org directory, it warns and picks the newest — **always `--dry-run` first**, and use `CCD_FROM=/CCD_TO=` to force direction.
- **`undo`** reverts the most recent `migrate:` commit (not necessarily the very last operation) and can conflict if you edited things after; for surgical rollback use `ccd-migrate log` + `git … revert <hash>`.
- **Deletion intent**: in the selected `claude-code-sessions` destination account/org, a `deleted_<session-id>` or `deleted_<cliSessionId>` marker suppresses both NEW and UPD copies. Marker presence wins, even for an empty/malformed timestamp or a newer source. Markers are preserved; source markers never delete other accounts or archives. An existing destination index's transcript ID is also checked before an update. This policy does not infer deletion markers for the separate Cowork/local-agent payload format. A symlink/special-file marker blocks the operation. `.tombstones` remains a separate, hand-maintained global filename list. This naming is reported for Desktop 2.42304.0 and is not a stable public API; real UI compatibility has not been tested.
- **Writes are not transactional** — each JSON copy is atomically replaced, but a multi-file operation can stop midway. A persistent `ccd-operation.json` journal and exclusive `ccd.lock.d` block retries after failures/interruption. Keep the app quit: these checks cannot prevent simultaneous Desktop or same-user filesystem races.
- The sidebar's project grouping is derived from repo/PR metadata; this tool does **not** rewrite `originCwd` (load‑bearing for worktree/PR features). Sessions group under whatever project directory they actually ran in.
- macOS only (uses `launchd` + `osascript`).

## License

MIT — see [LICENSE](LICENSE).

## Conservative mutation checks

`ccd-migrate`, `undo`, and `snapshot` refuse existing Git conflicts, in-progress revert/merge/cherry-pick/rebase, a retained operation journal, pre-staged changes, malformed or non-UTF-8/BOM-prefixed index JSON, assume-unchanged/skip-worktree index flags, unsupported tracked paths, symlinks/hardlinks/special files, and custom Git filters/attributes. Checks cover every account/org index in this registry, so an invalid unrelated account index may block an operation. JSON objects with duplicate keys or non-finite numbers are refused. Missing/null `cliSessionId` uses filename-ID deletion markers only; nonempty IDs must be safe single filename components. Extra unknown arguments are rejected.

The safety helper `ccd-safety.py` is installed next to `ccd-migrate`. Git hooks, signing, fsmonitor, global/system configuration and global/system attributes are disabled for tool operations; automatic line-ending conversion is disabled. Existing installation refresh uses the same preflight, lock, journal and path-limited staging. Concrete LaunchAgent output files are checked for symlinks/hardlinks before any installation mutation. Registry completion clears the journal before optional agent setup, so agent setup failures do not block an otherwise valid registry. Overrides must be absolute paths; custom registry paths require `install.sh --no-agents` because the launchd templates use default paths. The tool snapshots ordinary app-side drift only after validation and selection; an invalid/no-op undo does not first commit drift. Unrelated, untracked files outside the recognized index paths are preserved. Untracked `.DS_Store` files are tolerated and never staged; other unexpected session-tree files and hardlinks (including payload hardlinks) cause a conservative stop.

If an operation stops, **do not immediately delete the lock/journal or retry**. Quit Desktop and inspect `ccd-migrate status`, Git status/diff, and the journal with an experienced maintainer. Preserve a copy of the affected index files and Git repository before any manual recovery. Recovery must decide whether partial copies or Git state should be retained or reverted; the tool never automatically resets, aborts, or discards edits. A stale lock after a killed process requires the same inspection.

Account/org prefix collisions in an undo target are refused. Org names must have a prefix representable in the generated history summary; literal glob metacharacters in paths are never expanded while selecting directories.

Undo identifies migration intent from a subject plus a validated patch. This is not authentication: deliberately forged history that is byte-for-byte indistinguishable from a legitimate migration cannot be detected. Unsupported/suspicious history stops rather than falling back to an older migration. Git does not restore original modification times during undo; later newer-wins decisions may be affected. This repair does not change payload-directory rollback: copied payload directories remain additive and untracked.
