#!/usr/bin/env bash
# install.sh — set up ccd-session-sync: scripts, git-versioned registry repo, and launchd agents.
# Idempotent. Env overrides: CCD_BIN (default ~/bin), CCD_ARCHIVE (default ~/ClaudeArchive).
# Flags: --no-agents (install scripts + git repo only, skip launchd), --dry-run (print, do nothing).
set -euo pipefail
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 GIT_ATTR_NOSYSTEM=1
unset GIT_CONFIG_PARAMETERS GIT_CONFIG_COUNT GIT_ATTR_SOURCE
safe_git() { git -c core.hooksPath=/dev/null -c core.fsmonitor=false -c commit.gpgsign=false -c core.attributesFile=/dev/null -c core.autocrlf=false "$@"; }


REPO_SRC="$(cd "$(dirname "$0")" && pwd)"
BIN="${CCD_BIN:-$HOME/bin}"
GD="${CCD_GIT_DIR:-$HOME/.ccd-sessions-git}"
WT="${CCD_CLAUDE_DIR:-$HOME/Library/Application Support/Claude}"
ARCHIVE="${CCD_ARCHIVE:-$HOME/ClaudeArchive}"
LA="$HOME/Library/LaunchAgents"
DRY=0; AGENTS=1
for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    --no-agents) AGENTS=0 ;;
    *) echo "unknown flag: $a" >&2; exit 2 ;;
  esac
done
run() { echo "+ $*"; [ "$DRY" = 1 ] || "$@"; }

echo "== preflight =="
if [ "$AGENTS" = 1 ] && { [ "$GD" != "$HOME/.ccd-sessions-git" ] || [ "$WT" != "$HOME/Library/Application Support/Claude" ]; }; then
  echo "ERROR: custom registry paths require --no-agents; launchd templates use default paths" >&2; exit 2
fi
[ "$(uname)" = "Darwin" ] || { echo "ERROR: macOS only." >&2; exit 1; }
for t in git python3 rsync; do command -v "$t" >/dev/null || { echo "ERROR: '$t' not found in PATH." >&2; exit 1; }; done
[ -d "$WT/claude-code-sessions" ] || { echo "ERROR: '$WT/claude-code-sessions' not found. Install & launch Claude Desktop once, then re-run." >&2; exit 1; }
case ":$PATH:" in *":$BIN:"*) : ;; *) echo "WARNING: $BIN is not on your PATH — add it to ~/.zshrc: export PATH=\"$BIN:\$PATH\"";; esac

# Validate existing data/recovery state before changing installed files or Git.
python3 -I "$REPO_SRC/bin/ccd-safety.py" "$GD" "$WT" preinstall "$BIN" "$ARCHIVE/claude-projects" "$LA" "$BIN/ccd-migrate" "$BIN/ccd-migrate-auto" "$BIN/claude-archive-sync" "$BIN/claude-second" "$BIN/ccd-safety.py" "$LA/com.ccd-session-sync.migrate-auto.plist" "$LA/com.ccd-session-sync.projects-archive.plist"
if [ -e "$GD/ccd.lock.d" ]; then echo "ERROR: existing operation lock; inspect manually" >&2; exit 1; fi
if [ "$DRY" = 0 ]; then
  if [ ! -d "$GD" ]; then
    safe_git init --bare "$GD" >/dev/null
    safe_git --git-dir="$GD" config core.bare false
    safe_git --git-dir="$GD" config user.name ccd-session-sync
    safe_git --git-dir="$GD" config user.email ccd-session-sync@localhost
  fi
  mkdir "$GD/ccd.lock.d" || exit 1
  trap 'rmdir "$GD/ccd.lock.d" 2>/dev/null || true' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  python3 -I "$REPO_SRC/bin/ccd-safety.py" "$GD" "$WT" preinstall
  python3 -I - "$GD/ccd-operation.json" <<'PY_INSTALL_JOURNAL'
import json, os, sys
with open(sys.argv[1], 'x') as f:
    json.dump({'command':'install','pid':os.getppid(),'recovery':'manual inspection required'}, f)
    f.flush(); os.fsync(f.fileno())
