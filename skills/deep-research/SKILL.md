---
name: deep-research
description: Run parallelized multi-agent research on any topic. Use when the user asks to research a technology, strategy, architecture, library, approach, or any complex question that benefits from partitioning the search space across multiple sub-agents. Each agent writes its own report, then a synthesis report distills the high-value findings.
argument-hint: <topic or question>
disable-model-invocation: true
allowed-tools: Task, Read, Write, Edit, Bash(ls *), Bash(mkdir *), Glob, Grep, WebSearch, WebFetch, AskUserQuestion
---

# Deep Research

Parallelized multi-agent research with synthesis. Partition a research question across 6-15 sub-agents running in parallel, each producing a report file. Then synthesize all reports into a single document that surfaces the signal and drops the noise.

## Step 1: Understand the Research Question

Parse `$ARGUMENTS` to identify:
- **Topic**: What is being researched
- **Context**: Why (decision to make, problem to solve, opportunity to evaluate)
- **Constraints**: Timeline, budget, technical environment, audience

If the topic is too vague to partition, ask one focused clarifying question. Do not over-interview -- start researching with reasonable assumptions and note them.

## Step 2: Choose Output Location

Infer the best output location without asking:

1. **If working in a project** -- create a `research-<topic>/` subfolder in the current directory
2. **If the user specified a path** -- use it

Create the output directory. Only ask the user if the location is genuinely ambiguous.

## Step 3: Partition the Search Space

Design 6-15 sub-agent assignments that collectively cover the research question. Good partitions are:

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

Present the partition plan to the user as a numbered list, then **immediately launch the agents without waiting for confirmation**. The user will review results when they come back -- fire and forget is the default. Only pause for approval if the user explicitly asked to review the partition first.

## Step 4: Launch Sub-Agents in Parallel

Spawn all sub-agents using the Task tool with `subagent_type: "general-purpose"` and `run_in_background: true`. Launch as many as possible in a single message to maximize parallelism.

### Sub-Report Organization

When launching more than 6 agents, group them into themed subfolders within the output directory. For example:

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

Choose subfolder names that reflect the partition groups (e.g., "Context", "Alternatives", "Security", "Implementation", "Case Studies"). The synthesis report always stays at the root level. Create the subfolders before launching agents.

Each sub-agent prompt must include:
1. **The specific research question** for that partition
2. **Output file path** -- numbered sequentially, placed in the appropriate subfolder (e.g., `Context/01 - Topic Name.md`)
3. **Format instructions:**
   - YAML front matter with relevant tags
   - Executive summary at the top
   - Specific findings with sources cited inline
   - Actionable recommendations where applicable
   - Sources section at the bottom with URLs
4. **Depth instruction:** "Be thorough. This is research, not a summary. Include specific names, numbers, dates, URLs, code examples, and configuration snippets where relevant."
5. **Context instructions:** If the sub-agent needs to explore a codebase, vault, or local files for context, include the relevant paths and search hints directly in its prompt. Do NOT run separate Explore agents in the main context -- fold all exploration into the research sub-agents themselves.

Sub-agent prompt template:
```
Research the following topic and write a comprehensive report to [SUBFOLDER/FILE_PATH].

Topic: [SPECIFIC QUESTION FOR THIS PARTITION]

Context: [OVERALL RESEARCH CONTEXT]

Write the report directly to the file. Include:
- YAML front matter with tags: ai-generated, [topic tags]
- Executive summary (3-5 sentences)
- Detailed findings organized by subtopic
- Specific examples, numbers, and evidence
- Actionable recommendations
- Sources section with URLs

Be thorough and specific. Cite sources inline. Include code examples, configuration snippets, or technical details where relevant. This report will be read by a technical audience.

The file you write IS the deliverable. Your return message will not be read.
```

If there are more than 8 agents, launch them in batches of 8.

### Context Budget Awareness

The main agent's context window is finite (~200K tokens). Protect it aggressively:

**CRITICAL: Never call `TaskOutput` to retrieve sub-agent results.** `TaskOutput` returns a truncated copy of the sub-agent's full transcript (~32K chars / ~8K tokens per call), regardless of how brief the sub-agent's return message was. With 14 agents, this alone consumes ~112K tokens -- more than half the context window. Sub-agents communicate their results via the files they write, not via return values.

Additional rules:
- **Never read report files into the main context** -- the synthesis sub-agent reads them
- **Do not run Explore or research agents in the foreground** -- large results consume main context
- **Do not poll for completion** -- background task notifications arrive automatically at zero API token cost
- Keep the main agent's role minimal: dispatch sub-agents, wait for notifications, launch synthesis

