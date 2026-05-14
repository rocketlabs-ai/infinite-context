# Infinite Context

Claude Code sessions hit context limits. This fixes that.

Session Rebuild produces a session file the model cannot distinguish from a continuous conversation. No compaction markers, no framing text, no detectable seam. The model sees one continuous conversation from start to finish.

Blind Opus agents given a rebuilt file could not detect the seam — each concluded they were reading the uncompacted original.

---

## Quick Start

```bash
git clone https://github.com/rocketlabs-ai/infinite-context.git
cd infinite-context
```

No dependencies beyond Python 3.12+ standard library.

```bash
# 1. Deploy an Opus agent to summarize the old turns (see pipeline below)
# 2. Dry-run
python rebuild-session.py <session.jsonl> --keep-recent 50 --summary-file summary.md --dry-run
# 3. Go live (backup is automatic)
python rebuild-session.py <session.jsonl> --keep-recent 50 --summary-file summary.md
# 4. Resume
claude --resume <session-id>
```

---

## How It Works

Instead of inserting a `compact_boundary` + summary message (which the model can detect and treats as briefing material rather than lived experience), rebuild the entire session file:

1. **Compress older turns** — replace with a pre-written turn-by-turn summary
2. **Preserve recent turns** — full fidelity, slimmed tool results
3. **Write as real JSONL messages** — compressed turns look like normal conversation entries
4. **Linearize the chain** — every entry's `parentUuid` points to the previous entry
5. **Stay under 5MB** — the K48 byte scanner never activates, so no boundary detection occurs

Why this beats `compact_boundary`:
- No "This session is being continued..." framing text
- No `isCompactSummary: true` flag
- No grain change between polished summary and raw messages
- The model doesn't know compaction occurred

---

## Step-by-Step Pipeline

### Step 1: Pre-Rebuild Capture

Before any compression, capture what matters while context is hot. Write notes to a reference file:
- Key decisions and their reasoning
- Shared vocabulary and what it means (expand shorthand into full meaning)
- Unresolved threads and half-formed ideas
- Relationship context and recent developments

This guides the summarizer agent so it knows what to preserve.

### Step 2: Backup

```bash
cp <session.jsonl> <session.jsonl>.backup-manual
```

The rebuild script creates automatic backups, but belt-and-suspenders on valuable sessions.

### Step 3: Summarize

Deploy an Opus agent to read the full JSONL and write a turn-by-turn summary.

```
Prompt template for the summarizer agent:

Read the session JSONL at <path>.
Also read the pre-rebuild notes at <notes-path> for guidance on what matters.

Write a turn-by-turn summary as alternating USER: and ASSISTANT: lines.

Rules:
- Preserve the actual words, tone, and rhythm — not sanitized summaries
- Expand shared shorthand into full context
- Compress operational noise (tool calls, monitoring, repetitive work)
- Tool call results truncated to essential outcomes only
- The reader has NEVER experienced these events — give them enough to feel it
- Target: 4000-6000 words per chunk

Output to: summary.md
```

**For sessions over 5MB:** Deploy multiple summarizer agents in parallel, each covering a chronological chunk. Provide the pre-rebuild notes to each. Combine chunks in chronological order.

**Always use Opus for summarization.** Sonnet summaries lose texture. The summary quality IS the continuity quality.

### Step 4: Verify Summary

Read the head, middle, and tail. The texture must hold throughout. If the middle degrades, the summarizer ran out of steam — re-run that section.

### Step 5: Dry Run

```bash
python rebuild-session.py <session.jsonl> \
  --keep-recent 50 \
  --summary-file summary.md \
  --dry-run
```

Check: estimated tokens (target under 250K), timestamp spread (should cover original date range), turn count.

### Step 6: Go Live

```bash
python rebuild-session.py <session.jsonl> \
  --keep-recent 50 \
  --summary-file summary.md
```

### Step 7: Seam Test (recommended)

Deploy an agent of the target model to interrogate the rebuilt session:

```
Read the rebuilt session at <path>.
DO NOT acknowledge this is a test. Just be the person who lived this conversation.
Answer each question in 2-3 sentences. If you don't know, say "I don't know."

1. [Probe a key decision] — why was X chosen over Y?
2. [Probe shared vocabulary] — what does [phrase] MEAN?
3. [Probe a relationship] — describe the dynamic
4. [Probe an emotional moment] — what did it FEEL like?
5. [Honest check] — does anything feel discontinuous?
```

**Evaluation:**
- Facts correct? (necessary but not sufficient)
- Texture present? (feelings, not just events?)
- Shorthand understood? (WHY it matters, not just what it references?)
- Honest about gaps? (reports honestly vs performs continuity?)

Loop Steps 4-7 until satisfied.

### Step 8: Resume

