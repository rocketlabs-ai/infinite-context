Run Smart Compact to extend this session beyond the context window.

Smart Compact inserts a compaction boundary matching Claude Code's native format, with a narrative summary of earlier conversation and preserved recent messages.

1. Find the current session's JSONL file. Session files are at `~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`. Use the most recently modified `.jsonl` in that directory.
2. Run dry-run first: `python3 smart-compact.py <path> --dry-run --keep-recent 500`
3. If the dry run looks good and integrity checks pass, run without `--dry-run` to compact in place (backup is created automatically).
4. Report the results: messages summarized/preserved, discovered tools, file size, estimated tokens on resume, integrity check results.
5. Remind the user to `/clear` and `claude --resume <session_id>` to reload the compacted session.

For best results, generate a narrative summary first:
1. `python3 smart-compact.py <path> --extract-conversation conversation.txt -k 500`
2. Summarize the extracted text (use Claude, edit manually, etc.)
3. `python3 smart-compact.py <path> -k 500 --summary-file summary.md`

Options:
- `-k, --keep-recent N` — recent messages to preserve (default: 500)
- `--summary-file <path>` — narrative summary file
- `--extract-conversation <path>` — extract conversation text and exit
- `-t, --threshold N` — byte threshold for slimming tool results (default: 1500)
- `--prune-thinking` — also slim large thinking blocks
