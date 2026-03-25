#!/usr/bin/env python3
"""Smart Compact: Context pruner for Claude Code session JSONL files.

Extends Claude Code sessions beyond the context window by inserting a
compact_boundary marker + narrative summary + preserved recent messages.
Format matches Claude Code's native /compact output exactly.

Usage:
    python smart-compact.py [session_file] [options]
    python smart-compact.py --dry-run --keep-recent 500

See README.md for full documentation.
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
import uuid as uuid_mod
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def find_latest_session() -> Path | None:
    """Auto-detect the most recently modified session JSONL."""
    claude_dir = Path.home() / ".claude" / "projects"
    if not claude_dir.exists():
        return None
    jsonl_files = sorted(
        claude_dir.glob("**/*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    # Skip subagent files
    jsonl_files = [f for f in jsonl_files if "subagents" not in str(f)]
    return jsonl_files[0] if jsonl_files else None


def content_byte_size(content) -> int:
    """Calculate byte size of a content field (string or list)."""
    if isinstance(content, str):
        return len(content.encode("utf-8"))
    if isinstance(content, list):
        return len(json.dumps(content, ensure_ascii=False).encode("utf-8"))
    return len(str(content).encode("utf-8"))


def count_lines(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (1 if not text.endswith("\n") else 0)


def format_size(size_bytes: int) -> str:
    if size_bytes >= 1_048_576:
        return f"{size_bytes:,} bytes ({size_bytes / 1_048_576:.1f} MB)"
    if size_bytes >= 1024:
        return f"{size_bytes:,} bytes ({size_bytes / 1024:.1f} KB)"
    return f"{size_bytes:,} bytes"


def new_uuid() -> str:
    return str(uuid_mod.uuid4())


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# ---------------------------------------------------------------------------
# JSONL Parsing
# ---------------------------------------------------------------------------

def parse_jsonl(session_path: Path) -> list[tuple[str, dict | None]]:
    """Read all lines from JSONL, returning (raw_line, parsed_obj_or_None)."""
    parsed = []
    with open(session_path, "r", encoding="utf-8") as f:
        for raw_line in f:
            raw_line = raw_line.rstrip("\n")
            if not raw_line.strip():
                parsed.append((raw_line, None))
                continue
            try:
                obj = json.loads(raw_line)
                parsed.append((raw_line, obj))
            except (json.JSONDecodeError, ValueError):
                parsed.append((raw_line, None))
    return parsed


def is_conversation_message(obj: dict) -> bool:
    """Return True if this JSONL entry is a user/assistant conversation message."""
    return obj.get("type") in ("user", "assistant")


def is_any_message(obj: dict) -> bool:
    """Return True if this entry is any message type the session loader processes."""
    return obj.get("type") in ("user", "assistant", "attachment", "system", "progress")


def find_last_compact_boundary(lines: list[tuple[str, dict | None]]) -> int:
    """Find index of last compact_boundary entry, or -1 if none."""
    for i in range(len(lines) - 1, -1, -1):
        _, obj = lines[i]
        if obj and obj.get("type") == "system" and obj.get("subtype") == "compact_boundary":
            return i
    return -1


# ---------------------------------------------------------------------------
# Tool extraction (for preCompactDiscoveredTools)
# ---------------------------------------------------------------------------

def extract_content_blocks(obj: dict) -> list | None:
    """Extract the content block array from various JSONL wrapper structures."""
    msg = obj.get("message")
    if isinstance(msg, dict):
        content = msg.get("content")
        if isinstance(content, list):
            return content
    content = obj.get("content")
    if isinstance(content, list):
        return content
    return None


def extract_discovered_tools(lines: list[tuple[str, dict | None]]) -> list[str]:
    """Extract unique tool names from all tool_use blocks in the session."""
    tools = set()
    for _raw, obj in lines:
        if obj is None:
            continue
        content = extract_content_blocks(obj)
        if not content:
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                name = block.get("name")
                if name:
                    tools.add(name)
    return sorted(tools)


def index_tool_uses(lines: list[tuple[str, dict | None]]) -> dict:
    """Build an index of tool_use_id -> {name, input} from all parsed lines."""
    index = {}
    for _raw, obj in lines:
        if obj is None:
            continue
        content = extract_content_blocks(obj)
        if content:
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_id = block.get("id")
                    if tool_id:
                        index[tool_id] = {
                            "name": block.get("name"),
                            "input": block.get("input", {}),
                        }
    return index


# ---------------------------------------------------------------------------
# Conversation extraction (for narrative summary generation)
# ---------------------------------------------------------------------------

def extract_conversation_text(lines: list[tuple[str, dict | None]],
                              end_index: int) -> list[str]:
    """Extract conversation turns as plain text, stripping tool blocks.

    Returns a list of "ROLE: text" strings suitable for summarization.
    Only processes lines up to end_index (the split point).
    """
    turns = []
    for i, (_raw, obj) in enumerate(lines):
        if i >= end_index:
            break
        if obj is None or not is_conversation_message(obj):
            continue

        role = obj.get("type", "?")
        content = obj.get("message", {}).get("content", "")

        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    # Skip tool_use, tool_result, thinking, image blocks
                elif isinstance(block, str):
                    text_parts.append(block)
            text = "\n".join(text_parts)
        else:
            text = str(content)

        text = text.strip()
        if text:
            turns.append(f"{role.upper()}: {text}")

    return turns


# ---------------------------------------------------------------------------
# Tool result summarization (for post-boundary slimming)
# ---------------------------------------------------------------------------

def make_tool_summary(tool_name: str | None, tool_input: dict | None,
                      content) -> str:
    """Generate a contextual summary from the paired tool_use info."""
    original_bytes = content_byte_size(content)
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(block.get("text", block.get("content", "")))
            elif isinstance(block, str):
                parts.append(block)
        text = "\n".join(str(p) for p in parts)
    else:
        text = str(content)
    lines = count_lines(text)

    if not tool_name:
        return f"[Tool result pruned — {lines} lines, {original_bytes:,} bytes]"

    inp = tool_input or {}
    name_lower = tool_name.lower()

    if name_lower in ("read", "view"):
        fp = inp.get("file_path", inp.get("path", "?"))
        return f"[Read {fp} — {lines} lines, {original_bytes:,} bytes]"
    if name_lower in ("bash", "shell"):
        cmd = inp.get("command", "?")
        if len(cmd) > 120:
            cmd = cmd[:117] + "..."
        return f"[Ran `{cmd}` — {lines} lines of output, {original_bytes:,} bytes]"
    if name_lower in ("grep", "search"):
        pattern = inp.get("pattern", "?")
        path = inp.get("path", inp.get("directory", "."))
        return f"[Searched for '{pattern}' in {path} — {lines} result lines]"
    if name_lower in ("glob", "list"):
        pattern = inp.get("pattern", "?")
        path = inp.get("path", ".")
        return f"[Listed files matching '{pattern}' in {path} — {lines} lines]"
    if name_lower in ("edit",):
        fp = inp.get("file_path", inp.get("path", "?"))
        return f"[Edited {fp} — result {original_bytes:,} bytes]"
    if name_lower in ("write",):
        fp = inp.get("file_path", inp.get("path", "?"))
        return f"[Wrote {fp} — result {original_bytes:,} bytes]"
    return f"[Tool '{tool_name}' completed — {lines} lines, {original_bytes:,} bytes]"


def slim_tool_result_block(block: dict, tool_index: dict,
                           threshold: int) -> bool:
    """Slim a single tool_result block if oversized. Returns True if slimmed."""
    if not isinstance(block, dict) or block.get("type") != "tool_result":
        return False
    content = block.get("content")
    if content is None:
        return False
    size = content_byte_size(content)
    if size <= threshold:
        return False
    tool_use_id = block.get("tool_use_id")
    tool_info = tool_index.get(tool_use_id)
    tool_name = tool_info["name"] if tool_info else None
    tool_input = tool_info["input"] if tool_info else None
    block["content"] = make_tool_summary(tool_name, tool_input, content)
    return True


def slim_thinking_block(block: dict, threshold: int) -> bool:
    """Slim a thinking block if oversized. Returns True if slimmed."""
    if not isinstance(block, dict) or block.get("type") != "thinking":
        return False
    thinking = block.get("thinking", "")
    if not isinstance(thinking, str):
        return False
    size = len(thinking.encode("utf-8"))
    if size <= threshold:
        return False
    lines = count_lines(thinking)
    block["thinking"] = f"[Thinking pruned — {lines} lines, {size:,} bytes]"
    return True


# ---------------------------------------------------------------------------
# Compact boundary + summary construction (exact Claude Code format)
# ---------------------------------------------------------------------------

def build_compact_boundary(last_message_uuid: str, session_id: str,
                           cwd: str, pre_token_estimate: int,
                           discovered_tools: list[str],
                           preserved_segment: dict | None) -> dict:
    """Build a compact_boundary system entry matching Claude Code's format.

    Field format verified against Claude Code cli.js source.
    See docs/claude-code-deep-trace.md section 3.5.
    """
    metadata = {
        "trigger": "smart-compact",
        "preTokens": pre_token_estimate,
        "preCompactDiscoveredTools": discovered_tools,
    }
    if preserved_segment:
        metadata["preservedSegment"] = preserved_segment

    return {
        "type": "system",
        "subtype": "compact_boundary",
        "content": "Conversation compacted",
        "isMeta": False,
        "timestamp": now_iso(),
        "uuid": new_uuid(),
        "level": "info",
        "compactMetadata": metadata,
        "logicalParentUuid": last_message_uuid,
        "parentUuid": last_message_uuid,
        "isSidechain": False,
        "sessionId": session_id,
        "cwd": cwd,
    }


def build_summary_message(boundary_uuid: str, session_id: str,
                          cwd: str, summary_text: str,
                          session_path: str) -> dict:
    """Build the summary user message matching Claude Code's format.

    Content is a plain string (not content blocks array).
    See docs/claude-code-deep-trace.md section 3.6.
    """
    full_text = (
        "This session is being continued from a previous conversation "
        "that ran out of context. The summary below covers the earlier "
        "portion of the conversation.\n\n"
        + summary_text
        + "\n\nIf you need specific details from before compaction "
        "(like exact code snippets, error messages, or content you generated), "
        "read the full transcript at: " + session_path
        + "\n\nRecent messages are preserved verbatim.\n"
        "Continue the conversation from where it left off without asking "
        "the user any further questions. Resume directly -- do not "
        "acknowledge the summary, do not recap what was happening, "
        "do not preface with \"I'll continue\" or similar. Pick up the "
        "last task as if the break never happened."
    )

    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": full_text,
        },
        "isVisibleInTranscriptOnly": True,
        "isCompactSummary": True,
        "uuid": new_uuid(),
        "timestamp": now_iso(),
        "parentUuid": boundary_uuid,
        "isSidechain": False,
        "sessionId": session_id,
        "cwd": cwd,
    }


# ---------------------------------------------------------------------------
# Core: determine split point
# ---------------------------------------------------------------------------

def find_split_point(lines: list[tuple[str, dict | None]],
                     keep_recent: int) -> int:
    """Find the split index. Everything before this is summarized,
    everything from this index forward is preserved.

    Respects tool_use/tool_result pairing.
    """
    conv_indices = []
    for i, (_raw, obj) in enumerate(lines):
        if obj and is_conversation_message(obj):
            conv_indices.append(i)

    if len(conv_indices) <= keep_recent:
        return 0

    split_conv_idx = len(conv_indices) - keep_recent
    split_line_idx = conv_indices[split_conv_idx]

    # Don't split a tool_use/tool_result pair
    _, split_obj = lines[split_line_idx]
    if split_obj and split_obj.get("type") == "user":
        content = extract_content_blocks(split_obj)
        if content:
            has_tool_results = any(
                isinstance(b, dict) and b.get("type") == "tool_result"
                for b in content
            )
            if has_tool_results:
                for j in range(split_line_idx - 1, -1, -1):
                    _, prev_obj = lines[j]
                    if prev_obj and prev_obj.get("type") == "assistant":
                        split_line_idx = j
                        break

    return split_line_idx


# ---------------------------------------------------------------------------
# Core: metadata extraction + chain repair
# ---------------------------------------------------------------------------

def extract_session_metadata(lines: list[tuple[str, dict | None]]) -> dict:
    """Extract session-level metadata from the JSONL entries."""
    session_id = None
    cwd = None
    last_uuid = None

    for _raw, obj in lines:
        if obj is None:
            continue
        if session_id is None and obj.get("sessionId"):
            session_id = obj["sessionId"]
        if cwd is None and obj.get("cwd"):
            cwd = obj["cwd"]
        if obj.get("uuid"):
            last_uuid = obj["uuid"]

    return {
        "session_id": session_id or "unknown",
        "cwd": cwd or os.getcwd(),
        "last_uuid": last_uuid or new_uuid(),
    }


def repair_preserved_chain(preserved_lines: list[tuple[str, dict | None]],
                           anchor_uuid: str) -> dict:
    """Fix the parentUuid chain so the first preserved message links
    to the summary message.

    Returns preservedSegment metadata: {headUuid, anchorUuid, tailUuid}.
    """
    head_uuid = None
    tail_uuid = None

    for _raw, obj in preserved_lines:
        if obj and obj.get("uuid"):
            if head_uuid is None:
                head_uuid = obj["uuid"]
            tail_uuid = obj["uuid"]

    # First message links to anchor (summary)
    for i, (raw, obj) in enumerate(preserved_lines):
        if obj and is_any_message(obj):
            obj["parentUuid"] = anchor_uuid
            preserved_lines[i] = (json.dumps(obj, ensure_ascii=False), obj)
            break

    return {
        "headUuid": head_uuid or anchor_uuid,
        "anchorUuid": anchor_uuid,
        "tailUuid": tail_uuid or anchor_uuid,
    }


# ---------------------------------------------------------------------------
# Main compact logic
# ---------------------------------------------------------------------------

def load_summary_content(summary_path: Path | None) -> str:
    """Load the summary text from a file, or return a default."""
    if summary_path and summary_path.exists():
        return summary_path.read_text(encoding="utf-8")
    return (
        "Summary:\n"
        "The previous conversation context was compacted. "
        "Specific details of earlier tool results and file reads were condensed. "
        "Recent conversation messages are preserved in full below."
    )


def estimate_tokens(lines: list[tuple[str, dict | None]]) -> int:
    """Rough token estimate: ~4 chars per token."""
    total_chars = sum(len(raw) for raw, _ in lines)
    return total_chars // 4


def smart_compact(session_path: Path, threshold: int, keep_recent: int,
                  prune_thinking: bool, dry_run: bool, no_backup: bool,
                  summary_path: Path | None,
                  extract_conversation: Path | None) -> None:
    """Main compact logic."""

    if not session_path.exists():
        print(f"Error: File not found: {session_path}", file=sys.stderr)
        sys.exit(1)

    size_before = session_path.stat().st_size
    print(f"Session: {session_path}")
    print(f"Size: {format_size(size_before)}")

    # Parse
    all_lines = parse_jsonl(session_path)
    total_entries = len([1 for _, obj in all_lines if obj is not None])
    conv_count = len([1 for _, obj in all_lines
                      if obj and is_conversation_message(obj)])
    print(f"Entries: {total_entries} total, {conv_count} conversation messages")

    # Check for existing compact boundary
    existing_boundary = find_last_compact_boundary(all_lines)
    if existing_boundary >= 0:
        print(f"Existing compact_boundary at line {existing_boundary + 1}")
        pre_boundary = all_lines[:existing_boundary + 1]
        post_boundary = all_lines[existing_boundary + 1:]
        work_lines = post_boundary
        prefix_lines = pre_boundary
    else:
        work_lines = all_lines
        prefix_lines = []

    work_conv_count = len([1 for _, obj in work_lines
                          if obj and is_conversation_message(obj)])
    print(f"Messages in working set: {work_conv_count}")

    if work_conv_count <= keep_recent:
        print(f"Only {work_conv_count} conversation messages "
              f"— below --keep-recent {keep_recent}. Nothing to compact.")
        return

    # Find split point
    split_idx = find_split_point(work_lines, keep_recent)
    if split_idx == 0:
        print("Split point at beginning — nothing to compact.")
        return

    pre_split = work_lines[:split_idx]
    post_split = work_lines[split_idx:]

    pre_conv = len([1 for _, obj in pre_split
                    if obj and is_conversation_message(obj)])
    post_conv = len([1 for _, obj in post_split
                     if obj and is_conversation_message(obj)])
    print(f"Will summarize {pre_conv} messages, "
          f"preserve {post_conv} recent messages")

    # Extract conversation mode: write stripped text and exit
    if extract_conversation is not None:
        turns = extract_conversation_text(work_lines, split_idx)
        with open(extract_conversation, "w", encoding="utf-8") as f:
            f.write("\n\n---\n\n".join(turns))
        print(f"\nExtracted {len(turns)} conversation turns "
              f"to: {extract_conversation}")
        print(f"Size: {format_size(extract_conversation.stat().st_size)}")
        est = extract_conversation.stat().st_size // 4
        print(f"Estimated tokens: ~{est:,}")
        print("\nGenerate a summary from this file, then re-run with "
              "--summary-file <path>")
        return

    # Extract metadata
    meta = extract_session_metadata(all_lines)

    # UUID of last message before split
    last_pre_uuid = meta["last_uuid"]
    for _raw, obj in reversed(pre_split):
        if obj and obj.get("uuid"):
            last_pre_uuid = obj["uuid"]
            break

    # Token estimate
    pre_tokens = estimate_tokens(all_lines)

    # Discovered tools from entire session
    discovered_tools = extract_discovered_tools(all_lines)
    print(f"Discovered tools: {len(discovered_tools)} "
          f"({', '.join(discovered_tools[:8])}"
          f"{'...' if len(discovered_tools) > 8 else ''})")

    # Build summary message (need UUID for preservedSegment)
    summary_text = load_summary_content(summary_path)
    boundary_uuid = new_uuid()

    summary_msg = build_summary_message(
        boundary_uuid=boundary_uuid,
        session_id=meta["session_id"],
        cwd=meta["cwd"],
        summary_text=summary_text,
        session_path=str(session_path),
    )
    summary_uuid = summary_msg["uuid"]

    # Repair parentUuid chain and get preservedSegment metadata
    preserved_segment = repair_preserved_chain(post_split, summary_uuid)

    # Build compact_boundary
    boundary = build_compact_boundary(
        last_message_uuid=last_pre_uuid,
        session_id=meta["session_id"],
        cwd=meta["cwd"],
        pre_token_estimate=pre_tokens,
        discovered_tools=discovered_tools,
        preserved_segment=preserved_segment,
    )
    boundary["uuid"] = boundary_uuid

    # Slim oversized tool_results in preserved section
    tool_index = index_tool_uses(all_lines)
    slimmed_count = 0
    thinking_pruned = 0
    tool_results_found = 0

    for i, (raw, obj) in enumerate(post_split):
        if obj is None:
            continue
        content = extract_content_blocks(obj)
        if not content:
            continue
        modified = False
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                tool_results_found += 1
                if slim_tool_result_block(block, tool_index, threshold):
                    slimmed_count += 1
                    modified = True
            if prune_thinking and block.get("type") == "thinking":
                if slim_thinking_block(block, threshold):
                    thinking_pruned += 1
                    modified = True
        if modified:
            post_split[i] = (json.dumps(obj, ensure_ascii=False), obj)

    # Assemble output
    output_lines = []
    for raw, _obj in prefix_lines:
        output_lines.append(raw)
    for raw, _obj in pre_split:
        output_lines.append(raw)
    output_lines.append(json.dumps(boundary, ensure_ascii=False))
    output_lines.append(json.dumps(summary_msg, ensure_ascii=False))
    for raw, _obj in post_split:
        output_lines.append(raw)

    # Output path
    if dry_run:
        output_path = session_path.with_suffix(".compact.jsonl")
    else:
        output_path = session_path
        if not no_backup:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = Path(f"{session_path}.backup-{ts}")
            shutil.copy2(session_path, backup_path)
            print(f"Backup: {backup_path}")

    # Atomic write
    tmp_fd, tmp_path = tempfile.mkstemp(
        suffix=".jsonl", dir=str(output_path.parent)
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            for line in output_lines:
                f.write(line + "\n")
        os.replace(tmp_path, str(output_path))
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    size_after = output_path.stat().st_size
    post_boundary_size = sum(
        len(l.encode("utf-8"))
        for l in output_lines[-(len(post_split) + 2):]
    )

    print()
    print("--- Smart Compact Results ---")
    print(f"Messages summarized:   {pre_conv}")
    print(f"Messages preserved:    {post_conv}")
    print(f"Tool results found:    {tool_results_found}")
    print(f"Tool results slimmed:  {slimmed_count}")
    if prune_thinking:
        print(f"Thinking blocks pruned: {thinking_pruned}")
    print(f"Discovered tools:      {len(discovered_tools)}")
    print(f"File size before: {format_size(size_before)}")
    print(f"File size after:  {format_size(size_after)}")
    print(f"Context on resume: ~{format_size(post_boundary_size)}")
    print(f"  (loader skips everything before compact_boundary)")
    est_tokens = post_boundary_size // 4
    print(f"Estimated tokens on resume: ~{est_tokens:,}")
    if dry_run:
        print(f"\nDry run — output at: {output_path}")
    else:
        print(f"\nCompacted in place: {output_path}")

    print("\n--- Integrity Check ---")
    verify_output(output_path)


def verify_output(path: Path) -> None:
    """Verify the compacted JSONL has valid structure."""
    lines = parse_jsonl(path)
    errors = []

    # 1. compact_boundary exists
    boundary_idx = find_last_compact_boundary(lines)
    if boundary_idx < 0:
        errors.append("FAIL: No compact_boundary found")
    else:
        print(f"OK: compact_boundary at line {boundary_idx + 1}")

    # 2. Summary follows boundary
    if boundary_idx >= 0 and boundary_idx + 1 < len(lines):
        _, summary_obj = lines[boundary_idx + 1]
        if summary_obj and summary_obj.get("isCompactSummary"):
            print("OK: Summary message follows boundary")
            msg = summary_obj.get("message", {})
            content = msg.get("content")
            if isinstance(content, str):
                print("OK: Summary content is plain string")
            else:
                errors.append("WARN: Summary content is not a plain string")
        else:
            errors.append("FAIL: No summary message after compact_boundary")
    else:
        errors.append("FAIL: Nothing after compact_boundary")

    # 3. compactMetadata fields
    if boundary_idx >= 0:
        _, boundary_obj = lines[boundary_idx]
        if boundary_obj:
            cm = boundary_obj.get("compactMetadata", {})
            if "trigger" in cm:
                print(f"OK: compactMetadata.trigger = {cm['trigger']}")
            else:
                errors.append("FAIL: Missing compactMetadata.trigger")
            if "preTokens" in cm:
                print(f"OK: compactMetadata.preTokens = {cm['preTokens']:,}")
            else:
                errors.append("FAIL: Missing compactMetadata.preTokens")
            if "preCompactDiscoveredTools" in cm:
                tools = cm["preCompactDiscoveredTools"]
                print(f"OK: preCompactDiscoveredTools = {len(tools)} tools")
            else:
                errors.append("FAIL: Missing preCompactDiscoveredTools")
            if "preservedSegment" in cm:
                ps = cm["preservedSegment"]
                has_all = all(
                    k in ps for k in ("headUuid", "anchorUuid", "tailUuid")
                )
                if has_all:
                    print("OK: preservedSegment has all required keys")
                else:
                    errors.append("WARN: preservedSegment missing keys")
            else:
                errors.append("WARN: Missing preservedSegment")
            if boundary_obj.get("logicalParentUuid"):
                print("OK: logicalParentUuid present")
            else:
                errors.append("FAIL: Missing logicalParentUuid")

    # 4. Tool pairing in post-boundary
    if boundary_idx >= 0:
        post_boundary = lines[boundary_idx + 1:]
        tool_uses = {}
        tool_results = set()
        for _raw, obj in post_boundary:
            if obj is None:
                continue
            content = extract_content_blocks(obj)
            if not content:
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    tool_uses[block.get("id")] = True
                if block.get("type") == "tool_result":
                    tool_results.add(block.get("tool_use_id"))
        orphaned_results = tool_results - set(tool_uses.keys())
        orphaned_uses = set(tool_uses.keys()) - tool_results
        if not orphaned_results and not orphaned_uses:
            print(f"OK: {len(tool_uses)} tool_use/tool_result pairs intact")
        else:
            if orphaned_results:
                errors.append(
                    f"WARN: {len(orphaned_results)} tool_results "
                    f"without matching tool_use"
                )
            if orphaned_uses:
                errors.append(
                    f"WARN: {len(orphaned_uses)} tool_uses "
                    f"without matching tool_result"
                )

    # 5. Summary links to boundary
    if boundary_idx >= 0 and boundary_idx + 1 < len(lines):
        _, boundary_obj_check = lines[boundary_idx]
        _, summary_obj = lines[boundary_idx + 1]
        boundary_uuid_check = (
            boundary_obj_check.get("uuid") if boundary_obj_check else None
        )
        summary_parent = (
            summary_obj.get("parentUuid") if summary_obj else None
        )
        if boundary_uuid_check and summary_parent == boundary_uuid_check:
            print("OK: Summary links to boundary")
        elif boundary_uuid_check:
            errors.append(
                f"WARN: Summary parentUuid is '{summary_parent}', "
                f"expected '{boundary_uuid_check}'"
            )

    # 6. First preserved message links to summary
    if boundary_idx >= 0 and boundary_idx + 1 < len(lines):
        _, summary_obj = lines[boundary_idx + 1]
        summary_uuid = summary_obj.get("uuid") if summary_obj else None
        if summary_uuid:
            for _raw, obj in lines[boundary_idx + 2:]:
                if obj and is_any_message(obj):
                    if obj.get("parentUuid") == summary_uuid:
                        print("OK: First preserved message links to summary")
                    else:
                        errors.append(
                            f"WARN: First preserved message parentUuid is "
                            f"'{obj.get('parentUuid')}', "
                            f"expected '{summary_uuid}'"
                        )
                    break

    # 7. Boundary parentUuid == logicalParentUuid
    if boundary_idx >= 0:
        _, boundary_obj = lines[boundary_idx]
        bp = boundary_obj.get("parentUuid") if boundary_obj else None
        lp = boundary_obj.get("logicalParentUuid") if boundary_obj else None
        if bp and bp == lp:
            print("OK: Boundary parentUuid == logicalParentUuid")
        elif bp:
            errors.append(f"WARN: parentUuid != logicalParentUuid")

    if errors:
        for e in errors:
            print(f"  {e}")
    else:
        print("ALL CHECKS PASSED")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Smart Compact: Context pruner for Claude Code sessions.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry run on latest session
  %(prog)s --dry-run

  # Compact with 500 preserved messages
  %(prog)s --keep-recent 500

  # Extract conversation for external summarization
  %(prog)s --extract-conversation conversation.txt --keep-recent 500

  # Compact with custom narrative summary
  %(prog)s --keep-recent 500 --summary-file my-summary.md

  # Full pipeline: extract → summarize → compact
  %(prog)s --extract-conversation conv.txt -k 500
  # (generate summary from conv.txt using your preferred method)
  %(prog)s -k 500 --summary-file summary.md
""",
    )
    parser.add_argument(
        "session_file", nargs="?", default=None,
        help="Path to .jsonl session file. Auto-detects if omitted.",
    )
    parser.add_argument(
        "-t", "--threshold", type=int, default=1500,
        help="Byte threshold for slimming tool results (default: 1500)",
    )
    parser.add_argument(
        "-k", "--keep-recent", type=int, default=500,
        help="Recent conversation messages to preserve (default: 500)",
    )
    parser.add_argument(
        "--summary-file", type=str, default=None,
        help="Path to summary text file for the compaction boundary.",
    )
    parser.add_argument(
        "--extract-conversation", type=str, default=None,
        help="Extract pre-split conversation text (stripped of tool blocks) "
             "to this file and exit. Use to generate a narrative summary "
             "with your preferred tool before compacting.",
    )
    parser.add_argument(
        "--prune-thinking", action="store_true",
        help="Also slim large thinking blocks in preserved section.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Write to .compact.jsonl instead of overwriting.",
    )
    parser.add_argument(
        "--no-backup", action="store_true",
        help="Skip creating a backup before overwriting.",
    )

    args = parser.parse_args()

    if args.session_file:
        session_path = Path(args.session_file)
    else:
        session_path = find_latest_session()
        if session_path is None:
            print("Error: No session files found.", file=sys.stderr)
            sys.exit(1)
        print(f"Auto-detected session: {session_path}")

    summary_path = Path(args.summary_file) if args.summary_file else None
    extract_path = (
        Path(args.extract_conversation)
        if args.extract_conversation
        else None
    )

    smart_compact(
        session_path=session_path,
        threshold=args.threshold,
        keep_recent=args.keep_recent,
        prune_thinking=args.prune_thinking,
        dry_run=args.dry_run,
        no_backup=args.no_backup,
        summary_path=summary_path,
        extract_conversation=extract_path,
    )


if __name__ == "__main__":
    main()
