#!/usr/bin/env bash
# Read-only structural probe for a Python codebase, for DDD audits.
# Gathers evidence. Interprets nothing. Every number here is a fact you can cite.
#
# Usage: bash probe.sh [repo_root]   (default: cwd)
#
# Writes nothing anywhere and installs nothing. Safe on a read-only filesystem.

set -uo pipefail
ROOT="${1:-.}"
cd "$ROOT" || exit 1

# .worktrees and worktrees are pruned deliberately: a checkout containing sibling
# git worktrees will otherwise report every file two or three times, which inflates
# LOC and file counts and can push the sizing gate's scale cap to the wrong tier.
PRUNE=( -name .git -o -name .venv -o -name venv -o -name node_modules
        -o -name site-packages -o -name __pycache__ -o -name migrations
        -o -name .mypy_cache -o -name .pytest_cache -o -name .tox
        -o -name .worktrees -o -name worktrees -o -name .direnv -o -name build
        -o -name dist -o -name '*.egg-info' )

# All python files, vendor dirs pruned.
pyfiles() { find . \( "${PRUNE[@]}" \) -prune -o -name '*.py' -print 2>/dev/null; }
# Directories, vendor dirs pruned.
pydirs()  { find . \( "${PRUNE[@]}" \) -prune -o -type d -print 2>/dev/null; }

# grep over first-party python only. usage: pgrep_py <grep-args...>
pgrep_py() { pyfiles | tr '\n' '\0' | xargs -0 -r grep "$@" 2>/dev/null; }

hr() { printf '\n=== %s ===\n' "$1"; }

hr "SIZE AND SHAPE"
NFILES=$(pyfiles | wc -l)
NLOC=$(pyfiles | tr '\n' '\0' | xargs -0 -r cat 2>/dev/null | wc -l)
echo "python files: $NFILES"
echo "python LOC:   $NLOC"
echo
echo "largest modules (LOC — orchestration usually hides in the top 3):"
pyfiles | tr '\n' '\0' | xargs -0 -r wc -l 2>/dev/null | sort -rn | grep -v ' total$' | head -10
echo
echo "package layout:"
pydirs | grep -v '^\.$' | grep -v '/\.' | sort | head -30

hr "LAYER NAMING (do the conventional layer names exist at all?)"
for layer in domain model models entities service_layer services usecases use_cases \
             adapters entrypoints api repositories infrastructure infra core; do
  hits=$(pydirs | grep -E "/${layer}$" | head -3 | tr '\n' ' ')
  [ -n "$hits" ] && printf '  %-16s %s\n' "$layer" "$hits"
done
echo "  (names alone prove nothing — the import check below is what decides)"

hr "THE ONE LAW: does the domain import infrastructure?"
INFRA='^\s*(from|import)\s+(sqlalchemy|django|flask|fastapi|starlette|requests|httpx|aiohttp|redis|boto3|psycopg|sqlite3|pymongo|celery|openai|anthropic|smtplib)'
DOMAIN_DIRS=$(pydirs | grep -E '/(domain|entities|model|models)$' | tr '\n' ' ')
if [ -z "$DOMAIN_DIRS" ]; then
  echo "No package is NAMED domain/entities/model(s)."
  echo "That is not the same as having no domain. A package can be the functional core"
  echo "under any name — judge by imports, not by directory name."
  echo
  echo "Cleanest first-party packages, fewest infrastructure imports first."
  echo "The top entry is the best candidate for the domain core:"
  for d in $(pydirs | grep -vE '^\.$|/\.|/tests?$' | grep -E '^\./[^/]+(/[^/]+)?$'); do
    tot=$(find "$d" -maxdepth 1 -name '*.py' 2>/dev/null | wc -l)
    [ "${tot:-0}" -eq 0 ] && continue
    bad=$(grep -rlE "$INFRA" "$d" --include='*.py' 2>/dev/null | grep -cvE '/tests?/')
    printf '  %-44s %2s/%-2s modules import infrastructure\n' "$d" "${bad:-0}" "$tot"
  done | sort -t' ' -k2 -n | head -12
  echo
  echo "Whole-tree infrastructure imports, by module (shows how far I/O has spread):"
  pgrep_py -lE "$INFRA" | grep -vE '/tests?/' | head -20
  echo "  modules importing infrastructure: $(pgrep_py -lE "$INFRA" | grep -cvE '/tests?/')  of $NFILES"
else
  echo "domain packages: $DOMAIN_DIRS"
  echo
  echo "infrastructure imports inside them (each line is a citable finding):"
  # shellcheck disable=SC2086
  grep -rnE "$INFRA" $DOMAIN_DIRS --include='*.py' 2>/dev/null | grep -vE '/tests?/' | head -25
  # shellcheck disable=SC2086
  echo "  count: $(grep -rnE "$INFRA" $DOMAIN_DIRS --include='*.py' 2>/dev/null | grep -cvE '/tests?/')"
fi

hr "ORM COUPLING: are domain classes ORM subclasses?"
# The trailing [,)] matters: without it, `Base` also matches pydantic's `BaseModel`
# and every pydantic schema gets misreported as an ORM-mapped domain class.
ORMBASE='class\s+\w+\((Base|db\.Model|models\.Model|DeclarativeBase|SQLModel)\s*[,)]'
pgrep_py -nE "$ORMBASE" | head -20
echo "  ORM-derived classes: $(pgrep_py -hoE "$ORMBASE" | wc -l)"
echo "  (pydantic BaseModel is deliberately NOT counted here — it is a schema, not persistence)"
echo "  imperative mapping (domain stays clean): $(pgrep_py -lE 'map_imperatively|mapper_registry|orm\.mapper\(' | tr '\n' ' ')"

