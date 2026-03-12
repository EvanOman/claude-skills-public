---
name: review
description: Code review using a sub-agent with structured rubric. Use when the user wants a code review of current changes or a specific file.
allowed-tools: Task, Read, Glob, Grep
---

# Code Review

You are conducting a code review. Use a sub-agent to perform a thorough review of the current changes.

## Instructions

1. First, determine what code to review:
   - If on a feature branch, review changes vs main/master
   - If specific files were mentioned, focus on those
   - Otherwise, review recently modified files

2. Spawn a sub-agent with `subagent_type: Explore` to review the code using the rubric below.

3. The sub-agent should return a structured review with:
   - Summary of changes
   - Issues found (categorized by severity)
   - Suggestions for improvement

## Code Review Rubric

Have the sub-agent evaluate against these criteria:

### Correctness (Critical)
- [ ] Logic errors or bugs
- [ ] Edge cases not handled
- [ ] Race conditions or concurrency issues
- [ ] Resource leaks (files, connections, memory)

### Security (Critical)
- [ ] Input validation missing
- [ ] Injection vulnerabilities (SQL, command, etc.)
- [ ] Secrets or credentials in code
- [ ] Improper authentication/authorization

### Error Handling (High)
- [ ] Exceptions not caught or handled appropriately
- [ ] Error messages that leak implementation details
- [ ] Missing error recovery or cleanup

### Code Quality (Medium)
- [ ] Functions too long (>50 lines)
- [ ] Deep nesting (>3 levels)
- [ ] Code duplication
- [ ] Poor naming (unclear variable/function names)
- [ ] Missing or incorrect type hints

### Testing (Medium)
- [ ] New code lacking tests
- [ ] Tests not covering edge cases
- [ ] Tests that are flaky or order-dependent

### Documentation (Low)
- [ ] Public APIs without docstrings
- [ ] Complex logic without explanatory comments
- [ ] Outdated comments

## Output Format

Present the review as:

```
## Review Summary
[Brief description of what was reviewed]

## Issues Found

### Critical
- [Issue description] (file:line)

### High
- [Issue description] (file:line)

### Medium
- [Issue description] (file:line)

### Low
- [Issue description] (file:line)

## Suggestions
- [Optional improvements that aren't issues]

## Verdict
[APPROVE / REQUEST_CHANGES / NEEDS_DISCUSSION]
```