fd = os.open(os.path.dirname(sys.argv[1]), os.O_RDONLY)
try: os.fsync(fd)
finally: os.close(fd)
PY_INSTALL_JOURNAL
fi

echo "== install scripts -> $BIN =="
run mkdir -p "$BIN"
for s in ccd-migrate ccd-migrate-auto claude-archive-sync claude-second; do
  run cp "$REPO_SRC/bin/$s" "$BIN/$s"
  run chmod +x "$BIN/$s"
done
run cp "$REPO_SRC/bin/ccd-safety.py" "$BIN/ccd-safety.py"
run chmod 644 "$BIN/ccd-safety.py"

echo "== bootstrap git-versioned registry repo -> $GD =="
if [ "$DRY" = 1 ]; then
  echo "+ (dry-run) init/refresh $GD, refresh info/exclude, seed last-owner, baseline commit"
else
  # always (re)write the tracking whitelist so upgrades pick up changes
  cat > "$GD/info/exclude" <<'EOF'
*
!claude-code-sessions/
!claude-code-sessions/**
!cowork-enabled-cli-ops.json
!local-agent-mode-sessions/
!local-agent-mode-sessions/*/
!local-agent-mode-sessions/*/*/
!local-agent-mode-sessions/*/*/local_*.json
local-agent-mode-sessions/skills-plugin/
local-agent-mode-sessions/*/*/rpm/
local-agent-mode-sessions/*/*/local_*/
local-agent-mode-sessions/*/*/cowork-gb-cache.json
EOF
  # create the tombstones list only if absent — never truncate one the user maintains
  [ -f "$WT/claude-code-sessions/.tombstones" ] || : > "$WT/claude-code-sessions/.tombstones"
  python3 -I "$REPO_SRC/bin/ccd-safety.py" "$GD" "$WT" stage
  rc=0; safe_git --git-dir="$GD" --work-tree="$WT" diff --cached --quiet || rc=$?
  if [ "$rc" -gt 1 ]; then exit "$rc"; fi
  if [ "$rc" = 1 ]; then
    safe_git --git-dir="$GD" --work-tree="$WT" commit -q -m "install: baseline/refresh session registries"
  else echo "(nothing to commit)"; fi
  # Seed only after complete validation; do not import modules from the caller cwd.
  python3 -I -c 'import json,sys;print(json.load(open(sys.argv[1]+"/cowork-enabled-cli-ops.json"))["ownerAccountId"])' "$WT" > "$GD/last-owner"
  rm -- "$GD/ccd-operation.json"

fi

echo "== archive dir -> $ARCHIVE =="
run mkdir -p "$ARCHIVE/claude-projects"

if [ "$AGENTS" = 1 ]; then
  echo "== install launchd agents =="
  run mkdir -p "$LA"
  for base in com.ccd-session-sync.migrate-auto com.ccd-session-sync.projects-archive; do
    tpl="$REPO_SRC/launchagents/$base.plist.template"; out="$LA/$base.plist"
    if [ "$DRY" = 0 ]; then
      sed -e "s#__HOME__#$HOME#g" -e "s#__BIN__#$BIN#g" -e "s#__ARCHIVE__#$ARCHIVE#g" "$tpl" > "$out"
      plutil -lint "$out" >/dev/null
      launchctl bootout "gui/$(id -u)/$base" 2>/dev/null || true
      launchctl bootstrap "gui/$(id -u)" "$out"
    else
      echo "+ render $tpl -> $out (sed __HOME__/__BIN__/__ARCHIVE__), plutil -lint, launchctl bootstrap"
    fi
  done
else
  echo "== skipping launchd agents (--no-agents) — use 'ccd-migrate' manually =="
fi

echo
echo "Done. Next:"
echo "  ccd-migrate status      # inspect accounts + git state"
echo "  ccd-migrate --dry-run   # preview a migration"
echo "  ccd-migrate             # migrate now (or let the auto agent do it on account switch)"
echo "After a migration, fully quit & relaunch Claude Desktop to load the merged sidebar."
