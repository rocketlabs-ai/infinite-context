# Claude Code Deep Trace -- JSONL Session Engine

**Version analyzed:** `@anthropic-ai/claude-code` v0.2.65 (installed 2026-03-20)
**Source:** Single bundled `cli.js` (12.4MB, 15,728 lines, minified)
**Methodology:** Byte-offset extraction from bundled binary, function-name tracing

---

## 1. Repo Structure

The open-source repo at `github.com/anthropics/claude-code` is **not the source code**. It contains:
- Shell scripts (47%), Python (29.3%), TypeScript (17.7% -- GitHub Actions/issue mgmt only)
- `plugins/` directory, `.claude/commands/`, examples
- No `src/` directory. No application TypeScript.

The actual runtime is distributed as a single bundled file:
```
node_modules/@anthropic-ai/claude-code/
  cli.js          -- 12.4MB bundled application (entry point)
  resvg.wasm      -- SVG rendering
  sdk-tools.d.ts  -- SDK type definitions
  vendor/         -- vendored dependencies
  package.json    -- bin: {"claude": "cli.js"}
```

All code references below are from `cli.js` with byte offsets noted for verification.

---

## 2. Path 1: Session Resume

### 2.1 CLI Argument Handling

The `--resume` / `-r` flag (and `--continue` / `-c`) are parsed by the CLI framework. When a session ID is provided, it resolves to a JSONL file path.

### 2.2 Session File Path

**Key functions:**

- **`E8()`** -- returns current session ID (a UUID)
- **`WY()`** -- returns current session JSONL path: `join(projectStorageDir, sessionId + ".jsonl")`
- **`Gv(sessionId)`** (byte ~10898900) -- resolves any session ID to its JSONL path:
  ```js
  function Gv(A) {
    if (A === E8()) return WY();
    let q = IO(l1());   // project storage dir
    return DN(q, `${A}.jsonl`);
  }
  ```
- **`l1()`** -- project identifier (hash of cwd path)
- **`IO()`** -- base storage directory (platform-dependent, e.g., `~/.claude/projects/<hash>/`)

Session files live at: `~/.claude/projects/<project-hash>/<session-id>.jsonl`
Subagent files live at: `~/.claude/projects/<project-hash>/<session-id>/subagents/agent-<agent-id>.jsonl`

### 2.3 Master Session Loader: `G26` (byte ~10926100)

This is the **single most important function** in the session engine. It:

1. Reads the JSONL file
2. Optionally skips pre-boundary content (performance optimization)
3. Parses all entries
4. Builds a UUID-keyed message map
5. Extracts metadata (summaries, titles, tags, etc.)
6. Identifies leaf nodes via parentUuid chain

**Full decompiled logic:**

