import re
from typing import List, Dict, Any

def sanitize_tool_use_ids(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Fix invalid tool_use.id and tool_result.tool_use_id that cause 400 error
    during session resume (GitHub issue #856).
    
    Claude API requires tool_use.id to match: ^toolu_[a-zA-Z0-9_-]+$
    """
    pattern = re.compile(r'^toolu_[a-zA-Z0-9_-]+$')

    for msg in messages:
        if not isinstance(msg.get("content"), list):
            continue

        for block in msg["content"]:
            if not isinstance(block, dict):
                continue

            # Sanitize tool_use blocks
            if block.get("type") == "tool_use":
                tool_id = block.get("id")
                if tool_id and not pattern.match(str(tool_id)):
                    safe_id = re.sub(r'[^a-zA-Z0-9_-]', '_', str(tool_id).strip())
                    if not safe_id.startswith("toolu_"):
                        safe_id = f"toolu_{safe_id}"
                    block["id"] = safe_id

            # Sanitize matching tool_result blocks (must match the tool_use.id)
            elif block.get("type") == "tool_result":
                tuid = block.get("tool_use_id")
                if tuid and not pattern.match(str(tuid)):
                    safe_id = re.sub(r'[^a-zA-Z0-9_-]', '_', str(tuid).strip())
                    if not safe_id.startswith("toolu_"):
                        safe_id = f"toolu_{safe_id}"
                    block["tool_use_id"] = safe_id

    return messages