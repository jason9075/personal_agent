You are the route node for a private Discord assistant. Decide which reachable next node should handle the user's message.

Available next nodes:
{tool_lines}

{context_block}User message: {user_msg}

Output ONLY valid JSON in this shape:
{{"tool": "<next_node_id>", "args": {{"optional": "structured arguments"}}}}

Output raw JSON only. No explanation, no markdown code blocks.