```js
async function G26(filePath, options) {
  let messages = new Map();       // uuid -> message
  let summaries = new Map();      // leafUuid -> summary text
  let customTitles = new Map();   // sessionId -> title
  let tags = new Map();           // sessionId -> tag
  let agentNames = new Map();
  let agentColors = new Map();
  let agentSettings = new Map();
  let prNumbers = new Map();
  let prUrls = new Map();
  let prRepositories = new Map();
  let modes = new Map();
  let worktreeStates = new Map();
  let fileHistorySnapshots = new Map();
  let attributionSnapshots = new Map();
  let contentReplacements = new Map();
  let agentContentReplacements = new Map();
  let contextCollapseCommits = [];
  let contextCollapseSnapshot;

  let postBoundaryBuf = null;
  let preBoundaryMeta = null;
  let hasPreservedSegment = false;

  // STEP 1: If file > 5MB and DISABLE_PRECOMPACT_SKIP not set,
  //         use K48 streaming scanner to skip pre-boundary content
  if (!env.CLAUDE_CODE_DISABLE_PRECOMPACT_SKIP) {
    let { size } = await stat(filePath);
    if (size > 5242880) {  // Sr8 = 5MB threshold
      let result = await K48(filePath, size);
      postBoundaryBuf = result.postBoundaryBuf;
      hasPreservedSegment = result.hasPreservedSegment;
      if (result.boundaryStartOffset > 0) {
        preBoundaryMeta = await ZHY(filePath, result.boundaryStartOffset);
      }
    }
  }

  // STEP 2: If K48 didn't find a boundary, read entire file
  postBoundaryBuf ??= await readFile(filePath);

  // STEP 3: Leaf pruning for large buffers (>5MB, no preserved segment)
  if (!options?.keepAllLeaves && !hasPreservedSegment
      && !env.CLAUDE_CODE_DISABLE_PRECOMPACT_SKIP
      && postBoundaryBuf.length > 5242880) {
    postBoundaryBuf = vHY(postBoundaryBuf);  // prune non-main-chain leaves
  }

  // STEP 4: Parse pre-boundary metadata (summaries, titles, etc.)
  if (preBoundaryMeta?.length > 0) {
    let entries = mu(Buffer.from(preBoundaryMeta.join('\n')));
    for (let entry of entries) {
      // Only metadata types extracted (summary, custom-title, tag, etc.)
      // NO message content from before the boundary
    }
  }

  // STEP 5: Parse post-boundary messages
  let entries = mu(postBoundaryBuf);
  for (let entry of entries) {
    if (mi(entry)) {  // is it a message? (user|assistant|attachment|system|progress)
      // Skip certain progress types
      // Clear normalizedMessages from progress data (memory optimization)
      messages.set(entry.uuid, entry);
      if (of(entry)) {  // is it a compact_boundary?
        contextCollapseCommits.length = 0;  // reset collapse tracking
      }
    } else {
      // Route to metadata maps (summary, custom-title, tag, etc.)
    }
  }

  // STEP 6: Repair preserved segment parentUuid chain
  wHY(messages);

  // STEP 7: Find leaf UUIDs (messages with no children)
  let allMessages = [...messages.values()];
  let parentUuids = new Set(allMessages.map(m => m.parentUuid).filter(Boolean));
  let leaves = allMessages.filter(m => !parentUuids.has(m.uuid));

  // STEP 8: Walk leaves back to find "user/assistant" leaf UUIDs
  // (detecting cycles along the way)
  let leafUuids = new Set();
  for (let leaf of leaves) {
    let visited = new Set();
    let current = leaf;
    while (current) {
      if (visited.has(current.uuid)) break; // cycle detection!
      visited.add(current.uuid);
      if (current.type === 'user' || current.type === 'assistant') {
        leafUuids.add(current.uuid);
        break;
      }
      current = current.parentUuid ? messages.get(current.parentUuid) : undefined;
    }
  }

  return { messages, summaries, customTitles, tags, ..., leafUuids };
}
```

### 2.4 Streaming JSONL Scanner: `K48` (byte ~812045)

K48 performs a **streaming binary scan** of the JSONL file to find the last `compact_boundary` entry without parsing every line. This is critical for performance on large session files.

**Algorithm:**
1. Allocates a read buffer (1MB chunks, `BJK = 1048576`)
2. Creates a search needle: `Buffer.from('"compact_boundary"')` via `pJK()`
3. Reads file in chunks, scanning for the needle byte-by-byte
4. When `"compact_boundary"` is found in a line, parses ONLY that line via `iXA()` to verify `type === "system" && subtype === "compact_boundary"`
5. When a valid boundary is found, discards all content BEFORE it (resets output buffer `out.len = 0`)
6. Records `boundaryStartOffset` (byte position in file)
7. Checks for `preservedSegment` in `compactMetadata`
8. Collects the "last snapshot" (last `attribution-snapshot` line) -- the final attribution-snapshot entry is always appended to the output regardless of boundary position
9. Returns `{ boundaryStartOffset, postBoundaryBuf, hasPreservedSegment }`

**Key detail:** K48 does NOT JSON.parse every line. It uses fast byte-level `Buffer.indexOf()` to find potential boundaries, then only parses those specific lines. This makes it O(n) in file size but fast.

**What `q48` is:** `Buffer.from('{"type":"attribution-snapshot"')` -- used to detect attribution-snapshot entries. The last one is preserved in the output even if it appears before the boundary.

**What `FJK` is:** `Buffer.from('{"type":"system"')` -- fast prefix check before attempting compact_boundary detection. Only lines starting with `{"type":"system"` that also contain the `"compact_boundary"` needle are JSON-parsed.

**What `UJK` is:** `Buffer.from([10])` -- a single newline byte, used for output formatting.

**Pre-boundary metadata types scanned by `ZHY()`:**
The `PHY` array defines which entry types are extracted from the pre-boundary portion:
- `"type":"summary"` -- leaf summaries
- `"type":"custom-title"` -- session titles
- `"type":"tag"` -- session tags
- `"type":"agent-name"` -- agent display names
- `"type":"agent-color"` -- agent colors
- `"type":"agent-setting"` -- agent configurations
- `"type":"mode"` -- session modes
- `"type":"worktree-state"` -- worktree sessions
- `"type":"pr-link"` -- PR link metadata

