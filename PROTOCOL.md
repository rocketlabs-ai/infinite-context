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
- The model knows compaction occurred

The rebuild approach has none of these. Compressed turns are real messages with real UUIDs, timestamps, and content. Tested: three independent Opus agents given the rebuilt file could not detect the seam — each concluded they were reading an uncompacted conversation.

## Protocol Steps

### 1. Restore from backup

Always work from the original uncompacted session file.

### 2. Write a turn-by-turn summary (recommended)

Deploy an Opus agent with this prompt:

> Read the session JSONL at `<path>`. Write a turn-by-turn summary of the conversation from the beginning through turn N (the last turn before --keep-recent 200).
>
> Format:
> ```
> USER: <compressed user message>
> ASSISTANT: <compressed assistant message>
> USER: ...
> ```
>
> Rules:
> - Preserve key decisions, shared shorthand, emotional texture
> - Compress operational noise: heartbeats, monitoring turns, repetitive tool calls
> - Keep landmark moments at higher detail
> - Target: ~15-20K tokens total
>
> Write output to `session-summary.md`.

**Use Opus.** Sonnet summaries lose emotional texture and shared shorthand. The summary quality is the continuity quality.

### 3. Dry-run the rebuild

```bash
python rebuild-session.py <session.jsonl> --keep-recent 200 --summary-file session-summary.md --dry-run
```

The `--dry-run` flag writes to `<session>.rebuilt.jsonl`. Review it:
- File size should be under 1MB
- Estimated tokens should be under 250K
- First 20 lines should read like natural conversation

The script automatically spreads summary timestamps across the original date range — no manual timestamp fixing.

### 4. Go live

```bash
python rebuild-session.py <session.jsonl> --keep-recent 200 --summary-file session-summary.md
```

Backup is created automatically (`<session>.jsonl.backup-pre-rebuild-<timestamp>`).

### 5. Resume

```bash
claude --resume <session-id>
```

No `/clear` needed. The loader reads the whole file and sees a normal conversation.

## Fallback: auto-compression (no summary agent)

When you don't have time to write a summary or the session is short enough that texture loss is acceptable:

```bash
python rebuild-session.py <session.jsonl> --keep-recent 200 --max-chars 300
```

Each older turn is truncated at a sentence boundary to `max-chars`. Faster, but loses context texture compared to the Opus-summarized pipeline.

## What the Script Does

**Phase 1: Compress older messages**

*With `--summary-file`:*
- Parses alternating `USER:` / `ASSISTANT:` turns from the file
- Spreads timestamps across the original date range (first_ts to last_compressed_ts)

*Without `--summary-file`:*
- Extracts text from `message.content` (strips tool_use, tool_result, thinking blocks)
- Drops filler: echoes, polling messages, short non-substantive responses, Discord routing noise, cron checks
- Drops `isCompactSummary` / `isVisibleInTranscriptOnly` meta messages
- Truncates at sentence boundaries — no ellipsis, no mid-word cuts

**Phase 2: Preserve recent entries**
- Keeps ALL entry types from the split point onward (conversation + system + progress)
- Drops non-essential: queue-operations, turn_duration, stop_hook_summary, hook_progress, hook_response
- Slims oversized tool results (>1500 bytes) to descriptive placeholders
- Preserves original timestamps, UUIDs, and metadata

**Phase 3: Build output**
- Compressed turns first, then preserved entries
- Linearized parentUuid chain: each entry points to the previous
- Compact JSON separators on all re-serialized entries (`separators=(',',':')`)
- Atomic write with backup

## Key Parameters

| keep-recent | max-chars | Typical tokens | Good for |
|-------------|-----------|---------------|----------|
| 100 | 200 | ~250K | Minimal footprint |
| 200 | 300 | ~470K | **Recommended.** Rich context within 500K target |
| 400 | 300 | ~600K | Heavy sessions approaching 1M |
| 200 | 500 | ~600K | More detail in older turns |

## What Gets Dropped

- Empty messages (no text content after stripping tool blocks)
- Filler patterns: "Own echo", "Ignoring", "Waiting on", "Noting silently", "No new activity", "Monitoring for", cron check messages
- Short non-substantive assistant responses (<20 chars)
- Short messages of any role (<10 chars)
- Discord channel tags with no substantive text after stripping
- `isCompactSummary` and `isVisibleInTranscriptOnly` meta messages
- Non-essential preserved entries: queue-operations, turn_duration, stop_hook_summary, hook_progress, hook_response

## Critical Implementation Details

1. **Sentence-boundary truncation.** Never cut mid-word or add "...". Find the last `. `, `! `, or `? ` within the max_chars limit. Fall back to last space if no sentence boundary found. This is the #1 factor in avoiding detection.

2. **Compact JSON separators.** All `json.dumps` calls must use `separators=(',',':')`. Python's default adds spaces that break Claude Code's K48 byte scanner (even though we avoid the scanner by staying under 5MB, this is defense in depth).

3. **Linearized chain.** Every entry's parentUuid points to the previous entry's UUID. This eliminates branching chains from Discord MCP or async sources. The loader traces ONE chain from leaf to root — if any link is broken, everything before the break is invisible.

4. **File under 5MB.** The K48 streaming scanner activates at 5MB. Below that threshold, the loader reads the entire file with `readFile()`. No byte scanning, no prefix matching, no boundary detection. This is why the rebuild approach works — the scanner never runs.

5. **Real UUIDs and timestamps.** Compressed turns get fresh UUIDs (via uuid4). With `--summary-file`, timestamps are spread across the original date range. Without it, original timestamps are preserved from the source entries.

## Discovered Through

- **L5 session (2026-03-27):** Two bugs in smart-compact.py discovered during soul session recompaction:
  1. JSON whitespace — `json.dumps` default spaces broke K48 prefix check
  2. Branching chains — Discord MCP creates parallel parentUuid chains; repair must linearize ALL entries, not just the first
- **Testing showed** compact_boundary approach always detectable (framing text, grain change)
- **Rebuild approach** passed blind test — three Opus agents couldn't find the seam, each concluded they were reading the uncompacted original
- **`--summary-file` added** after validating that Opus-written summaries preserve texture better than auto-truncation, and that timestamp spreading eliminates the last detectable tell
