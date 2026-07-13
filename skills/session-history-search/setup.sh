#!/bin/bash
# Install session-history-search CLI tools to ~/.claude/bin/
# Run this once after installing the skill.

set -e

SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="$HOME/.claude/bin"

mkdir -p "$BIN_DIR"

for tool in cc-sessions cc-search cc-transcript cc-index; do
    cp "$SKILL_DIR/bin/$tool" "$BIN_DIR/$tool"
    chmod +x "$BIN_DIR/$tool"
    echo "Installed $tool -> $BIN_DIR/$tool"
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