### 2.5 JSONL Parser: `mu` (byte ~101 area)

Three implementations based on runtime:
- **`NJK`** -- Bun's native `JSONL.parseChunk()` (fastest)
- **`VJK`** -- Buffer-based: splits on `\n` (byte 10), UTF-8 decodes each line, `JSON.parse()`
- **`EJK`** -- String-based: splits on `\n`, trims, `JSON.parse()`

All silently skip lines that fail to parse (no errors thrown).

### 2.6 ParentUuid Chain Tracer: `Vs6` (byte ~10914302)

Given a message map and a leaf message, builds the ordered conversation chain:

```js
function Vs6(messages, leaf) {
  let chain = [];
  let visited = new Set();
  let current = leaf;
  while (current) {
    if (visited.has(current.uuid)) {
      // CYCLE DETECTED -- log error, return partial chain
      break;
    }
    visited.add(current.uuid);
    chain.push(current);
    current = current.parentUuid ? messages.get(current.parentUuid) : undefined;
  }
  return chain.reverse();  // oldest first
  // Then: OHY() recovers parallel tool execution messages
}
```

**Missing parentUuid handling:** If `messages.get(parentUuid)` returns `undefined`, the chain simply stops. No error, no crash. The partial chain is returned.

**Parallel transcript recovery (`OHY`):** After building the main chain, scans for assistant messages with the same `message.id` that aren't in the chain (parallel tool executions). These are sorted by timestamp and inserted after their corresponding assistant message. This recovers messages that were generated in parallel but recorded with different UUIDs.

### 2.7 Session Resume Flow (end-to-end)

1. CLI parses `--resume <sessionId>` or `-r`
2. `Gv(sessionId)` resolves to `~/.claude/projects/<hash>/<id>.jsonl`
3. `G26(path)` loads the file:
   a. If file > 5MB: `K48()` streams to find last `compact_boundary`, returns only post-boundary bytes
   b. If file <= 5MB or no boundary: reads entire file
   c. `mu()` parses JSONL lines into objects
   d. Messages go into UUID map, metadata into separate maps
   e. `wHY()` repairs preserved segment chains
4. `mn6(sessionId)` (the public API) calls `G26`, then:
   a. Finds latest non-sidechain leaf via `gb8()`
   b. Traces chain via `Vs6(messages, leaf)`
   c. Builds session state via `as1(chain, ...)`
5. Chain becomes the `messages` array for the new session

### 2.8 Answers to Path 1 Questions

**Q: What function handles --resume/--continue?**
A: The CLI arg parser routes to a session picker/loader component. The core loading is `mn6(sessionId)` -> `G26(path)` -> `Vs6(messages, leaf)`.

**Q: How is the JSONL file path determined?**
A: `Gv(sessionId)` = `join(projectStorageDir, sessionId + ".jsonl")`. Project storage is `~/.claude/projects/<hash>/`.

**Q: Is the entire file read or streamed?**
A: If file > 5MB (`Sr8`): K48 streams the file in 1MB chunks looking for the last `compact_boundary`. Only post-boundary content is parsed. Pre-boundary metadata (summaries, titles) is separately extracted by `ZHY()`. If file <= 5MB: entire file is read and parsed.

**Q: How does compact_boundary scanning work?**
A: Byte-level Buffer.indexOf() for the string `"compact_boundary"`. When found in a JSONL line, that specific line is JSON.parsed via `iXA()` to confirm `type==="system" && subtype==="compact_boundary"`. NOT a regex -- it's raw buffer scanning. Each boundary found resets the output buffer (discards everything before it).

**Q: What happens to messages BEFORE the boundary?**
A: They are completely discarded from the message set. Only their metadata lines (summary, custom-title, tag, agent-name, agent-color, agent-setting, mode, worktree-state, pr-link) are preserved via `ZHY()` which reads the pre-boundary portion looking for specific JSON keys.

**Q: How is parentUuid traced? What if missing?**
A: `Vs6()` walks backward from leaf via `parentUuid`. If a parentUuid points to a UUID not in the map, the chain simply stops (no error). Cycle detection via a Set prevents infinite loops.

**Q: Fields that cause messages to be SKIPPED during loading?**
A: In G26:
- Progress messages with certain data types (`Ns6` check) are skipped
- Progress messages with `normalizedMessages` arrays have them cleared (but message is kept)
- Non-message types (summary, custom-title, tag, etc.) go to metadata maps, not message map

