#!/usr/bin/env bash
# usage-check.sh — overmind pre-flight: where does each worker backend stand on quota?
# Prints one line per backend. Never prints tokens/keys.
set -uo pipefail

echo "== overmind backend usage ($(date '+%Y-%m-%d %H:%M')) =="

# --- codex (ChatGPT subscription) -------------------------------------------
# No usage API in codex 0.141; every session JSONL carries rate_limits snapshots.
# Parse the newest one and report its age.
codex_line() {
  local files
  files=$(grep -rl '"rate_limits"' "$HOME/.codex/sessions" 2>/dev/null | xargs -r ls -t 2>/dev/null)
  if [[ -z "$files" ]]; then echo "codex     : no rate-limit snapshot found (run any codex task to refresh)"; return; fi
  python3 - $files << 'EOF'
import json, sys, time, datetime
# Newest file first; some sessions only carry "rate_limits":null — walk back until a
# file yields a real snapshot (take the last one in that file).
def usable(r):
    return isinstance(r, dict) and (r.get('primary') or {}).get('used_percent') is not None
def find(x):
    if isinstance(x, dict):
        if usable(x.get('rate_limits')): return x['rate_limits']
        for v in x.values():
            r = find(v)
            if r: return r
    return None
snap, src = None, None
for path in sys.argv[1:]:
    for line in open(path):
        if '"rate_limits"' not in line: continue
        try: d = json.loads(line)
        except Exception: continue
        r = find(d)
        if r: snap, src = r, path
    if snap: break
if not snap:
    print("codex     : no parseable snapshot in recent sessions (run any codex task to refresh)"); sys.exit()
sys.argv[1] = src  # downstream age calculation reads argv[1]
p = snap.get('primary') or {}
used = p.get('used_percent'); win = p.get('window_minutes') or 0
resets = p.get('resets_at')
reset_s = datetime.datetime.fromtimestamp(resets).strftime('%m-%d %H:%M') if resets else '?'
age_h = (time.time() - __import__('os').path.getmtime(sys.argv[1])) / 3600
plan = snap.get('plan_type', '?')
label = 'weekly' if win >= 10000 else f'{win}min'
print(f"codex     : ChatGPT plan={plan} · {label} {used:.0f}% used · resets {reset_s} · snapshot {age_h:.1f}h old")
EOF
}
codex_line

# --- claude (Anthropic Max plan OAuth) ---------------------------------------
python3 - << 'EOF'
import json, urllib.request, os, time
CACHE = os.path.expanduser('~/.cache/overmind-claude-usage.json')
def render(d, stale_h=None):
    parts = []
    fh = d.get('five_hour') or {}
    sd = d.get('seven_day') or {}
    if fh: parts.append(f"5h {fh.get('utilization', '?'):.0f}%")
    if sd: parts.append(f"7d {sd.get('utilization', '?'):.0f}%")
    for lim in d.get('limits') or []:
        scope = (lim.get('scope') or {}).get('model') or {}
        name = scope.get('display_name')
        if name:
            parts.append(f"{name} 7d {lim.get('percent', '?')}%")
    eu = d.get('extra_usage') or {}
    extra = 'enabled' if eu.get('is_enabled') else 'disabled'
    stale = f' · snapshot {stale_h:.1f}h old' if stale_h else ''
    print(f"claude    : Max plan · {' · '.join(parts)} · extra-usage {extra}{stale}")

# Cloudflare fronts this endpoint and hard-flags clients that burst it (a 429 here
# can persist for hours, and retry loops sustain the flag — never poll it on failure).
# Serve a fresh-enough cache without touching the network; fall back to stale on error.
TTL_H = 1.0
cache_age = (time.time() - os.path.getmtime(CACHE)) / 3600 if os.path.exists(CACHE) else None
if cache_age is not None and cache_age < TTL_H:
    render(json.load(open(CACHE)), stale_h=cache_age)
else:
    try:
        tok = json.load(open(os.path.expanduser('~/.claude/.credentials.json')))['claudeAiOauth']['accessToken']
        req = urllib.request.Request('https://api.anthropic.com/api/oauth/usage',
            headers={'Authorization': f'Bearer {tok}', 'anthropic-beta': 'oauth-2025-04-20'})
        d = json.load(urllib.request.urlopen(req, timeout=15))
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        json.dump(d, open(CACHE, 'w'))
        render(d)
    except Exception as e:
        if cache_age is not None:
            render(json.load(open(CACHE)), stale_h=cache_age)
        else:
            print(f"claude    : usage endpoint failed ({type(e).__name__}: {e}) — no cache yet; do NOT retry-loop (Cloudflare flag). Manual fallback: /usage in any interactive Claude Code session")
EOF

# --- opencode (z.ai GLM, metered) --------------------------------------------
KEYFILE="${OPENCODE_KEYFILE:-$HOME/.config/opencode/zai.key}"
if [[ -s "$KEYFILE" ]]; then
  echo "opencode  : z.ai key present ($KEYFILE) · metered API — check spend with opencode-worker.sh stats"
else
  echo "opencode  : NOT CONFIGURED (no key at $KEYFILE) — see reference/opencode-setup.md"
fi
