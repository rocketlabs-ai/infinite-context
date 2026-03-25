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

No dependencies beyond Python 3.12+ standard library. Zero `pip install` required.

For auto-summarization, the script calls any OpenAI-compatible API via stdlib `urllib` — no SDK needed. Works with Ollama (local, free) out of the box, or any remote API with `--base-url` and `--api-key`.

## Usage

### Option A: Slash command (Max/Pro users — no API key needed)

If you're running Claude Code, install the slash command:

```bash
# Copy into your project
cp -r .claude/commands/ /path/to/your/project/.claude/commands/
```

Then in any Claude Code session:
```
/project:smart-compact
```

The slash command orchestrates the full pipeline using the session's own model — no API key required. It extracts the conversation, writes a narrative summary, and compacts.

### Option B: Auto-summarize via Ollama (local, free)

```bash
# Make sure Ollama is running with a model pulled
ollama pull qwen3:8b
ollama serve

# One command — extracts, summarizes, compacts
python smart-compact.py --auto-summarize --keep-recent 500

# Use a different local model
python smart-compact.py --auto-summarize --model llama3.1:8b -k 500
```

Default model: `qwen3:8b`. No API key needed.

### Option C: Auto-summarize via remote API

```bash
# Any OpenAI-compatible endpoint (OpenRouter, Anthropic, Together, etc)
python smart-compact.py --auto-summarize \
  --base-url https://openrouter.ai/api/v1 \
  --api-key sk-or-... \
  --model anthropic/claude-haiku -k 500

# Or set the key via environment variable
export SMART_COMPACT_API_KEY=sk-...
python smart-compact.py --auto-summarize --base-url https://api.anthropic.com/v1 --model claude-haiku-4-5-20251001 -k 500
```

### Option D: Manual pipeline (full control)

```bash
# Step 1: Extract conversation text (strips tool blocks, keeps dialogue)
python smart-compact.py --extract-conversation conversation.txt -k 500

# Step 2: Summarize conversation.txt using your preferred method
# (Claude, ChatGPT, manual editing — whatever captures the context)

# Step 3: Compact with the narrative summary
python smart-compact.py -k 500 --summary-file summary.md
```

### Option E: Quick compact (no summary)

```bash
# Dry run first
python smart-compact.py --dry-run

# Compact in place (creates timestamped backup automatically)
python smart-compact.py
```

Works without a summary file — uses a generic placeholder. Better than hitting context limits, but options A-D produce better results.

### Specify a session file

```bash
python smart-compact.py path/to/session.jsonl --dry-run
```

Session files live at `~/.claude/projects/<project-hash>/<session-id>.jsonl`.

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `-k, --keep-recent N` | 500 | Number of recent messages to preserve at full fidelity |
| `--auto-summarize` | off | Generate narrative summary via LLM API |
| `--model MODEL` | qwen3:8b | Model for `--auto-summarize` |
| `--base-url URL` | http://localhost:11434/v1 | API endpoint (Ollama default) |
| `--api-key KEY` | — | API key for remote endpoints |
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
