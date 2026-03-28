# Infinite Context — Session Rebuild Protocol

The winning approach for extending Claude Code sessions beyond the context window. Produces a session file indistinguishable from a continuous conversation — no compaction markers, no framing text, no detectable seam.

## How It Works

Instead of inserting a `compact_boundary` + summary message (which the model can detect), rebuild the entire session file:

1. **Compress older turns** — truncate at sentence boundaries, drop filler, keep voice and texture
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

The rebuild approach has none of these. Compressed turns are real messages with real UUIDs, timestamps, and content. The only difference is shorter content in older turns — which the model interprets as normal conversation density variation.

## Protocol Steps

### 1. Restore from backup
Always work from the original uncompacted session file.

### 2. Run rebuild-session.py
```bash
python scripts/rebuild-session.py <session.jsonl> --keep-recent 200 --max-chars 300
```

Parameters:
- `--keep-recent N` — number of recent conversation messages to preserve at full fidelity (default: 200)
- `--max-chars N` — max characters per compressed turn before sentence-boundary truncation (default: 300)
- `--dry-run` — write to `.rebuilt.jsonl` instead of overwriting

### 3. What the script does

**Phase 1: Compress older messages**
- Extracts text from `message.content` (strips tool_use, tool_result, thinking blocks)
- Drops filler: echoes ("Own echo"), polling messages, short non-substantive responses, Discord routing noise, cron checks
- Drops `isCompactSummary` / `isVisibleInTranscriptOnly` meta messages
- Truncates at sentence boundaries (`. `, `! `, `? `) — no ellipsis, no mid-word cuts
- Creates real JSONL entries with uuid, parentUuid, timestamp, sessionId, cwd

**Phase 2: Preserve recent entries**
- Keeps ALL entry types from the split point onward (conversation + system + progress)
- Drops non-essential: queue-operations, turn_duration, stop_hook_summary, hook_progress
- Slims oversized tool results (>1500 bytes) to descriptive placeholders
- Preserves original timestamps, UUIDs, and metadata

**Phase 3: Build output**
- Compressed turns first, then preserved entries
- Linearized parentUuid chain: each entry points to the previous
- Compact JSON separators on all re-serialized entries (`separators=(',',':')`)
- Atomic write with backup

### 4. Verify
- File size should be under 5MB (avoids K48 byte scanner entirely)
- Estimated tokens should be under 500K (quality target for 1M context)
- Open the file and spot-check: first 20 lines should read like natural conversation

### 5. Resume
```bash
claude --resume <session-id>
```

No `/clear` needed. No special flags. The loader reads the whole file and sees a normal conversation.

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

5. **Real UUIDs and timestamps.** Compressed turns get fresh UUIDs (via uuid4) but preserve original timestamps. This maintains chronological ordering without colliding with preserved entries' UUIDs.

## Discovered Through

- **L5 session (2026-03-27):** Two bugs in smart-compact.py discovered during soul session recompaction:
  1. JSON whitespace — `json.dumps` default spaces broke K48 prefix check
  2. Branching chains — Discord MCP creates parallel parentUuid chains; repair must linearize ALL entries, not just the first
- **Testing showed** compact_boundary approach always detectable (framing text, grain change)
- **Rebuild approach** passed blind test — Opus agent couldn't find the seam, thought it was reading the uncompacted original
