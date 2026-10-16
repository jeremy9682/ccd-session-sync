#!/usr/bin/env bash
# uninstall.sh — remove ccd-session-sync agents and scripts.
# By default KEEPS your git history repo (~/.ccd-sessions-git) and archive (~/ClaudeArchive) —
# they are your backups. Pass --purge to also delete those (irreversible).
set -euo pipefail
BIN="${CCD_BIN:-$HOME/bin}"
LA="$HOME/Library/LaunchAgents"
PURGE=0; [ "${1:-}" = "--purge" ] && PURGE=1

for base in com.ccd-session-sync.migrate-auto com.ccd-session-sync.projects-archive; do
  launchctl bootout "gui/$(id -u)/$base" 2>/dev/null || true
  rm -f "$LA/$base.plist"
done
rm -f "$BIN/ccd-migrate" "$BIN/ccd-migrate-auto" "$BIN/claude-archive-sync" "$BIN/claude-second"
echo "removed agents + scripts."

if [ "$PURGE" = 1 ]; then
  rm -rf "$HOME/.ccd-sessions-git" "${CCD_ARCHIVE:-$HOME/ClaudeArchive}"
  echo "PURGED git history repo and archive."
else
  echo "kept ~/.ccd-sessions-git (git history) and ~/ClaudeArchive (transcript backup). Pass --purge to remove."
fi
