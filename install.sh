#!/usr/bin/env bash
# install.sh — set up ccd-session-sync: scripts, git-versioned registry repo, and launchd agents.
# Idempotent. Env overrides: CCD_BIN (default ~/bin), CCD_ARCHIVE (default ~/ClaudeArchive).
# Flags: --no-agents (install scripts + git repo only, skip launchd), --dry-run (print, do nothing).
set -euo pipefail

REPO_SRC="$(cd "$(dirname "$0")" && pwd)"
BIN="${CCD_BIN:-$HOME/bin}"
GD="$HOME/.ccd-sessions-git"
WT="$HOME/Library/Application Support/Claude"
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
[ "$(uname)" = "Darwin" ] || { echo "ERROR: macOS only." >&2; exit 1; }
for t in git python3 rsync; do command -v "$t" >/dev/null || { echo "ERROR: '$t' not found in PATH." >&2; exit 1; }; done
[ -d "$WT/claude-code-sessions" ] || { echo "ERROR: '$WT/claude-code-sessions' not found. Install & launch Claude Desktop once, then re-run." >&2; exit 1; }
case ":$PATH:" in *":$BIN:"*) : ;; *) echo "WARNING: $BIN is not on your PATH — add it to ~/.zshrc: export PATH=\"$BIN:\$PATH\"";; esac

echo "== install scripts -> $BIN =="
run mkdir -p "$BIN"
for s in ccd-migrate ccd-migrate-auto claude-archive-sync claude-second; do
  run cp "$REPO_SRC/bin/$s" "$BIN/$s"
  run chmod +x "$BIN/$s"
done

echo "== bootstrap git-versioned registry repo -> $GD =="
if [ "$DRY" = 1 ]; then
  echo "+ (dry-run) init/refresh $GD, refresh info/exclude, seed last-owner, baseline commit"
else
  if [ ! -d "$GD" ]; then
    git init --bare "$GD" >/dev/null
    git --git-dir="$GD" config core.bare false
    git --git-dir="$GD" config user.name ccd-session-sync
    git --git-dir="$GD" config user.email ccd-session-sync@localhost
  else
    echo "repo already exists — leaving history intact, refreshing config"
  fi
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
  gadd=""; for p in claude-code-sessions local-agent-mode-sessions cowork-enabled-cli-ops.json; do [ -e "$WT/$p" ] && gadd="$gadd $p"; done
  [ -n "$gadd" ] && git --git-dir="$GD" --work-tree="$WT" add -A -- $gadd
  git --git-dir="$GD" --work-tree="$WT" commit -q -m "install: baseline/refresh session registries" || echo "(nothing to commit)"
  # (re)seed auto-migrate state on EVERY install so a reinstall never fires a stale-owner migration
  python3 -c 'import json,sys;print(json.load(open(sys.argv[1]+"/cowork-enabled-cli-ops.json"))["ownerAccountId"])' "$WT" > "$GD/last-owner" 2>/dev/null || true
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
