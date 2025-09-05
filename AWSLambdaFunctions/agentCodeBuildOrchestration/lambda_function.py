# app.py — Custom orchestration for "Generate Dockerfile" agent
# States: START → MODEL_INVOKED ↔ TOOL_INVOKED → FINISH

"""
This version derives **MODEL_ID** (the foundation model to call) **and**
**READ_FILE_TOOL** (the tool name emitted by the model) **directly from the
Bedrock `event` payload**, so you don’t need to wire them through environment
variables.

*  `MODEL_ID` → `event["context"]["agentConfiguration"]["defaultModelId"]`
*  `READ_FILE_TOOL` → first action‑group name that contains both
   "read" and "file" (case‑insensitive).  Fallback to the name your model
   eventually requests.

The rest of the logic is unchanged.
"""

import json
import os
import uuid
from typing import Any, Dict, List, Tuple

MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "2000"))
TEMPERATURE = float(os.environ.get("TEMPERATURE", "0.2"))

# ---------------------------- Helpers ---------------------------------------


def _as_text(s: Any) -> str:
    """Return a plain string; if `s` is a JSON string with a top‑level `text` key, extract it."""
    if isinstance(s, str):
        try:
            obj = json.loads(s)
            if isinstance(obj, dict) and isinstance(obj.get("text"), str):
                return obj["text"]
        except Exception:
            pass
        return s
    return json.dumps(s)


def _extract_config(event: Dict[str, Any]) -> Tuple[str, str]:
    """Pull `model_id` and `read_file_tool` from the invocation payload."""
    agent_cfg = event.get("context", {}).get("agentConfiguration", {})

    # Default model ID that the agent itself is configured with
    model_id = agent_cfg.get("defaultModelId") or "anthropic.claude-3-sonnet-20240229-v1:0"

    # Heuristically grab the ReadFile action‑group name
    read_file_tool = "ReadFile"  # sensible fallback
    for ag in agent_cfg.get("actionGroups", []):
        name = ag.get("name") or ag.get("actionGroupName")
        if name and "read" in name.lower() and "file" in name.lower():
            read_file_tool = name
            break

    return model_id, read_file_tool


def _invoke_model(model_id: str, messages: List[Dict[str, Any]], system_prompt: str | None = None):
    body = {
        "modelId": model_id,
        "messages": messages,
        "inferenceConfig": {"maxTokens": MAX_TOKENS, "temperature": TEMPERATURE},
    }
    if system_prompt:
        body["system"] = [{"text": system_prompt}]
    return {
        "version": "1.0",
        "actionEvent": "INVOKE_MODEL",
        "output": {
            "text": json.dumps(body),
            "trace": {"event": {"text": f"INVOKE_MODEL {model_id}"}},
        },
    }


def _invoke_tool(name: str, tool_input: Dict[str, Any]):
    payload = {"toolUse": {"toolUseId": str(uuid.uuid4()), "name": name, "input": tool_input}}
    return {
        "version": "1.0",
        "actionEvent": "INVOKE_TOOL",
        "output": {
            "text": json.dumps(payload),
            "trace": {"event": {"text": f"INVOKE_TOOL {name}"}},
        },
    }


def _finish(text: str):
    return {
        "version": "1.0",
        "actionEvent": "FINISH",
        "output": {"text": text, "trace": {"event": {"text": "FINISH"}}},
    }


def _first_tool_use_from_converse_response(resp: Dict[str, Any]):
    """Return the first `toolUse` block found in a Converse response, else `None`."""
    try:
        for block in resp["output"]["message"]["content"]:
            if isinstance(block, dict) and "toolUse" in block:
                return block["toolUse"]
    except Exception:
        pass

    if isinstance(resp.get("toolUse"), dict):
        return resp["toolUse"]

    return None


def _final_text_from_converse_response(resp: Dict[str, Any]) -> str:
    try:
        blocks = resp["output"]["message"]["content"]
        texts = [b["text"] for b in blocks if isinstance(b, dict) and "text" in b]
        if texts:
            return "\n".join(texts).strip()
    except Exception:
        pass

    for key in ("generation", "outputText"):
        if isinstance(resp.get(key), str):
            return resp[key].strip()

    return json.dumps(resp)


# ---------------------------- Handler ---------------------------------------

def lambda_handler(event: Dict[str, Any], context) -> Dict[str, Any]:
    """Main entry‑point for the custom‑orchestration Lambda."""

    state = event.get("state")
    input_text = event.get("input", {}).get("text", "")

    # Pull model/tool configuration from the event
    model_id, read_file_tool = _extract_config(event)

    # ————————————— START — ask the model to draft/plan the Dockerfile
    if state == "START":
        user_payload = _as_text(input_text)
        system_prompt = (
            "You are a senior DevOps engineer. Given a repository's file tree, "
            "produce a complete, secure Dockerfile suitable for production. "
            "If you lack necessary details (e.g., runtime version, build steps, "
            "start command), issue a tool call named '{tool}' to fetch the contents "
            "of specific files. Keep asking for files until ready. When ready, "
            "respond ONLY with the final Dockerfile in triple backticks."
        ).format(tool=read_file_tool)

        messages = [
            {
                "role": "user",
                "content": [
                    {"text": "Repository structure and task:"},
                    {"text": user_payload},
                ],
            }
        ]
        return _invoke_model(model_id, messages, system_prompt=system_prompt)

    # ————————————— MODEL_INVOKED — inspect the model's reply
    if state == "MODEL_INVOKED":
        try:
            resp = json.loads(input_text or "{}")
        except Exception:
            return _finish(f"Model response (unparsed):\n{input_text}")

        tool_use = _first_tool_use_from_converse_response(resp)
        if tool_use:
            name = tool_use.get("name")
            if name != read_file_tool:
                return _finish(f"Unexpected tool requested: {name}")
            tool_input = tool_use.get("input") or {}
            return _invoke_tool(name, tool_input)

        final_text = _final_text_from_converse_response(resp)
        return _finish(final_text)

    # ————————————— TOOL_INVOKED — feed tool results back to the model
    if state == "TOOL_INVOKED":
        tool_result_text = _as_text(input_text).strip()
        assistant_summary = (
            "Tool results received. Use them to refine or finish the Dockerfile. "
            "If more details are needed, request another file with a toolUse; "
            "otherwise, output ONLY the final Dockerfile in triple backticks."
        )

        messages = [
            {"role": "assistant", "content": [{"text": assistant_summary}]},
            {
                "role": "user",
                "content": [
                    {"text": "Requested file contents:"},
                    {"text": tool_result_text[:120000]},  # stay below 256 KB
                ],
            },
        ]
        return _invoke_model(model_id, messages)

    # ————————————— Unknown state
    return _finish(f"Unhandled state '{state}'. Check your orchestration Lambda.")
