#!/bin/bash
# Install session-history-search CLI tools to ~/.claude/bin/
# Run this once after installing the skill.
#
# By default the tools are SYMLINKED, so a `git pull` of this repo updates
# them with no re-install. Pass --copy to install standalone copies instead
# (for users who don't keep the repo checked out).
#
# Usage:
#   bash setup.sh            # symlink bin/* into ~/.claude/bin (default)
#   bash setup.sh --copy     # copy bin/* into ~/.claude/bin

set -e

SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="$HOME/.claude/bin"

MODE="symlink"
for arg in "$@"; do
    case "$arg" in
        --copy) MODE="copy" ;;
        *) echo "Unknown option: $arg (supported: --copy)"; exit 1 ;;
    esac
done

mkdir -p "$BIN_DIR"

for tool in cc-sessions cc-search cc-transcript cc-index; do
    src="$SKILL_DIR/bin/$tool"
    dest="$BIN_DIR/$tool"
    if [[ "$MODE" == "copy" ]]; then
        cp "$src" "$dest"
        chmod +x "$dest"
        echo "Copied   $tool -> $dest"
    else
        ln -sf "$src" "$dest"
        echo "Linked   $tool -> $dest"
    fi
done

# Add ~/.claude/bin to PATH if not already there
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    echo ""
    echo "Add this to your shell profile (~/.bashrc, ~/.zshrc, etc.):"
    echo "  export PATH=\"\$HOME/.claude/bin:\$PATH\""
fi

echo ""
echo "Done. Run 'cc-index' to build the search index, then try:"
echo "  cc-sessions             # List recent sessions"
echo "  cc-search \"keyword\"     # Search history"
echo "  cc-transcript <id>      # Read a session"