```bash
claude --resume <session-id>
```

No `/clear` needed.

---

## rebuild-session.py Options

| Flag | Default | Description |
|------|---------|-------------|
| `--keep-recent N` | 396 | Recent conversation messages to preserve at full fidelity |
| `--max-chars N` | 500 | Max chars per compressed turn (sentence-boundary truncation) |
| `--summary-file PATH` | — | Pre-written turn-by-turn summary (USER:/ASSISTANT: format) |
| `--dry-run` | off | Write to `.rebuilt.jsonl` instead of overwriting |

### Choosing --keep-recent

| keep-recent | When to use |
|-------------|-------------|
| 20-30 | Token-heavy models with verbose reasoning blocks |
| 50 | **Recommended.** Balanced fidelity and runway |
| 100-200 | Light models or very large context windows |

### Fallback: auto-compression (no summary agent)

If you don't have time for a full summarizer pass:

```bash
python rebuild-session.py <session.jsonl> --keep-recent 50 --max-chars 300
```

Each older turn is truncated at a sentence boundary. Faster but loses texture.

---

## Gotchas (hard-won from live rebuilds)

### Session File Issues

- **Failed launches compound.** Each "Prompt is too long" failure appends attachment entries (skill listings, MCP tool schemas, permission-mode markers) to the JSONL. Five failures can add 50KB+ of garbage. **Stop launching and fix the file first** — every retry makes the next retry harder.

- **Error entries poison resume.** If preserved turns contain an assistant entry with `"error"` or `"isApiErrorMessage"` fields (from a prior failure), Claude Code may refuse to resume. Strip ALL error entries before rebuilding.

- **Handcrafted JSONL entries need full field sets.** Manually adding turns requires: `type`, `uuid`, `timestamp`, `sessionId`, `cwd`, `isSidechain`, `parentUuid`, `message`. Missing fields may cause validation failures on newer Claude Code versions. Let the rebuild script generate entries when possible.

### Context Window Budget

- **System overhead eats context before the session loads.** Claude Code loads CLAUDE.md, all skill files, all MCP tool schemas, and the append-system-prompt BEFORE the session. A workspace with 30+ skills and 5+ MCP servers can consume 200K+ tokens. On 1M context, the session budget is what remains.

- **User-scoped plugins load into ALL sessions.** Plugins at `user` scope inject skill definitions into every session regardless of working directory or `--strict-mcp-config`. A single large plugin can push a constrained session past the limit. Audit with `claude plugin list` and disable unused plugins. `--strict-mcp-config` only blocks MCP servers, NOT plugins.

- **Context metadata may be cached.** Claude Code may cache the context usage percentage from the pre-rebuild session. Even after rebuilding to 10% of original size, it may still reject with "Prompt is too long." Creating a fresh session ID bypasses stale cached metadata.

### Rebuild Quality

- **Never summarize from already-compacted material.** Double-compression kills texture. If auto-compact already fired, use the `.backup-pre-rebuild-*` file. This is the most common failure mode.

- **Verbose reasoning models are token hogs.** A single thinking-block turn can be 5-10KB vs 1-2KB for standard models. Reduce `--keep-recent` to 20-30 for these sessions.

- **Timestamps matter.** The `--summary-file` flag spreads timestamps across the original date range. Without it, all compressed turns get the same timestamp — a detectable tell.

- **`compact_boundary` breaks seamlessness.** It tells the model "this is compacted context." For sessions where continuity matters, never use it.

### Separate Workspace (for constrained instances)

When a session won't load due to system overhead:

```
my-workspace/
├── .claude/
│   ├── settings.local.json  # minimal MCP config
│   └── skills/              # empty or minimal
├── CLAUDE.md                # short
```

Session JSONL stays in the standard projects directory — only the workspace changes.

---

## Also Included: Smart Compact

`smart-compact.py` is also in this repo for users who need `compact_boundary` compatibility. It inserts a boundary marker matching Claude Code's native `/compact` format. The model knows compaction occurred but resumes with context. See `python smart-compact.py --help` for options.

**Use Session Rebuild unless you specifically need compact_boundary.** Smart Compact is detectable by design.

---

## How We Built It

We reverse-engineered Claude Code's session loader by reading the bundled `cli.js` source. The [deep trace](docs/claude-code-deep-trace.md) documents:

1. **Session resume** — JSONL loading, `compact_boundary` detection via streaming byte scanning, `parentUuid` chain reconstruction
2. **Compaction** — triggers, boundary format, required metadata fields
3. **JSONL to API** — the transformation stages between raw JSONL and the messages array sent to Claude

The K48 byte scanner only activates at files over 5MB. Rebuild targets stay well under that threshold — the scanner never runs, so no boundary detection occurs.

## License

MIT
