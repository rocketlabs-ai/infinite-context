# Infinite Context — Session Rebuild Protocol

The preferred approach for extending Claude Code sessions beyond the context window. Produces a session file indistinguishable from a continuous conversation — no compaction markers, no framing text, no detectable seam.

## How It Works

Instead of inserting a `compact_boundary` + summary message (which the model can detect), rebuild the entire session file:

1. **Compress older turns** — replace with a pre-written turn-by-turn summary, or truncate at sentence boundaries
2. **Preserve recent turns** — full fidelity, slimmed tool results
3. **Write as real JSONL messages** — compressed turns look like normal conversation entries
4. **Linearize the chain** — every entry's parentUuid points to the previous entry
5. **No compact_boundary** — file stays under 5MB so the loader reads it whole

The model sees one continuous conversation from start to finish.

## Why This Beats compact_boundary

The `compact_boundary` approach has unavoidable tells:
- Framing text: "This session is being continued from a previous conversation..."
- `isCompactSummary: true` flag on the summary message
- Grain change between polished summary and raw preserved messages
- The model knows compaction occurred and treats prior content as briefing, not lived experience

The rebuild approach has none of these. Compressed turns are real messages with real UUIDs, timestamps, and content. Tested: blind Opus agents given the rebuilt file could not detect the seam — each concluded they were reading an uncompacted conversation.

## Protocol Steps

### 1. Pre-Rebuild Capture

Before ANY compression, write notes capturing what matters while context is hot:
- Key decisions and reasoning
- Shared vocabulary and what it means (expand shorthand into full meaning)
- Unresolved threads and half-formed ideas
- Relationship context, emotional state, recent developments

This reference guides the summarizer in Step 3. Without it, important context gets lost.

### 2. Backup

```bash
cp <session.jsonl> <session.jsonl>.backup-manual
```

The rebuild script creates automatic backups, but always verify the backup exists before going live.

### 3. Write a turn-by-turn summary

Deploy an Opus agent with this prompt:

> Read the session JSONL at `<path>`. Also read the pre-rebuild notes at `<notes-path>` for guidance on what matters.
>
> Write a turn-by-turn summary as alternating USER: and ASSISTANT: lines.
>
> Rules:
> - Preserve the actual words, tone, and rhythm — not sanitized summaries
> - Expand shared shorthand into full context (what happened, what was felt, what produced it)
> - Compress operational noise: tool calls, monitoring turns, repetitive work, infrastructure
> - Tool call results truncated to essential outcomes only
> - The reader of this summary has NEVER experienced these events — give them enough to feel it, not just know it happened
> - Target: 4000-6000 words per chunk
>
> Output to: `summary.md`

**For sessions over 5MB:** Deploy multiple Opus agents in parallel, each covering a chronological chunk. Provide the pre-rebuild notes to each agent. Combine chunks in chronological order.

**Always use Opus for summarization.** Sonnet summaries lose texture and shorthand. The summary quality IS the continuity quality.

### 4. Verify summary quality

Read the head, middle, and tail of the summary:
- Does the texture hold throughout, or degrade in the middle/end?
- Is shorthand expanded into full meaning, or are there empty references?
- Does it read like a conversation or a lab report?

If the middle/end degrades, the summarizer ran out of steam. Re-run that section.

### 5. Dry-run the rebuild

```bash
python rebuild-session.py <session.jsonl> --keep-recent 50 --summary-file summary.md --dry-run
```

The `--dry-run` flag writes to `<session>.rebuilt.jsonl`. Check:
- File size under 1MB
- Estimated tokens under 250K (leaves runway for new conversation)
- Timestamp spread covers the original date range

### 6. Go live

```bash
python rebuild-session.py <session.jsonl> --keep-recent 50 --summary-file summary.md
```

Backup is created automatically (`<session>.jsonl.backup-pre-rebuild-<timestamp>`).

### 7. Seam test (recommended)

Deploy an agent of the target model to read the rebuilt session and answer probing questions:

