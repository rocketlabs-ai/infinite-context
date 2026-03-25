# Infinite Context

Claude Code sessions hit context limits. This fixes that.

Smart Compact extends your Claude Code sessions beyond the context window by surgically inserting a compaction boundary — the same mechanism Claude Code uses internally — with a narrative summary of everything before it. Your session resumes with full awareness of what happened, not a blank slate.

## How it works

Claude Code stores conversations as JSONL files. When a session gets too long, the built-in `/compact` command summarizes everything and draws a line — the session loader only reads messages after that line on resume. Smart Compact does the same thing, but gives you control:

1. **Compact boundary** — a marker that tells the session loader "start reading here." Format matches Claude Code's native output exactly (verified against the source).
2. **Narrative summary** — instead of a generic summary, you provide (or generate) a rich chronological narrative of what happened before the boundary. The model resumes with real context.
3. **Preserved messages** — the last N conversation messages are kept at full fidelity after the boundary. Tool results above a size threshold are slimmed with descriptive summaries.

The result: your 28MB session that was hitting context limits now loads ~800K tokens on resume — the narrative summary plus your recent conversation — and the model picks up exactly where you left off.

## Installation

```bash
git clone https://github.com/rocketlabs-ai/infinite-context.git
cd infinite-context
```

No dependencies beyond Python 3.12+ standard library. The script reads and writes JSONL files directly.

## Usage

### Quick start — compact your latest session

```bash
# Dry run first (writes to .compact.jsonl, doesn't modify original)
python smart-compact.py --dry-run

# Compact in place (creates timestamped backup automatically)
python smart-compact.py
```

### With a narrative summary (recommended)

The best results come from providing a narrative summary of the conversation before the boundary. Two-step workflow:

```bash
# Step 1: Extract conversation text (strips tool blocks, keeps dialogue)
python smart-compact.py --extract-conversation conversation.txt --keep-recent 500

# Step 2: Summarize conversation.txt using your preferred method
# (Claude, ChatGPT, manual editing — whatever captures the context)

# Step 3: Compact with the narrative summary
python smart-compact.py --keep-recent 500 --summary-file summary.md
```

### Specify a session file

```bash
python smart-compact.py path/to/session.jsonl --dry-run
```

Session files live at `~/.claude/projects/<project-hash>/<session-id>.jsonl`.

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `-k, --keep-recent N` | 500 | Number of recent messages to preserve at full fidelity |
| `--summary-file PATH` | — | Path to narrative summary text file |
| `--extract-conversation PATH` | — | Extract pre-split conversation text and exit |
| `-t, --threshold N` | 1500 | Byte threshold for slimming tool results |
| `--prune-thinking` | off | Also slim large thinking blocks |
| `--dry-run` | off | Write to `.compact.jsonl` instead of overwriting |
| `--no-backup` | off | Skip creating a backup |

### Choosing --keep-recent

The right value depends on your context window and how much room you need:

| keep-recent | Typical tokens on resume | Good for |
|-------------|------------------------|----------|
| 50 | ~35K | Minimal context, fast resume |
| 200 | ~200K | Light sessions |
| 500 | ~800K | **Recommended.** Rich context within 1M window |
| 1000 | ~1.3M | Heavy sessions, may exceed context window |

## How we built it

We reverse-engineered Claude Code's session loader by reading the bundled `cli.js` source (12.4MB, minified). The [deep trace](docs/claude-code-deep-trace.md) documents three code paths:

1. **Session resume** — how JSONL files are loaded, how `compact_boundary` markers are found via streaming byte-level scanning, how the `parentUuid` chain is traced to reconstruct conversation order.
2. **Compaction** — what triggers it, the exact JSON format of the boundary marker and summary message, what metadata fields are required.
3. **JSONL to API** — the 12+ transformation stages between raw JSONL and the messages array sent to Claude, including which fields are harness-only and which reach the API.

Smart Compact produces output that is structurally identical to Claude Code's native `/compact`. The session loader processes it the same way.

## Integrity checks

Every compaction run verifies:

- Compact boundary exists with correct format
- Summary message follows boundary with plain string content
- `compactMetadata` has all required fields (trigger, preTokens, preCompactDiscoveredTools, preservedSegment)
- `logicalParentUuid` links to the last pre-compact message
- Summary links to boundary via `parentUuid`
- First preserved message links to summary
- Tool use/result pairing is intact in preserved section
- Boundary `parentUuid` matches `logicalParentUuid`

## License

MIT
