export const meta = {
  name: 'deep-research',
  description: 'Fan out research partitions to parallel agents, then synthesize a single report',
  whenToUse: 'Invoked by the deep-research skill after the partition plan is designed. Do not invoke directly without args.',
  phases: [
    { title: 'Research', detail: 'one agent per partition, each writes a report file' },
    { title: 'Synthesize', detail: 'one agent reads all reports and writes the synthesis' },
  ],
}

// Expected args (all required):
// {
//   topic: string,           // what is being researched
//   context: string,         // why — decision to make, constraints, audience
//   outputDir: string,       // absolute path; directories must already exist
//   date: string,            // YYYY-MM-DD (scripts cannot call Date)
//   partitions: [            // 3-15 entries, designed by the main agent
//     { file: 'Context/01 - Topic Name.md', question: 'specific research question' },
//   ],
// }

const { topic, context, outputDir, date, partitions } = args

const REPORT_SCHEMA = {
  type: 'object',
  properties: {
    filePath: { type: 'string', description: 'Absolute path of the report file you wrote' },
    summary: { type: 'string', description: 'Executive summary of your findings, 3-5 sentences' },
    sourceCount: { type: 'number', description: 'How many distinct sources you cited' },
    surprises: {
      type: 'array',
      items: { type: 'string' },
      description: 'Findings that contradict conventional wisdom or the research framing, if any',
    },
  },
  required: ['filePath', 'summary', 'sourceCount'],
}

const SYNTH_SCHEMA = {
  type: 'object',
  properties: {
    filePath: { type: 'string', description: 'Absolute path of the synthesis report you wrote' },
    keyTakeaway: { type: 'string', description: 'The key takeaway, 3-5 sentences' },
    reportsRead: { type: 'number' },
    contradictions: {
      type: 'array',
      items: { type: 'string' },
      description: 'Notable contradictions or surprises across reports',
    },
    suggestedNextSteps: { type: 'array', items: { type: 'string' } },
  },
  required: ['filePath', 'keyTakeaway', 'reportsRead'],
}

function researchPrompt(p) {
  return `Research the following topic and write a comprehensive report to ${outputDir}/${p.file}.

Topic: ${p.question}

Overall research context: ${context}

Start the file with YAML front matter:
---
title: "<descriptive title>"
created: ${date}
tags:
  - ai-generated
  - <topic tags, lowercase-hyphenated>
---

The report must include:
- Executive summary (3-5 sentences)
- Detailed findings organized by subtopic
- Specific examples, numbers, dates, and evidence
- Actionable recommendations where applicable
- A Sources section at the bottom with URLs

Be thorough and specific — this is research, not a summary. Cite sources inline. Include code examples, configuration snippets, or technical details where relevant. This report will be read by a technical audience. Use WebSearch and WebFetch to ground every substantive claim in a real source.

Write the report directly to the file with the Write tool. The file is the deliverable; your structured return carries only metadata about it.`
}

phase('Research')
const results = await parallel(
  partitions.map((p) => () =>
    agent(researchPrompt(p), { label: p.file, phase: 'Research', schema: REPORT_SCHEMA })
  )
)
const reports = results
  .map((r, i) => (r ? { ...r, partition: partitions[i] } : null))
  .filter(Boolean)
const missing = partitions.filter((p, i) => !results[i]).map((p) => p.file)

if (missing.length) log(`${missing.length} research agent(s) failed: ${missing.join(', ')}`)
log(`${reports.length}/${partitions.length} reports written to ${outputDir}`)

if (reports.length === 0) {
  return { error: 'All research agents failed; no reports to synthesize.', missing }
}

phase('Synthesize')
const synthesisPrompt = `You are a research synthesis agent. Read all the individual research reports listed below and write a synthesis report that surfaces the signal and drops the noise.

Research topic: ${topic}
Research context: ${context}

Report files (all verified written — read every one):
${reports.map((r) => `- ${r.filePath}`).join('\n')}
${missing.length ? `\nThese partitions FAILED and have no report — note them as gaps in the synthesis:\n${missing.map((f) => `- ${f}`).join('\n')}` : ''}

Write the synthesis to: ${outputDir}/00 - Synthesis Report.md

Use this structure:

# [Topic]: Research Synthesis

**Purpose:** [One sentence on why this research was done]
**Provenance:** AI-generated synthesis of ${reports.length} research reports (${date}). Treat as a well-researched starting point, not a final decision.

## The One-Paragraph Summary
[The entire research distilled into one paragraph. If the reader reads nothing else, this should give them the answer.]

## Key Findings
[3-7 major findings, each as a subsection with supporting evidence drawn from the individual reports. This is the meat of the synthesis.]

## Recommendations
[Prioritized, actionable recommendations. What should be done, in what order, and why.]

## Risks and Concerns
[What could go wrong. Be honest about uncertainties and limitations.]

## Cost and Effort Estimates
[If applicable — what does this cost in money, time, and complexity?]

## Dead Ends and Low-Value Paths
[Research directions that did not pan out, 2-3 sentences each: why explored, why dropped. This saves future researchers from re-exploring them.]

## Report Index
[Table listing all individual reports with one-line summaries of each]

Synthesis principles:
- Prioritize signal over completeness — the individual reports have the details; the synthesis has the conclusions.
- Drop dead ends from the main sections; mention them briefly in "Dead Ends" so the reader knows they were explored.
- Cross-reference: when multiple reports converge on a finding from different angles, that is high-confidence — call it out.
- Flag contradictions: when reports disagree, present both sides and your assessment of which is more credible and why.
- Be opinionated: "here's what the research suggests you should do" beats "here are all the options."
- Cite the individual reports with [[Report Name]] links so the reader can drill into details.

The file is the deliverable; your structured return carries only the key takeaway and metadata.`

const synthesis = await agent(synthesisPrompt, {
  label: 'synthesis',
  phase: 'Synthesize',
  schema: SYNTH_SCHEMA,
})

if (!synthesis) {
  log('Synthesis agent failed — reports are on disk, resume this run to retry synthesis only.')
}

return {
  error: synthesis ? undefined : 'Synthesis agent failed; report files are intact. Resume with resumeFromRunId to retry.',
  synthesis,
  outputDir,
  reports: reports.map((r) => ({
    file: r.filePath,
    summary: r.summary,
    sourceCount: r.sourceCount,
    surprises: r.surprises || [],
  })),
  missing,
}
