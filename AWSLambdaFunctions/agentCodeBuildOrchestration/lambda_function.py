# app.py — Custom orchestration for "Generate Dockerfile" agent
# States: START → MODEL_INVOKED ↔ TOOL_INVOKED → FINISH

"""
Hard coded configuration
------------------------
- **MODEL_ID**        : Foundation model that generates/refines the Dockerfile.
- **READ_FILE_TOOL**  : Exact `toolUse.name` the model emits when it needs file
                        contents.

The **ReadFile** tool expects these inputs:
    BRANCH (str, required)
    GITHUB_REPO (str, required)
    GITHUB_OWNER (str, required)
    filePath (str, required, provided by the model)
    GITHUB_TOKEN (str, optional)

This Lambda captures the *static* fields (everything except `filePath`) from the
**initial user message** and stores them in `sessionAttributes`.  When the model
requests `ReadFile`, we merge those attributes with the model‑supplied
`filePath` so every invocation has the full parameter set.
"""

import json
import uuid
import re
from typing import Any, Dict, List, Tuple

MODEL_ID: str = "anthropic.claude-3-7-sonnet-20250219-v1:0"  # <- update if you switch models
READ_FILE_TOOL: str = "ReadFile"                              # <- must match the action‑group tool name
MAX_TOKENS: int = 2000
TEMPERATURE: float = 0.2

# ---------------------------- Utilities --------------------------------------

REQ_KEYS = {"BRANCH", "GITHUB_REPO", "GITHUB_OWNER"}
OPT_KEYS = {"GITHUB_TOKEN"}
ALL_KEYS = REQ_KEYS | OPT_KEYS


def _as_text(s: Any) -> str:
    """Return a plain string. If `s` is a JSON string with a top level `text` key, extract it."""
    if isinstance(s, str):
        try:
            obj = json.loads(s)
            if isinstance(obj, dict) and isinstance(obj.get("text"), str):
                return obj["text"]
        except Exception:
            pass
        return s
    return json.dumps(s)


def _extract_static_tool_params(text: str) -> Dict[str, str]:
    """Pull BRANCH, GITHUB_REPO, GITHUB_OWNER, GITHUB_TOKEN from the user's first message.

    Accepts either:
      * raw JSON: {"BRANCH": "main", "GITHUB_REPO": "my‑repo", ...}
      * free text lines like `BRANCH=main`, `GITHUB_REPO=my‑repo`.
    """
    params: Dict[str, str] = {}

    # Case 1: JSON
    try:
        candidate = json.loads(text)
        if isinstance(candidate, dict):
            for k in ALL_KEYS:
                if k in candidate and isinstance(candidate[k], str):
                    params[k] = candidate[k]
            if params:
                return params
    except Exception:
        pass

    # Case 2: key=value lines
    for line in text.splitlines():
        m = re.match(r"^(BRANCH|GITHUB_REPO|GITHUB_OWNER|GITHUB_TOKEN)\s*[=:]\s*(.+)$", line.strip())
        if m:
            params[m.group(1)] = m.group(2).strip()

    return params


def _invoke_model(messages: List[Dict[str, Any]], system_prompt: str | None = None, session_attrs: Dict[str, Any] | None = None):
    body = {
        "modelId": MODEL_ID,
        "messages": messages,
        "inferenceConfig": {"maxTokens": MAX_TOKENS, "temperature": TEMPERATURE},
    }
    if system_prompt:
        body["system"] = [{"text": system_prompt}]

    response = {
        "version": "1.0",
        "actionEvent": "INVOKE_MODEL",
        "output": {
            "text": json.dumps(body),
            "trace": {"event": {"text": f"INVOKE_MODEL {MODEL_ID}"}},
        },
    }
    if session_attrs:
        response["context"] = {"sessionAttributes": session_attrs}
    return response


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

    session_attrs: Dict[str, str] = event.get("context", {}).get("sessionAttributes", {}) or {}

    # ————————————— START — capture repo metadata & ask model to draft Dockerfile
    if state == "START":
        user_payload = _as_text(input_text)

        # Extract static tool parameters and stash them in session attributes so
        # we can reuse them for every ReadFile invocation.
        static_params = _extract_static_tool_params(user_payload)
        session_attrs.update(static_params)

        system_prompt = (
            "You are a senior DevOps engineer. Given a repository's file tree, "
            "produce a complete, secure Dockerfile suitable for production. "
            "If you lack necessary details (e.g., runtime version, build steps, "
            "start command), issue a tool call named '{tool}' to fetch the contents "
            "of specific files. The tool requires BRANCH, GITHUB_REPO, GITHUB_OWNER, "
            "and filePath. Provide only filePath; the orchestrator supplies the rest. "
            "Continue requesting files until ready, then respond ONLY with the final "
            "Dockerfile in triple backticks."
        ).format(tool=READ_FILE_TOOL)

        messages = [
            {
                "role": "user",
                "content": [
                    {"text": "Repository structure and task:"},
                    {"text": user_payload},
                ],
            }
        ]
        return _invoke_model(messages, system_prompt=system_prompt, session_attrs=session_attrs)

    # ————————————— MODEL_INVOKED — inspect the model's reply
    if state == "MODEL_INVOKED":
        try:
            resp = json.loads(input_text or "{}")
        except Exception:
            return _finish(f"Model response (unparsed):\n{input_text}")

        tool_use = _first_tool_use_from_converse_response(resp)
        if tool_use:
            name = tool_use.get("name")
            if name != READ_FILE_TOOL:
                return _finish(f"Unexpected tool requested: {name}")

            # Merge static params from session attributes with the filePath the model supplied
            tool_input = dict(tool_use.get("input") or {})
            for key in REQ_KEYS | OPT_KEYS:
                if key not in tool_input and key in session_attrs:
                    tool_input[key] = session_attrs[key]

            # Validate required keys
            missing = [k for k in REQ_KEYS if k not in tool_input or not tool_input[k]]
            if missing:
                return _finish(f"Missing required ReadFile parameters: {', '.join(missing)}")

            return _invoke_tool(name, tool_input)

        # No tool call → model produced the Dockerfile
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
                    {"text": tool_result_text[:120000]},
                ],
            },
        ]
        return _invoke_model(messages)

    # ————————————— Unknown state
    return _finish(f"Unhandled state '{state}'. Check your orchestration Lambda.")
