#!/bin/bash
# Install session-history-search CLI tools to ~/.claude/bin/
# Run this once after installing the skill.
#
# Installs both tool families:
#   cc-*  — Claude Code session history (cc-sessions, cc-search, cc-transcript, cc-index)
#   cx-*  — Codex CLI session history   (cx-sessions, cx-search, cx-transcript, cx-index)
#
# By default the tools are SYMLINKED, so a `git pull` of this repo updates
# them with no re-install. Pass --copy to install standalone copies instead
# (for users who don't keep the repo checked out).
#
# Usage:
#   bash setup.sh                    # symlink into ~/.claude/bin (default)
#   bash setup.sh --copy             # copy instead of symlinking
#   bash setup.sh --bin-dir DIR      # install somewhere else (e.g. a temp home)
#   bash setup.sh --dry-run          # print what would be installed, change nothing

set -e

SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="$HOME/.claude/bin"

MODE="symlink"
DRY_RUN=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --copy) MODE="copy"; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        --bin-dir)
            [[ $# -ge 2 ]] || { echo "--bin-dir requires a directory argument"; exit 1; }
            BIN_DIR="$2"; shift 2 ;;
        *) echo "Unknown option: $1 (supported: --copy, --dry-run, --bin-dir DIR)"; exit 1 ;;
    esac
done

# cx_common.py is the shared module behind the cx-* tools; it must sit next
# to them in the bin dir.
FILES="cc-sessions cc-search cc-transcript cc-index cx-sessions cx-search cx-transcript cx-index cx_common.py"

if [[ "$DRY_RUN" == "1" ]]; then
    echo "Dry run — nothing will be installed."
    for tool in $FILES; do
        echo "Would ${MODE} $tool -> $BIN_DIR/$tool"
    done
    exit 0
fi

mkdir -p "$BIN_DIR"

for tool in $FILES; do
    src="$SKILL_DIR/bin/$tool"
    dest="$BIN_DIR/$tool"
    if [[ "$MODE" == "copy" ]]; then
        cp "$src" "$dest"
        [[ "$tool" == *.py ]] || chmod +x "$dest"
        echo "Copied   $tool -> $dest"
    else
        ln -sf "$src" "$dest"
        echo "Linked   $tool -> $dest"
    fi
done

# Add the bin dir to PATH if not already there
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    echo ""
    echo "Add this to your shell profile (~/.bashrc, ~/.zshrc, etc.):"
    echo "  export PATH=\"$BIN_DIR:\$PATH\""
fi

echo ""
echo "Done. Claude Code history:            Codex CLI history:"
echo "  cc-index    # build search index      cx-index"
echo "  cc-sessions # list recent sessions    cx-sessions"
echo "  cc-search \"keyword\"                   cx-search \"keyword\""
echo "  cc-transcript <id>                    cx-transcript <id>"