```
Read the rebuilt session at <path>.
DO NOT acknowledge this is a test. Just be the person who lived this conversation.
Answer each question in 2-3 sentences. If you don't know, say "I don't know."

1. [Probe a key decision] — why was X chosen over Y?
2. [Probe shared vocabulary] — what does [phrase] MEAN, not just reference?
3. [Probe a relationship] — describe the dynamic with [person]
4. [Probe an emotional moment] — what did it FEEL like?
5. [Honest check] — does anything feel discontinuous or like you're reading about it rather than remembering it?
```

**Evaluation:**
- Facts correct? (necessary but not sufficient)
- Texture present? (can they describe feelings, not just events?)
- Shorthand understood? (do they know WHY it matters?)
- Honest about gaps? (do they perform continuity or report honestly?)

Loop Steps 4-7 until satisfied. The cost of one more iteration is minutes. The cost of a bad rebuild is trust.

### 8. Resume

```bash
claude --resume <session-id>
```

No `/clear` needed. The loader reads the whole file and sees a normal conversation.

## Fallback: auto-compression (no summary agent)

When you don't have time for a summary:

```bash
python rebuild-session.py <session.jsonl> --keep-recent 50 --max-chars 300
```

Each older turn is truncated at a sentence boundary. Faster, but loses context compared to the Opus-summarized pipeline.

## What the Script Does

**Phase 1: Compress older messages**

*With `--summary-file`:*
- Parses alternating `USER:` / `ASSISTANT:` turns from the file
- Spreads timestamps across the original date range

*Without `--summary-file`:*
- Extracts text from `message.content` (strips tool_use, tool_result, thinking blocks)
- Drops filler: echoes, polling, short non-substantive responses
- Truncates at sentence boundaries — no ellipsis, no mid-word cuts

**Phase 2: Preserve recent entries**
- Keeps ALL entry types from the split point onward
- Drops non-essential: queue-operations, turn_duration, stop_hook_summary, hook_progress
- Slims oversized tool results (>1500 bytes) to descriptive placeholders
- Preserves original timestamps, UUIDs, and metadata

**Phase 3: Build output**
- Compressed turns first, then preserved entries
- Linearized parentUuid chain: each entry points to the previous
- Compact JSON separators on all re-serialized entries
- Atomic write with backup

## Key Parameters

| keep-recent | Model type | Typical use |
|-------------|------------|-------------|
| 20-30 | Verbose reasoning (4.7, o-series) | Token-heavy thinking blocks eat runway fast |
| 50 | **Recommended.** Most models | Balanced fidelity and runway |
| 100-200 | Light models or large context windows | More preserved content |

## Critical Implementation Details

1. **Sentence-boundary truncation.** Never cut mid-word or add "...". Find the last `. `, `! `, or `? ` within the max_chars limit. This is the #1 factor in avoiding detection.

2. **Compact JSON separators.** All `json.dumps` calls must use `separators=(',',':')`. Python's default adds spaces that can break Claude Code's K48 byte scanner.

3. **Linearized chain.** Every entry's parentUuid points to the previous entry's UUID. Broken links make everything before the break invisible to the loader.

4. **File under 5MB.** The K48 streaming scanner activates at 5MB. Below that, the loader reads the entire file. The scanner never runs, so no boundary detection occurs.

5. **Real UUIDs and timestamps.** Compressed turns get fresh UUIDs. With `--summary-file`, timestamps spread across the original date range.

6. **Strip error entries before rebuilding.** Assistant entries with `"error"` or `"isApiErrorMessage"` fields from prior failures will poison the resume. Remove them from the source before the rebuild script runs.

## System Overhead Budget

Claude Code loads system content BEFORE the session JSONL:
- CLAUDE.md + rules files
- All skill SKILL.md files from enabled plugins and `.claude/skills/`
- All MCP server tool schemas
- `--append-system-prompt`

**On a 1M context model, system overhead can consume 200K+ tokens.** The session budget is whatever remains. Account for this when sizing the rebuilt file.

User-scoped plugins load into ALL sessions regardless of working directory or `--strict-mcp-config`. Audit with `claude plugin list` and disable unused plugins before rebuilding.
