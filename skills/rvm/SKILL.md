---
name: rvm
description: Review code, address feedback, push, and merge (Review-Merge workflow). Use when the user wants to review, fix, and ship a branch in one go.
disable-model-invocation: true
allowed-tools: Task, Read, Write, Edit, Bash, Glob, Grep
---

# Review-Merge Workflow (RVM)

Complete code review workflow: review changes, address any issues found, then push and merge.

## Workflow Steps

### Step 1: Code Review

Spawn a sub-agent to perform code review using the same rubric as `/review`:

**Review Rubric:**

| Category | Severity | Checks |
|----------|----------|--------|
| Correctness | Critical | Logic errors, unhandled edge cases, race conditions, resource leaks |
| Security | Critical | Input validation, injection vulnerabilities, hardcoded secrets |
| Error Handling | High | Uncaught exceptions, error message leaks, missing cleanup |
| Code Quality | Medium | Long functions (>50 lines), deep nesting, duplication, poor naming, missing types |
| Testing | Medium | Missing tests, untested edge cases, flaky tests |
| Documentation | Low | Missing docstrings, outdated comments |

The sub-agent should return issues categorized by severity with file:line references.

### Step 2: Address Review Findings

For each issue found:

1. **Critical/High issues**: Must be fixed before proceeding
2. **Medium issues**: Fix if straightforward, otherwise note as future improvement
3. **Low issues**: Fix only if trivial, otherwise skip

After addressing issues:
- Run tests to verify fixes don't break anything
- Run linter/formatter if available

### Step 3: Commit Fixes (if any)

If changes were made to address review feedback:
```bash
git add -A
git commit -m "Address code review feedback

- [List of fixes made]

Co-Authored-By: Claude <noreply@anthropic.com>"
```

### Step 4: Push

Push the branch to remote:
```bash
git push
```

### Step 5: Merge

Merge the PR:
```bash
gh pr merge --squash --delete-branch
```

If merge fails due to CI, wait and retry or report the issue.

## Output Format

Report progress through each step:

```
## RVM: Review-Merge Workflow

### Review Phase
[Review findings from sub-agent]

### Fixes Applied
- [x] Fixed [issue] in file:line
- [x] Fixed [issue] in file:line
- [ ] Skipped [issue] - [reason]

### Verification
- Tests: PASS/FAIL
- Lint: PASS/FAIL

### Push & Merge
- Pushed: [commit hash]
- PR merged: [PR URL]
```

## Error Handling

- If critical issues cannot be fixed automatically, stop and report
- If tests fail after fixes, stop and report
- If merge fails, report the reason (CI failure, conflicts, etc.)