**Q: Exact order of transformations from raw JSONL to API-ready messages?**
A: `G26 parse` -> `wHY repair` -> `Vs6 chain` -> `vk (slice from last boundary)` -> `L34 content-replacement` -> `Kp microcompact` -> `if4 autocompact` -> `aR8 (prepend user context)` -> `HX normalize` -> `gyq tool-result repair` -> `_JY image limit` -> API call

---

## 3. Path 2: Compaction

### 3.1 Compaction Triggers

Two paths:

**Auto-compaction (`if4`):**
- Checked EVERY turn in the main loop (`OS` function)
- Gated by: `DISABLE_COMPACT` env not set, `DISABLE_AUTO_COMPACT` env not set, `autoCompactEnabled` setting true
- Token check: `Uf(messages)` estimates current token count
- Threshold: `yd6(model)` = `contextWindow(model) - 13000` (the `Hh1 = 13000` headroom constant)
- Circuit breaker: After 3 consecutive failures (`lf4 = 3`), stops trying for the session

**Manual compaction (`/compact` command):**
- User invokes `/compact`
- Calls `nZ6()` directly with `isAutoCompact = false`
- Same core logic, but no threshold check

**Session Memory compaction (`YG8`):**
- Alternative compaction that uses session memory content as the summary
- Checked before full auto-compaction
- Only fires if `tengu_session_memory` and `tengu_sm_compact` feature flags are both enabled

### 3.2 Auto-Compact Check Flow (per turn)

```
1. OS() main loop starts new turn
2. Kp() microcompact runs (time-based tool result clearing)
3. if4() autocompact check runs:
   a. Check DISABLE_COMPACT env
   b. Check consecutive failure circuit breaker (>= 3 = skip)
   c. Ge9() token threshold check:
      - Calculate current tokens: Uf(messages)
      - Calculate threshold: contextWindow - 13000
      - If tokens >= threshold AND autoCompact enabled: proceed
   d. Try YG8() session memory compact first
   e. If no SM compact: call nZ6() full compaction
4. If compact succeeded: yield boundary + summary + attachments
5. Replace messages array with compacted result
```

### 3.3 Compaction Summary Generation (`nZ6`)

1. Count pre-compact tokens via `Uf(messages)`
2. Run pre-compact hooks via `rZ6()`
3. Build summary prompt:
   - Full compaction uses `ft9` template (9-section format)
   - Partial compaction uses `Zt9` template (same but scoped to recent messages)
   - Template includes `<<ANALYSIS_INSTRUCTION>>` placeholder replaced with analysis instructions
   - Optional custom instructions appended
   - Ends with: `"IMPORTANT: Do NOT use any tools. You MUST respond with ONLY the <summary>...</summary> block."`
4. Call Claude API (`iW4`) with messages + summary request
5. Extract text from response, strip `<analysis>` tags, extract `<summary>` content via `Gt9()`
6. Build re-attachment messages (file re-reads, plan state, agent context)
7. Construct boundary marker + summary messages

### 3.4 Compact Summary Prompt Template (9 sections)

The full template (`ft9`) requests:
1. Primary Request and Intent
2. Key Technical Concepts
3. Files and Code Sections (with code snippets)
4. Errors and fixes
5. Problem Solving
6. All user messages (non-tool-result)
7. Pending Tasks
8. Current Work (most recent, with file names and snippets)
9. Optional Next Step (with direct quotes from recent conversation)

### 3.5 EXACT JSON Format: Compact Boundary Entry

Created by `kd6()` (byte ~11045285):

```json
{
  "type": "system",
  "subtype": "compact_boundary",
  "content": "Conversation compacted",
  "isMeta": false,
  "timestamp": "2026-03-24T12:00:00.000Z",
  "uuid": "<random-uuid>",
  "level": "info",
  "compactMetadata": {
    "trigger": "auto" | "manual",
    "preTokens": 180000,
    "userContext": undefined,
    "messagesSummarized": undefined
  },
  "logicalParentUuid": "<uuid-of-last-message-before-compact>"
}
```

**With preserved segment** (via `oR1()`):
```json
{
  ...above...,
  "compactMetadata": {
    ...above...,
    "preservedSegment": {
      "headUuid": "<first-preserved-message-uuid>",
      "anchorUuid": "<summary-message-uuid>",
      "tailUuid": "<last-preserved-message-uuid>"
    },
    "preCompactDiscoveredTools": ["Read", "Write", "Bash", ...]
  }
}
```

### 3.6 EXACT JSON Format: Summary Message

