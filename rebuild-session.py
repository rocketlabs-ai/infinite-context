#!/usr/bin/env python3
"""Rebuild a session JSONL with compressed older turns + preserved recent turns.

No compact_boundary. No summary framing. Just real messages — compressed older
turns look like shorter versions of the real conversation, then preserved recent
turns at full fidelity. The model sees one continuous conversation.
"""

import json
import os
import shutil
import sys
import uuid as uuid_mod
from datetime import datetime, timezone
from pathlib import Path


def new_uuid():
    return str(uuid_mod.uuid4())


def extract_text(content):
    """Extract readable text from message content (string or blocks array)."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, str):
                texts.append(block)
            elif isinstance(block, dict):
                btype = block.get("type", "")
                if btype == "text":
                    t = block.get("text", "")
                    if t.strip():
                        texts.append(t.strip())
        return "\n".join(texts)
    return ""


def is_filler(text, role):
    """Returns True if this turn is filler that can be dropped."""
    t = text.lower().strip()
    # Drop very short non-substantive messages
    if len(t) < 20 and role == "assistant":
        return True
    if len(t) < 10:
        return True
    # Drop echoes, polling, and low-value patterns
    filler_phrases = [
        "own echo", "ignoring", "waiting on", "noting silently",
        "no new activity", "monitoring for", "cancelling the monitoring",
        "no delivery yet", "will do", "noted", "got it",
        "checking", "let me check", "let me read",
    ]
    for phrase in filler_phrases:
        if t.startswith(phrase):
            return True
    # Drop Discord channel routing noise (raw channel tags)
    if t.startswith("hannel source=") or t.startswith("<channel source="):
        # Keep if there's substantive text after the tag
        # Strip the channel tag to check
        import re
        stripped = re.sub(r'</?channel[^>]*>', '', t).strip()
        if len(stripped) < 30:
            return True
    # Drop cron/system check messages
    if "eck lucien" in t and "fetch last" in t:
        return True
    return False


def compress_turn(text, max_chars=500):
    """Compress a turn while preserving voice. Cuts at sentence boundary."""
    if len(text) <= max_chars:
        return text
    # Find last sentence boundary within the limit
    chunk = text[:max_chars]
    # Try to cut at sentence end (. ! ? followed by space or end)
    for end_char in [". ", ".\n", "! ", "!\n", "? ", "?\n"]:
        last = chunk.rfind(end_char)
        if last > max_chars // 3:  # don't cut too early
            return chunk[:last + 1]
    # Fall back to cutting at last space
    last_space = chunk.rfind(" ")
    if last_space > max_chars // 3:
        return chunk[:last_space]
    return chunk


def main():
    if len(sys.argv) < 2:
        print("Usage: rebuild-session.py <session.jsonl> [--keep-recent N] [--max-chars N] [--dry-run]")
        sys.exit(1)

    session_path = Path(sys.argv[1])
    keep_recent = 396
    max_chars = 500
    dry_run = False

    i = 2
    while i < len(sys.argv):
        if sys.argv[i] == "--keep-recent":
            keep_recent = int(sys.argv[i + 1])
            i += 2
        elif sys.argv[i] == "--max-chars":
            max_chars = int(sys.argv[i + 1])
            i += 2
        elif sys.argv[i] == "--dry-run":
            dry_run = True
            i += 1
        else:
            i += 1

    # Parse
    print(f"Session: {session_path}")
    all_entries = []
    with open(session_path, "r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
                all_entries.append(obj)
            except:
                continue

    # Collect conversation messages with their indices
    conv_indices = []
    for i, obj in enumerate(all_entries):
        if obj.get("type") in ("user", "assistant"):
            conv_indices.append(i)

    total_conv = len(conv_indices)
    print(f"Total entries: {len(all_entries)}, conversation messages: {total_conv}")

    if total_conv <= keep_recent:
        print("Nothing to compress.")
        return

    # Split point
    split_conv = total_conv - keep_recent
    split_entry_idx = conv_indices[split_conv]

    # Get session metadata
    session_id = None
    cwd = None
    for obj in all_entries:
        if not session_id and obj.get("sessionId"):
            session_id = obj["sessionId"]
        if not cwd and obj.get("cwd"):
            cwd = obj["cwd"]
        if session_id and cwd:
            break

    print(f"Session ID: {session_id}")
    print(f"Split: compress {split_conv} messages, preserve {keep_recent}")

    # PHASE 1: Compress older conversation messages
    compressed = []
    dropped = 0
    for idx in conv_indices[:split_conv]:
        obj = all_entries[idx]
        msg = obj.get("message", {})
        role = msg.get("role", obj.get("type", ""))
        content = msg.get("content", "")
        text = extract_text(content)

        if not text:
            dropped += 1
            continue

        # Skip compaction summaries and meta messages
        if obj.get("isCompactSummary") or obj.get("isVisibleInTranscriptOnly"):
            dropped += 1
            continue

        # Drop filler
        if is_filler(text, role):
            dropped += 1
            continue

        # Compress
        compressed_text = compress_turn(text, max_chars)
        compressed.append({
            "role": role,
            "text": compressed_text,
            "timestamp": obj.get("timestamp"),
        })

    print(f"Compressed: {len(compressed)} turns kept, {dropped} dropped")

    # PHASE 2: Collect preserved entries, slim tool results
    preserved = []
    preserved_dropped = 0
    for i in range(split_entry_idx, len(all_entries)):
        obj = all_entries[i]
        etype = obj.get("type", "")
        # Drop queue-operations and non-essential system entries
        if etype == "queue-operation":
            preserved_dropped += 1
            continue
        if etype == "system" and obj.get("subtype") in (
            "compact_boundary", "turn_duration", "stop_hook_summary",
            "hook_progress", "hook_response",
        ):
            preserved_dropped += 1
            continue
        # Slim oversized tool results in preserved messages
        msg = obj.get("message", {})
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    rc = block.get("content", "")
                    if isinstance(rc, str) and len(rc) > 1500:
                        lines = rc.count("\n") + 1
                        block["content"] = f"[Tool result: {lines} lines, {len(rc):,} bytes]"
                    elif isinstance(rc, list) and len(json.dumps(rc)) > 1500:
                        block["content"] = f"[Tool result: {len(json.dumps(rc)):,} bytes]"
        preserved.append(obj)

    print(f"Preserved entries: {len(preserved)} (dropped {preserved_dropped} non-essential)")

    # PHASE 3: Build output JSONL
    output = []
    prev_uuid = None

    # Write compressed turns as real messages
    for turn in compressed:
        uuid = new_uuid()
        entry = {
            "type": "user" if turn["role"] == "user" else "assistant",
            "uuid": uuid,
            "timestamp": turn["timestamp"] or datetime.now(timezone.utc).isoformat(),
            "sessionId": session_id,
            "cwd": cwd,
            "isSidechain": False,
        }
        if prev_uuid:
            entry["parentUuid"] = prev_uuid

        if turn["role"] == "user":
            entry["message"] = {
                "role": "user",
                "content": turn["text"],
            }
        else:
            entry["message"] = {
                "role": "assistant",
                "content": [{"type": "text", "text": turn["text"]}],
            }

        output.append(json.dumps(entry, ensure_ascii=False, separators=(',', ':')))
        prev_uuid = uuid

    # Write preserved entries with linearized chain
    for obj in preserved:
        uuid = obj.get("uuid")
        if uuid:
            if prev_uuid:
                obj["parentUuid"] = prev_uuid
            prev_uuid = uuid
        output.append(json.dumps(obj, ensure_ascii=False, separators=(',', ':')))

    # Calculate stats
    output_size = sum(len(line.encode("utf-8")) for line in output)
    est_tokens = output_size // 4

    print(f"\n--- Rebuild Results ---")
    print(f"Compressed turns: {len(compressed)}")
    print(f"Preserved entries: {len(preserved)}")
    print(f"Total output lines: {len(output)}")
    print(f"Output size: {output_size:,} bytes ({output_size / 1_048_576:.1f} MB)")
    print(f"Estimated tokens: ~{est_tokens:,}")

    # Write
    if dry_run:
        out_path = session_path.with_suffix(".rebuilt.jsonl")
    else:
        out_path = session_path
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = Path(f"{session_path}.backup-pre-rebuild-{ts}")
        shutil.copy2(session_path, backup)
        print(f"Backup: {backup}")

    import tempfile
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".jsonl", dir=str(out_path.parent))
    with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
        for line in output:
            f.write(line + "\n")
    os.replace(tmp_path, str(out_path))
    print(f"Written: {out_path}")


if __name__ == "__main__":
    main()
