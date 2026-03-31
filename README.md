# Infinite Context

Claude Code sessions hit context limits. This fixes that.

Two approaches. **Session Rebuild is preferred** — it produces a file the model cannot distinguish from a continuous conversation. Smart Compact is the alternative if you need compact_boundary compatibility.

---

## Approach A: Session Rebuild (preferred)

Rebuilds the session JSONL with compressed older turns written as real messages, then preserved recent turns at full fidelity. No `compact_boundary`, no framing text, no detectable seam.

**Why it beats compact_boundary:**
- No "This session is being continued..." framing text
- No `isCompactSummary: true` flag
- No grain change between polished summary and raw messages
- Three blind Opus agents couldn't find the seam — they thought they were reading the uncompacted original

The model sees one continuous conversation from start to finish.

### Best pipeline: Opus-summarized rebuild

1. **Deploy an Opus agent** to read the full JSONL and write a turn-by-turn summary:
   - Format: alternating `USER:` and `ASSISTANT:` lines
   - Preserve key decisions, shared shorthand, emotional texture
   - Compress operational noise (heartbeats, monitoring, repetitive tool calls)
   - Target: ~15-20K tokens covering all turns before the preserved section
   - Output to a file, e.g. `session-summary.md`

2. **Dry-run:**
   ```bash
   python rebuild-session.py <session.jsonl> --keep-recent 200 --summary-file session-summary.md --dry-run
   ```

3. **Verify:** Check estimated tokens (~200-250K target), timestamp spread, turn count.

4. **Go live** (backup is automatic):
   ```bash
   python rebuild-session.py <session.jsonl> --keep-recent 200 --summary-file session-summary.md
   ```

5. **Resume:**
   ```bash
   claude --resume <session-id>
   ```

No `/clear` needed. The loader reads the whole file and sees a normal conversation.

### Fallback: auto-compression (no summary agent)

```bash
# Truncates each old turn to max-chars. Faster but loses texture.
python rebuild-session.py <session.jsonl> --keep-recent 200 --max-chars 300 --dry-run
```

### rebuild-session.py options

| Flag | Default | Description |
|------|---------|-------------|
| `--keep-recent N` | 396 | Recent conversation messages to preserve at full fidelity |
| `--max-chars N` | 500 | Max chars per compressed turn (sentence-boundary truncation) |
| `--summary-file PATH` | — | Pre-written turn-by-turn summary (USER:/ASSISTANT: format) |
| `--dry-run` | off | Write to `.rebuilt.jsonl` instead of overwriting |

When `--summary-file` is provided, the script spreads timestamps across the original date range automatically — no manual timestamp fixing needed.

### Key parameters

| keep-recent | max-chars | Typical tokens | Good for |
|-------------|-----------|---------------|----------|
| 100 | 200 | ~250K | Minimal footprint |
| 200 | 300 | ~470K | **Recommended.** Rich context within 500K target |
| 400 | 300 | ~600K | Heavy sessions approaching 1M |
| 200 | 500 | ~600K | More detail in older turns |

---

## Approach B: Smart Compact

Inserts a `compact_boundary` marker matching Claude Code's native format, with a narrative summary and preserved recent messages. The model knows compaction occurred — framing text is visible — but resumes with full context.

Smart Compact is the right choice if:
- You want compact_boundary compatibility
- You're using the `/project:smart-compact` slash command (no API key required)
- You prefer the simpler single-script pipeline

### Installation

```bash
git clone https://github.com/rocketlabs-ai/infinite-context.git
cd infinite-context
```

No dependencies beyond Python 3.12+ standard library.

For auto-summarization, the script calls any OpenAI-compatible API via stdlib `urllib` — no SDK needed. Works with Ollama (local, free) out of the box.

### Option A: Slash command (Max/Pro users — no API key needed)

```bash
cp -r .claude/commands/ /path/to/your/project/.claude/commands/
```

Then in any Claude Code session:
```
/project:smart-compact
```

The slash command orchestrates the full pipeline using the session's own model.

### Option B: Auto-summarize via Ollama (local, free)

```bash
ollama pull qwen3:8b
ollama serve

python smart-compact.py --auto-summarize --keep-recent 500
```

### Option C: Auto-summarize via remote API

```bash
python smart-compact.py --auto-summarize \
  --base-url https://openrouter.ai/api/v1 \
  --api-key sk-or-... \
  --model anthropic/claude-haiku -k 500
```

### Option D: Manual pipeline (full control)

```bash
# Step 1: Extract conversation text
python smart-compact.py --extract-conversation conversation.txt -k 500

# Step 2: Summarize using your preferred method

# Step 3: Compact with the narrative summary
python smart-compact.py -k 500 --summary-file summary.md
```

### Option E: Quick compact (no summary)

```bash
python smart-compact.py --dry-run
python smart-compact.py
```

### smart-compact.py options

| Flag | Default | Description |
|------|---------|-------------|
| `-k, --keep-recent N` | 500 | Recent messages to preserve at full fidelity |
| `--auto-summarize` | off | Generate narrative summary via LLM API |
| `--model MODEL` | qwen3:8b | Model for `--auto-summarize` |
| `--base-url URL` | http://localhost:11434/v1 | API endpoint |
| `--api-key KEY` | — | API key for remote endpoints |
| `--summary-file PATH` | — | Path to narrative summary text file |
| `--extract-conversation PATH` | — | Extract pre-split conversation text and exit |
| `-t, --threshold N` | 1500 | Byte threshold for slimming tool results |
| `--prune-thinking` | off | Also slim large thinking blocks |
| `--dry-run` | off | Write to `.compact.jsonl` instead of overwriting |
| `--no-backup` | off | Skip creating a backup |

### Choosing --keep-recent (Smart Compact)

| keep-recent | Typical tokens on resume | Good for |
|-------------|------------------------|----------|
| 50 | ~35K | Minimal context, fast resume |
| 200 | ~200K | Light sessions |
| 500 | ~800K | **Recommended.** Rich context within 1M window |
| 1000 | ~1.3M | Heavy sessions, may exceed context window |

---

## How we built it

We reverse-engineered Claude Code's session loader by reading the bundled `cli.js` source (12.4MB, minified). The [deep trace](docs/claude-code-deep-trace.md) documents three code paths:

1. **Session resume** — how JSONL files are loaded, how `compact_boundary` markers are found via streaming byte-level scanning, how the `parentUuid` chain is traced to reconstruct conversation order.
2. **Compaction** — what triggers it, the exact JSON format of the boundary marker and summary message, what metadata fields are required.
3. **JSONL to API** — the 12+ transformation stages between raw JSONL and the messages array sent to Claude, including which fields are harness-only and which reach the API.

Both scripts produce output that correctly handles Claude Code's session loading requirements. The K48 byte scanner only activates at files over 5MB — rebuild targets keep files well under that threshold by design.

## License

MIT