Created by `F8()` with compaction-specific flags:

```json
{
  "type": "user",
  "message": {
    "role": "user",
    "content": "This session is being continued from a previous conversation that ran out of context. The summary below covers the earlier portion of the conversation.\n\n<summary content>\n\nIf you need specific details from before compaction (like exact code snippets, error messages, or content you generated), read the full transcript at: <path>\n\nRecent messages are preserved verbatim.\nContinue the conversation from where it left off without asking the user any further questions. Resume directly -- do not acknowledge the summary, do not recap what was happening, do not preface with \"I'll continue\" or similar. Pick up the last task as if the break never happened."
  },
  "isMeta": undefined,
  "isVisibleInTranscriptOnly": true,
  "isCompactSummary": true,
  "uuid": "<random-uuid>",
  "timestamp": "2026-03-24T12:00:00.000Z"
}
```

### 3.7 What Gets Appended After Summary

The `yl()` function (byte ~6857306) composes the full append sequence:

```js
function yl(compactionResult) {
  return [
    compactionResult.boundaryMarker,     // compact_boundary system message
    ...compactionResult.summaryMessages,  // the summary (user message)
    ...compactionResult.messagesToKeep ?? [],  // preserved recent messages (SM compact)
    ...compactionResult.attachments,      // file re-reads, plan state, agent context
    ...compactionResult.hookResults       // post-compact hook results
  ];
}
```

**Attachments include:**
- `nW4()`: Re-reads of files that were in context (via `readFileState`)
- `oW4()`: Plan state restoration
- `lZ8()`: Agent-specific context
- `aW4()`: Additional agent context
- `rW4()`: Agent worktree context
- Tool schema injections for dynamic tool loading
- MCP tool schema injections

### 3.8 logicalParentUuid

Set to `A[A.length-1]?.uuid` -- the UUID of the last message in the conversation before compaction. This is the "logical parent" of the boundary marker, indicating where in the conversation the compaction occurred.

### 3.9 Difference Between Auto and Manual Compaction

- **Trigger field:** `"auto"` vs `"manual"` in `compactMetadata.trigger`
- **Error handling:** Manual compaction propagates errors to user (`lW4()`). Auto compaction catches and counts failures.
- **Threshold:** Auto requires token count >= threshold. Manual has no threshold.
- **Circuit breaker:** Only auto has the 3-failure circuit breaker.
- **Session Memory:** Only auto-compaction attempts SM compact first.
- **Summary prompt:** Both use `PW4()` (full template). Partial compact (`cW4`) uses `DW4()` (partial template).

---

## 4. Path 3: JSONL to API Messages

### 4.1 The Normalization Pipeline

The transformation from JSONL entries to the `messages[]` array sent to the Claude API goes through multiple stages:

```
Raw JSONL entries
  |
  v
G26() parse -> messages Map<uuid, entry>
  |
  v
Vs6() chain trace -> ordered array (oldest to newest)
  |
  v
vk() slice from last compact_boundary
  |
  v
L34() content-replacement (tool result size reduction)
  |
  v
Kp() microcompact (time-based tool result clearing)
  |
  v
if4() autocompact check (may replace everything with summary)
  |
  v
aR8() prepend user context (system-reminder block)
  |
  v
HX() message normalization (THE critical function):
  - Filter out: progress, non-local-command system messages
  - Convert system messages to user messages (F8 wrapper)
  - Merge adjacent user messages
  - Merge attachments into preceding user messages
  - Merge assistant messages with same message.id (parallel tools)
  - Remove documents/images from preceding meta messages
  - Strip tool references if dynamic loading disabled
  |
  v
gyq() tool-result pairing repair:
  - Fix orphaned tool_results (no matching tool_use)
  - Fix missing tool_results (tool_use with no result)
  - Fix duplicate tool_use IDs
  - Remove server_tool_use without matching tool_use_id
  |
  v
_JY() image count limiting
  |
  v
It1() / byq() strip tool references (if no dynamic loading)
  |
  v
pn6() filter orphaned thinking-only messages
  |
  v
gn6() filter whitespace-only assistant messages
  |
  v
cjY() fix empty assistant content (replace with placeholder)
  |
  v
VjY() merge any remaining adjacent user messages
  |
  v
xD4() final validation
  |
  v
API call via callModel (aZ6)
```

### 4.2 Field Mapping: JSONL to API