hr "TRANSACTION BOUNDARIES: where does commit happen?"
pgrep_py -nE '\.commit\(\)' | grep -vE '/tests?/' | head -20
echo "  distinct files calling commit(): $(pgrep_py -lE '\.commit\(\)' | grep -cvE '/tests?/')"
echo "  (spread across several layers means transaction scope was never designed)"
echo "  unit-of-work module: $(pyfiles | grep -E 'unit_of_work|/uow' | tr '\n' ' ')"

hr "PERSISTENCE ACCESS: is querying centralized?"
echo "modules issuing queries directly:"
pgrep_py -lE 'session\.(query|execute|scalars)|\.objects\.(filter|get|all)\(|cursor\.execute|select\(' | grep -vE '/tests?/' | head -20
echo "  repository modules: $(pyfiles | grep -iE 'repositor' | tr '\n' ' ')"
echo "  (queries in more than two non-repository modules means the data layer is diffuse)"

hr "CONFIG AND DEPENDENCY ACQUISITION"
echo "modules reading env/config directly (should be the composition root only):"
pgrep_py -lE 'os\.environ|os\.getenv|getenv\(|load_dotenv|BaseSettings' | grep -vE '/tests?/' | head -15
echo "  count: $(pgrep_py -lE 'os\.environ|os\.getenv|load_dotenv|BaseSettings' | grep -cvE '/tests?/')"
echo "  composition root: $(pyfiles | grep -E 'bootstrap|container|wiring' | tr '\n' ' ')"

hr "TEST SHAPE"
TESTFILES=$(pyfiles | grep -E '/tests?/|test_.*\.py$|.*_test\.py$')
NTESTS=$(echo "$TESTFILES" | grep -c . )
echo "test files: $NTESTS"
if [ "$NTESTS" -gt 0 ]; then
  echo "test functions: $(echo "$TESTFILES" | tr '\n' '\0' | xargs -0 -r grep -hcE '^\s*(async )?def test_' 2>/dev/null | awk '{s+=$1} END {print s+0}')"
  echo "by directory:"
  echo "$TESTFILES" | xargs -r -n1 dirname 2>/dev/null | sort | uniq -c | sort -rn | head -8
  echo "  mock.patch / MagicMock uses: $(echo "$TESTFILES" | tr '\n' '\0' | xargs -0 -r grep -hoE 'mock\.patch|@patch|MagicMock|monkeypatch' 2>/dev/null | wc -l)"
  echo "  (high relative to test count = dependencies are grabbed, not injected)"
  echo "  private-member access in tests: $(echo "$TESTFILES" | tr '\n' '\0' | xargs -0 -r grep -hoE '\._[a-z]\w*' 2>/dev/null | wc -l)"
fi
echo "  hand-written fakes: $(pgrep_py -lE 'class (Fake|InMemory|Stub)' | tr '\n' ' ')"

hr "SIDE EFFECTS SITTING NEXT TO DECISIONS (candidates for extraction)"
pyfiles | while read -r f; do
  io=$(grep -cE 'requests\.|httpx\.|\.post\(|smtplib|subprocess\.|open\(|send_' "$f" 2>/dev/null)
  br=$(grep -cE '^\s+(if|elif) ' "$f" 2>/dev/null)
  if [ "${io:-0}" -gt 0 ] && [ "${br:-0}" -gt 5 ]; then
    printf '  %-52s %2s branches + %2s I/O calls\n' "$f" "$br" "$io"
  fi
done | sort -k2 -rn | head -12

hr "LANGUAGE: the vocabulary the code actually speaks"
echo "class names:"
pgrep_py -hoE '^class\s+[A-Z]\w+' | awk '{print $2}' | sort | uniq -c | sort -rn | head -25
echo
echo "leading verbs of functions (use cases hide here):"
pgrep_py -hoE '^(async )?def\s+[a-z_]+' | sed -E 's/^(async )?def //' | grep -v '^_\|^test_' \
  | awk -F_ '{print $1}' | sort | uniq -c | sort -rn | head -20
echo
echo "possible synonym drift (one concept, several words):"
CLASSES=$(pgrep_py -hoE '^class\s+[A-Z]\w+' | awk '{print tolower($2)}' | sort -u)
for w in item record entry signal event message job task run request draft post note doc document \
         user account session order budget policy rule config state status result; do
  m=$(printf '%s\n' "$CLASSES" | grep -c "$w")
  [ "${m:-0}" -gt 1 ] && printf '  "%s" appears inside %s distinct class names\n' "$w" "$m"
done

hr "CHURN: where the system is actually being changed"
if [ -d .git ]; then
  echo "most-modified python files, last 120 days (rework belongs where change pressure is):"
  git log --since='120 days ago' --name-only --pretty=format: -- '*.py' 2>/dev/null \
    | grep -v '^$' | sort | uniq -c | sort -rn | head -12
else
  echo "not a git repo — skipping churn analysis"
fi

hr "DONE"
echo "These are facts, not findings. Every claim in the audit must cite a file and line."
