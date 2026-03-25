Run Smart Compact to extend this session beyond the context window.

Smart Compact inserts a compaction boundary matching Claude Code's native format, with a narrative summary of earlier conversation and preserved recent messages. This command orchestrates the full pipeline without external dependencies.

Follow these steps:

1. Find the current session's JSONL file. Session files are at `~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`. Use the most recently modified `.jsonl` in that directory (skip any in `subagents/` subdirectories).

2. Run the extraction step to get conversation text stripped of tool blocks:
   ```
   python3 smart-compact.py <path> --extract-conversation /tmp/sc-conversation.txt --keep-recent 500
   ```

3. Read the extracted conversation file at `/tmp/sc-conversation.txt`.

4. Write a chronological narrative summary to `/tmp/sc-summary.md`. The summary should:
   - Maintain chronological order — this is a story that unfolds over time
   - Preserve who said what, when decisions were made, when direction changed
   - Keep relationship dynamics, tone shifts, and key agreements
   - Preserve technical decisions, project milestones, and pivots
   - Remove tool execution details but reference what was accomplished
   - Keep all names, project names, and specific terms
   - Be roughly 10-15% of the original conversation length
   - End with open threads and unresolved items

5. Run the compaction with the narrative summary:
   ```
   python3 smart-compact.py <path> --keep-recent 500 --summary-file /tmp/sc-summary.md --dry-run
   ```

6. If the dry run passes all integrity checks, run without `--dry-run`:
   ```
   python3 smart-compact.py <path> --keep-recent 500 --summary-file /tmp/sc-summary.md
   ```

7. Report the results: messages summarized/preserved, discovered tools, estimated tokens on resume, integrity check results.

8. Tell the user to run `/clear` then `claude --resume <session_id>` to reload the compacted session.