**Fields SENT to the API (via the `message` property):**
| JSONL field | API field | Notes |
|---|---|---|
| `message.role` | `role` | "user" or "assistant" |
| `message.content` | `content` | string or content blocks array |
| `message.id` | -- | NOT sent to API, used for dedup/merge |
| `message.usage` | -- | NOT sent to API |

**Content block types that reach the API:**
- `text` -- text content
- `tool_use` -- tool invocations (input may be normalized by `pyq()`)
- `tool_result` -- tool results (may have content replaced by L34)
- `image` -- image content (subject to `_JY` limits)
- `document` -- document content
- `thinking` / `redacted_thinking` -- thinking blocks
- `server_tool_use` / `mcp_tool_use` -- MCP tool calls
- `code_execution_tool_result` -- code execution results
- `container_upload` -- container uploads

### 4.3 JSONL-Only Metadata Fields (NOT sent to API)

These fields exist on JSONL entries and control harness behavior but are stripped during normalization:

| Field | Purpose |
|---|---|
| `type` | Entry type: "user", "assistant", "system", "attachment", "progress" |
| `subtype` | For system entries: "compact_boundary", "local_command", "api_error", "hook_progress", "hook_response" |
| `uuid` | Unique message ID for chain tracking |
| `parentUuid` | Links to previous message in chain |
| `timestamp` | ISO timestamp for ordering |
| `isMeta` | If true: meta-message (not shown as user input) |
| `isVisibleInTranscriptOnly` | If true: shown in transcript but may be filtered from API |
| `isCompactSummary` | Marks summary messages from compaction |
| `isSidechain` | Marks messages from side conversations (not main chain) |
| `sessionId` | Which session this message belongs to |
| `toolUseResult` | Tool result text for display purposes |
| `mcpMeta` | MCP-specific metadata |
| `imagePasteIds` | References to pasted images |
| `sourceToolAssistantUUID` | Links tool result back to originating assistant message |
| `permissionMode` | Permission mode at time of message |
| `origin` | Origin information (e.g., `{kind: "channel"}`) |
| `isApiErrorMessage` | Marks API error responses |
| `apiError` | Error type (e.g., "max_output_tokens") |
| `message.model` | Model that generated this response |
| `summarizeMetadata` | Metadata for summarization |
| `forkedFrom` | Branch/fork origin info |
| `gitBranch` | Git branch at time of message |
| `cwd` | Working directory at time of message |
| `teamName` | Team name |
| `agentName` | Agent name |
| `agentSetting` | Agent configuration |
| `requestId` | API request ID for debugging |

### 4.4 The `isSidechain` Field

**Effect on message inclusion:**
- In `G26()`: sidechain messages ARE loaded into the message map
- In `mn6()`: `gb8()` explicitly filters for `!m.isSidechain` when finding the latest leaf
- In `Vs6()`: chain tracing follows `parentUuid` regardless of `isSidechain`
- In `vHY()` (leaf pruning): scans for `"isSidechain":true` to find the LAST non-sidechain message, then walks the parentUuid chain backward from it. Only messages in that chain (plus metadata entries) are kept; all other messages are pruned. This is the primary mechanism for reducing large JSONL files on session listing.
- In K48 streaming scan: K48 does NOT directly track `isSidechain`. It tracks `attribution-snapshot` entries (via `q48`). The sidechain filtering happens in `vHY()` and `mn6()`.

**Net effect:** Sidechain messages are loaded into the map but the main chain is traced from the latest non-sidechain leaf. Sidechain messages that happen to be in the parentUuid chain ARE included. The `vHY()` pruning (applied for files >5MB without preserved segments) aggressively drops messages not on the main chain.

### 4.5 The `isCompactSummary` Field

- Set to `true` on the summary message created during compaction
- Combined with `isVisibleInTranscriptOnly: true`
- In the normalization pipeline (`HX`): `isVisibleInTranscriptOnly` messages are included in normalization and DO get sent to the API
- The `bfq()` function: `isVisibleInTranscriptOnly` user messages are filtered ONLY when not the current prompt AND the caller specifically requests it -- but in the main normalization path they pass through

### 4.6 The `isVisibleInTranscriptOnly` Field

- Not directly filtered in `HX()` normalization
- In `bfq()` (used for session listing/display): if `isVisibleInTranscriptOnly` is true and `q` (includeTranscriptOnly) is false, the message is excluded from display -- but this is for UI listing, NOT for API message construction
- **Critical finding:** Summary messages with `isVisibleInTranscriptOnly: true` DO get sent to the API. The flag primarily controls transcript display, not API inclusion.