## Step 5: Synthesize via Sub-Agent

**CRITICAL: Do NOT read the report files in the main agent.** Reading 6-15 full reports (~50-100K+ tokens) into the main context will exhaust the context window. Instead, delegate synthesis to a dedicated sub-agent.

After launching all research sub-agents, **wait for all background task notifications to arrive**. These notifications appear automatically as system reminders at zero API token cost. You do not need to poll or call TaskOutput -- just wait. Once all agents show as completed (or a reasonable time has passed), proceed.

Then spawn **one synthesis sub-agent** using the Task tool with `subagent_type: "general-purpose"`:

Synthesis sub-agent prompt template:
```
You are a research synthesis agent. Read all the individual research reports listed below and write a synthesis report.

Expected report files (check which ones exist -- some agents may have failed):
[LIST ALL EXPECTED FILE PATHS FROM STEP 4]

Write the synthesis to: [OUTPUT_DIR]/00 - Synthesis Report.md

For any expected report that does not exist, note it as a gap in the synthesis.

[INSERT SYNTHESIS REPORT STRUCTURE FROM BELOW]

After writing the synthesis, return ONLY:
- A 3-5 sentence summary of the key takeaway
- Count of how many reports you successfully read (and any that were missing)
- Any notable contradictions or surprises
- The file path where you wrote the synthesis

Do NOT return the full synthesis content. The file is the deliverable.
```

The synthesis sub-agent has its own ~200K token context window -- more than enough to read all reports and write the synthesis without affecting the main agent.

## Step 6: Synthesis Report Structure

Provide this structure to the synthesis sub-agent. The synthesis report should be written as `00 - Synthesis Report.md` (or `00 - [Topic] Synthesis.md`).

### Synthesis Report Structure

```markdown
# [Topic]: Research Synthesis

**Purpose:** [One sentence on why this research was done]
**Provenance:** AI-generated synthesis of [N] research reports. Treat as a well-researched starting point, not a final decision.

---

## The One-Paragraph Summary
[The entire research distilled into one paragraph. If the reader reads nothing else, this should give them the answer.]

---

## Key Findings
[3-7 major findings, each as a subsection with supporting evidence drawn from the individual reports. This is the meat of the synthesis.]

## Recommendations
[Prioritized, actionable recommendations. What should be done, in what order, and why.]

## Risks and Concerns
[What could go wrong. Be honest about uncertainties and limitations.]

## Cost and Effort Estimates
[If applicable -- what does this cost in money, time, and complexity?]

## Dead Ends and Low-Value Paths
[Brief section summarizing research directions that did not pan out. 2-3 sentences each, explaining why they were explored and why they were dropped. This saves future researchers from re-exploring them.]

## Report Index
[Table listing all individual reports with one-line summaries of each]
```

### Synthesis Principles

- **Prioritize signal over completeness.** The individual reports have the details. The synthesis has the conclusions.
- **Drop dead ends.** If an agent researched something that turned out irrelevant, don't force it into the synthesis. Mention it briefly in "Dead Ends" so the reader knows it was explored.
- **Cross-reference.** When multiple reports converge on the same finding from different angles, that's high-confidence. Call it out.
- **Flag contradictions.** When reports disagree, present both sides and your assessment of which is more credible and why.
- **Be opinionated.** The synthesis should have a point of view. "Here's what the research suggests you should do" is more useful than "here are all the options."
- **Cite the individual reports.** Use `[[Report Name]]` links so the reader can drill into details.

## Step 7: Present Results

After the synthesis sub-agent completes, present to the user:
- How many agents were launched and the output directory path
- The key takeaway in 2-3 sentences (from the synthesis agent's return value, which is small enough to read)
- Any surprising findings or contradictions noted by the synthesis agent
- Suggested next steps

**Do NOT read the synthesis report file in the main agent.** The user can read the full synthesis file themselves. The synthesis sub-agent's brief return message provides enough for the summary.

---

## Handling Edge Cases

**If a sub-agent fails or returns low-quality results:** Note it in the synthesis. Do not re-run unless the user asks.

**If the research question is too narrow for 6+ agents:** Use fewer agents (minimum 3). Not every question needs 12 sub-agents.

**If the user provides a URL or document as input:** Read it first, then design the partition based on the questions it raises.

**If the project has specific documentation conventions:** Follow them for front matter, tagging, and citation style.
