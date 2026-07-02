---
name: deep-research
description: Run parallelized multi-agent research on any topic via a deterministic workflow. Use when the user asks to research a technology, strategy, architecture, library, approach, or any complex question that benefits from partitioning the search space across multiple sub-agents. Each agent writes its own report, then a synthesis report distills the high-value findings.
argument-hint: <topic or question>
disable-model-invocation: true
allowed-tools: Workflow, Task, Read, Write, Edit, Bash(ls *), Bash(mkdir *), Glob, Grep, WebFetch, AskUserQuestion
---

# Deep Research

Parallelized multi-agent research with synthesis. The main agent's job is judgment: understand the question, pick the output location, and design the partition. Execution — fan-out, synchronization, failure handling, synthesis — is delegated to the `workflow.js` script that ships in this skill's directory. Do not orchestrate the fan-out by hand with Task calls.

## Step 1: Understand the Research Question

Parse `$ARGUMENTS` to identify:
- **Topic**: What is being researched
- **Context**: Why (decision to make, problem to solve, opportunity to evaluate)
- **Constraints**: Timeline, budget, technical environment, audience

If the topic is too vague to partition, ask one focused clarifying question. Do not over-interview -- start researching with reasonable assumptions and note them.

If the user provides a URL or document as input, read it first, then design the partition based on the questions it raises.

## Step 2: Choose Output Location

Infer the best output location without asking:

1. **If working in a project** -- create a `research-<topic>/` subfolder in the current directory
2. **If the user specified a path** -- use it

Only ask the user if the location is genuinely ambiguous.

## Step 3: Partition the Search Space

Design 6-15 sub-agent assignments that collectively cover the research question (minimum 3 for narrow questions -- not every question needs 12 sub-agents). Good partitions are:

- **MECE** (mutually exclusive, collectively exhaustive) -- minimal overlap, no gaps
- **Independently researchable** -- each agent can work without the others' results
- **Concrete** -- each has a clear deliverable, not a vague exploration

Common partition patterns (pick the one that fits):

**Technology evaluation:**
- Core capabilities and architecture
- Ecosystem and integrations
- Pricing and licensing
- Case studies and adoption
- Alternatives and competitive landscape
- Security and compliance
- Migration path and setup
- Performance and scalability

**Strategic question:**
- Current state analysis
- Market/competitive landscape
- Case studies (successes and failures)
- Technical feasibility
- Cost-benefit analysis
- Risk assessment
- Implementation approach
- Alternative perspectives / contrarian views

**Library or tool evaluation:**
- API and developer experience
- Performance benchmarks
- Community and maintenance health
- Integration with existing stack
- Alternatives comparison
- Production readiness

**Always include these two agents regardless of pattern:**
1. **Case studies / real-world examples** -- ground the research in what others have actually done
2. **Alternative perspectives / contrarian views** -- challenge the default framing with 8-12 different lenses on the problem

### Recency discipline (scope it to the domain)

Match the source-age policy to how fast the topic moves; state it explicitly in the `context` arg either way.

- **Fast-moving domains (AI tooling, models, frameworks, current tech practice):** put a **hard, dated recency constraint in the `context` arg** that every agent inherits -- "cite only sources published in <current year>; tools without meaningful updates this year are irrelevant" -- not a soft preference. And do not name older tools or papers as examples in partition questions except to exclude them: **named examples in prompts become citations in output**.
- **Historical or stable domains (history, mathematics, settled science, biography):** recency does not apply and can actively hurt -- older scholarship, primary sources, and period documents are often the best evidence. Constrain for source *quality and authority* instead (primary over secondary, scholarly over pop).
- **Mixed topics** (e.g., "the history of X up to its current state"): split the recency rule per partition -- historical partitions get the quality constraint, current-state partitions get the dated recency constraint.

### File layout

Number report files sequentially. When there are more than 6 partitions, group them into themed subfolders (e.g., "Context", "Approaches", "Alternatives", "Risks", "Case Studies") that reflect the partition groups:

```
Research Topic/
├── 00 - Synthesis Report.md
├── Context/
│   ├── 01 - ...
│   └── 02 - ...
├── Approaches/
│   ├── 03 - ...
│   └── 04 - ...
└── Risks/
    ├── 05 - ...
    └── 06 - ...
```

The synthesis report always stays at the root level.

Create the output directory and all subfolders with `mkdir -p` **before** launching the workflow -- the workflow script cannot create directories.

Present the partition plan to the user as a numbered list, then **immediately launch the workflow without waiting for confirmation**. The user reviews results when they come back -- fire and forget is the default. Only pause for approval if the user explicitly asked to review the partition first.

## Step 4: Launch the Workflow

Resolve the absolute path to `workflow.js` -- it lives in the same directory as this SKILL.md file. Then invoke the Workflow tool:

```
Workflow({
  scriptPath: "<absolute path to this skill's workflow.js>",
  args: {
    topic: "<what is being researched>",
    context: "<why -- decision, constraints, audience>",
    outputDir: "<absolute path from Step 2>",
    date: "<today, YYYY-MM-DD>",          // the script cannot call Date
    partitions: [
      { file: "Context/01 - Topic Name.md", question: "<specific research question for this partition>" },
      ...
    ]
  }
})
```

Each partition's `question` should be self-contained: if a researcher needs local context (a codebase, docs, prior decisions), fold the relevant paths and search hints directly into that partition's `question` text -- researchers cannot see each other or this conversation.

The workflow runs in the background and handles the entire execution deterministically: parallel fan-out (concurrency capped automatically -- no batching needed), waiting for completion, dropping failed agents, and spawning the synthesis agent with the list of reports that actually exist. Do not poll, do not call TaskOutput, and do not read the report files -- the workflow's return value contains everything needed for Step 5.

**If the workflow fails partway** (killed, crashed, synthesis failed): re-invoke Workflow with the same `scriptPath` and `args` plus `resumeFromRunId: "<runId from the failed run>"`. Completed research agents replay from cache; only unfinished work re-runs.

**If the Workflow tool is unavailable in this environment**, fall back to the legacy pattern: parallel background Task calls (`subagent_type: "general-purpose"`) one per partition using the same prompt structure as workflow.js, then one synthesis Task agent -- never reading report files or TaskOutput into the main context.

## Step 5: Present Results

The workflow returns `{ synthesis, outputDir, reports, missing }` where `synthesis` has the key takeaway, contradictions, and suggested next steps, and `reports` has a per-report file path and mini-summary. Present to the user:

- How many agents ran, how many failed (`missing`), and the output directory path
- The key takeaway in 2-3 sentences (from `synthesis.keyTakeaway`)
- Any contradictions or surprises (`synthesis.contradictions`, per-report `surprises`)
- Suggested next steps

**Do NOT read the synthesis report file in the main agent.** The user can read the full synthesis file themselves; the workflow return provides enough for the summary.

---

## Handling Edge Cases

**If a research agent fails:** The workflow drops it automatically and the synthesis notes it as a gap. Do not re-run unless the user asks; if they do, resume the workflow run rather than starting over.

**If the project has specific documentation conventions:** Follow them for front matter, tagging, and citation style -- pass any deviations from the defaults into each partition's `question` text.

**If the user asks for changes to the research process itself** (different stages, a verification pass, different report format): edit `workflow.js` in this skill's directory -- the orchestration lives there, not in this file.