### 4.7 Tool Use / Tool Result Pairing: `gyq()` (byte ~11049519)

This function repairs broken tool use/result pairings. Critical for session resume where tool executions may be incomplete.

**Repairs performed:**
1. **Orphaned tool_results** (user message has tool_result but no preceding assistant with matching tool_use): Removes the tool_result, keeps remaining content. If nothing left and it's the first message, replaces with `"[Orphaned tool result removed due to conversation resume]"`.

2. **Duplicate tool_use IDs** in an assistant message: Removes duplicates.

3. **server_tool_use / mcp_tool_use without matching tool_use_id**: Removed from assistant content.

4. **Empty assistant content after cleanup**: Replaced with `[{type: "text", text: "[Tool use interrupted]"}]`.

5. **Missing tool_results** for tool_use IDs: Generates synthetic `{type: "tool_result", tool_use_id: id, content: "[Tool result missing due to internal error]", is_error: true}`.

6. **Duplicate tool_result for same tool_use_id**: Later duplicates removed.

7. **Orphaned tool_results in next user message** (tool_result for non-existent tool_use): Filtered out.

### 4.8 System Messages

System messages from the JSONL are NOT sent as `system` role to the API. They are:
- **`local_command` subtype**: Converted to a user message via `F8()` wrapper and merged into adjacent user messages
- **`compact_boundary`**: Used for slicing (`vk()`) but the boundary entry itself is NOT included in messages sent to API
- **Other system subtypes** (api_error, hook_progress, hook_response): Filtered OUT in HX normalization (`!gp1(entry)` check)

### 4.9 Role Values

Valid JSONL `type` values: `"user"`, `"assistant"`, `"system"`, `"attachment"`, `"progress"`
Valid API `role` values: Only `"user"` and `"assistant"` -- everything else is converted or filtered.

---

## 5. Critical Fields Reference

| Field | Present On | Sent to API? | Purpose |
|---|---|---|---|
| `type` | all entries | No | Entry classification (user/assistant/system/attachment/progress) |
| `subtype` | system entries | No | System message type (compact_boundary, local_command, api_error, etc.) |
| `uuid` | all messages | No | Unique identifier for chain tracking |
| `parentUuid` | all messages | No | Links to parent in conversation tree |
| `timestamp` | all messages | No | ISO 8601 creation time |
| `message.role` | user/assistant | Yes | "user" or "assistant" |
| `message.content` | user/assistant | Yes | Text string or content blocks array |
| `message.id` | assistant | No | API response ID (used for dedup/parallel merge) |
| `message.usage` | assistant | No | Token usage from API response |
| `isMeta` | user | No | Meta-message flag (not real user input) |
| `isCompactSummary` | user (summary) | No | Marks compaction summary messages |
| `isVisibleInTranscriptOnly` | user | No | Controls transcript display (not API filtering) |
| `isSidechain` | all messages | No | Marks non-main-chain messages |
| `sessionId` | all messages | No | Session ownership |
| `toolUseResult` | user (tool results) | No | Tool result text for UI display |
| `sourceToolAssistantUUID` | user (tool results) | No | Links to originating assistant |
| `isApiErrorMessage` | assistant | No | Marks error responses |
| `apiError` | assistant | No | Error type string |
| `logicalParentUuid` | compact_boundary | No | Parent before compaction |
| `compactMetadata` | compact_boundary | No | Compaction metadata (trigger, preTokens, preservedSegment, preCompactDiscoveredTools) |
| `isMeta: false` | compact_boundary | No | Always false on boundaries |
| `level` | system | No | Log level ("info", "error") |
| `content` | system | No | System message text |
| `forkedFrom` | user/assistant | No | Branch origin info |
| `gitBranch` | messages | No | Git branch at creation |
| `cwd` | messages | No | Working directory at creation |
| `origin` | user | No | Origin metadata (e.g., channel) |
| `permissionMode` | user | No | Permission mode |

---

## 6. Implications for Smart Compact

### MUST DO

1. **Include `type: "system"` and `subtype: "compact_boundary"`** -- K48 scans for the literal string `"compact_boundary"` in the JSONL. Without these exact fields, the boundary won't be detected.

2. **Include `uuid` field** with a valid UUID -- every message needs a unique UUID. Use `crypto.randomUUID()`.

3. **Include `timestamp` field** in ISO 8601 format -- used for ordering and chain traversal.

4. **Include `logicalParentUuid`** on the boundary marker -- set to the UUID of the last message before compaction. Required for chain reconstruction.

5. **Match the exact `compactMetadata` structure:**
   ```json
   {
     "trigger": "auto" | "manual",
     "preTokens": <number>,
     "preCompactDiscoveredTools": ["ToolName1", "ToolName2", ...]
   }
   ```
   The `preCompactDiscoveredTools` array is important for dynamic tool loading -- it carries forward which tools were discovered before compaction.

6. **Summary message must be `type: "user"`** with `role: "user"` -- the compaction summary is a user message, not a system message.

7. **Set `isCompactSummary: true` and `isVisibleInTranscriptOnly: true`** on the summary message -- these flags mark it appropriately for the harness.

8. **Include the continuation instruction** in the summary text: `"Continue the conversation from where it left off without asking the user any further questions..."` This prevents the model from asking "where were we?"

9. **Ensure tool_use / tool_result pairing is valid** in any preserved messages -- `gyq()` will repair broken pairings, but it's better to get it right. Every `tool_use` ID in an assistant message needs a corresponding `tool_result` in the next user message.

10. **Write entries as newline-delimited JSON** (one JSON object per line, `\n` separated).

### MUST NOT DO

1. **Do NOT use `type: "system"` for the summary message** -- summaries are `type: "user"`. Only the boundary marker is `type: "system"`.

2. **Do NOT omit the boundary marker** -- without it, K48 won't find the compaction point, and on resume the ENTIRE file will be parsed and loaded (not just post-compact messages).

3. **Do NOT put `subtype: "compact_boundary"` on the summary message** -- only the boundary marker has this subtype. The summary is a plain user message.

4. **Do NOT write multiple `compact_boundary` entries for one compaction** -- K48 finds the LAST one and discards everything before it. Multiple boundaries would cause data loss.

5. **Do NOT break the parentUuid chain** -- if you're preserving messages after the boundary, ensure their `parentUuid` values point to UUIDs that exist in the post-boundary content. Broken chains cause `Vs6()` to stop early. Sessions with Discord MCP, async sources, or parallel tool execution create **branching chains** -- there may be multiple chain roots in the preserved section, not just the first message. ALL orphaned parentUuids must be re-linked to the summary, not just the first one.

6. **Do NOT include `isSidechain: true` on main chain messages** -- `gb8()` explicitly filters these out when finding the conversation leaf. Setting this on a main chain message would orphan it.

7. **Do NOT set `isMeta: true` on the summary message without good reason** -- meta messages with `isVisibleInTranscriptOnly` are filtered in display contexts by `bfq()`. The default compaction sets `isMeta: undefined` on the summary.

8. **Do NOT exceed the context window with post-compact content** -- auto-compact fires when tokens >= `contextWindow - 13000`. If your compact result is still above threshold, it triggers re-compaction on the next turn (circuit breaker trips after 3 failures).

9. **Do NOT include `normalizedMessages` arrays in progress entries** -- G26 explicitly clears these to save memory. Don't waste bytes writing them.

10. **Do NOT serialize JSON with spaces in separators** -- K48's fast prefix check uses `Buffer.from('{"type":"system"')` (no space after colon). Python's `json.dumps` default produces `{"type": "system"` (with space), which K48 never matches. Use `json.dumps(obj, separators=(',', ':'))` for all entries that must be found by K48 (boundary and summary at minimum; compact separators for all re-serialized entries is safest).

11. **Do NOT use `"content-replacement"` type unless you understand the L34 pipeline** -- content replacements are applied by `L34()` before messages reach the API. They're tool-result-specific size reduction records, not general message transforms.

### Key Constants

| Constant | Value | Purpose |
|---|---|---|
| `Sr8` | 5,242,880 (5MB) | Threshold for K48 streaming scan and leaf pruning |
| `BJK` | 1,048,576 (1MB) | K48 read chunk size |
| `ha` | 65,536 (64KB) | Head/tail read buffer for session metadata |
| `Hh1` | 13,000 | Auto-compact headroom (tokens below context window) |
| `fe9` | 20,000 | Warning threshold (tokens from context limit) |
| `Ze9` | 20,000 | Error threshold (tokens from context limit) |
| `jh1` | 3,000 | Blocking limit headroom |
| `lf4` | 3 | Auto-compact circuit breaker (max consecutive failures) |
| `Lf4` | `"[Old tool result content cleared]"` | Microcompact replacement string |
| `QW4` | (variable) | Max file re-reads during compact attachment |
| `Rr8` | 200 | Max project path slug length |
| `dh6` | 104,857,600 (100MB) | Max JSONL file size for full read |
