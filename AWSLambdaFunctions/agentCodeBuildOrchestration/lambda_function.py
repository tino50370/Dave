# app.py — Custom orchestration for "Generate Dockerfile" agent
# States: START → MODEL_INVOKED ↔ TOOL_INVOKED → FINISH

"""
Hard‑coded configuration
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

🔎 **Debugging aid**: Every helper now logs **both** the dict it is about to
return *and* the `json.dumps()` version, so CloudWatch shows the exact payload
Bedrock will receive.
"""

import json
import uuid
import re
import logging
from typing import Any, Dict, List

# ——— configure root logger so `print()` isn’t required ————————————————
logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
log = logging.getLogger(__name__)

MODEL_ID: str = "anthropic.claude-3-7-sonnet-20250219-v1:0"  # <- update if you switch models
READ_FILE_TOOL: str = "ReadFile"                              # <- must match the action‑group tool name
MAX_TOKENS: int = 2000
TEMPERATURE: float = 0.2

# ---------------------------- Utilities --------------------------------------

REQ_KEYS = {"BRANCH", "GITHUB_REPO", "GITHUB_OWNER"}
OPT_KEYS = {"GITHUB_TOKEN"}
ALL_KEYS = REQ_KEYS | OPT_KEYS


def _log_response(label: str, obj: Dict[str, Any]):
    """Print and log the response dict and its JSON string."""
    text_version = json.dumps(obj, default=str)  # default=str to avoid non‑serialisable values
    log.info("%s | dict: %s", label, obj)
    log.info("%s | json: %s", label, text_version)


def _as_text(s: Any) -> str:
    """Return a plain string. If `s` is a JSON string with a top‑level `text` key, extract it."""
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
    """Pull BRANCH, GITHUB_REPO, GITHUB_OWNER, GITHUB_TOKEN from the user's first message."""
    params: Dict[str, str] = {}

    # Case 1: JSON blob
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

    # Case 2: env‑style lines
    for line in text.splitlines():
        m = re.match(r"^(BRANCH|GITHUB_REPO|GITHUB_OWNER|GITHUB_TOKEN)\s*[=:]\s*(.+)$", line.strip())
        if m:
            params[m.group(1)] = m.group(2).strip()

    return params


def _invoke_model(messages: List[Dict[str, Any]], *, system_prompt: str | None = None, session_attrs: Dict[str, Any] | None = None):
    body = {
        "modelId": MODEL_ID,
        "messages": messages,
        "inferenceConfig": {"maxTokens": MAX_TOKENS, "temperature": TEMPERATURE},
    }
    if system_prompt:
        body["system"] = [{"text": system_prompt}]

    response: Dict[str, Any] = {
        "version": "1.0",
        "actionEvent": "INVOKE_MODEL",
        "output": {
            "text": json.dumps(body),
            "trace": {"event": {"text": f"INVOKE_MODEL {MODEL_ID}"}},
        },
    }
    if session_attrs:
        response["context"] = {"sessionAttributes": session_attrs}

    _log_response("INVOKE_MODEL return", response)
    return response


def _invoke_tool(name: str, tool_input: Dict[str, Any]):
    payload = {"toolUse": {"toolUseId": str(uuid.uuid4()), "name": name, "input": tool_input}}
    response = {
        "version": "1.0",
        "actionEvent": "INVOKE_TOOL",
        "output": {
            "text": json.dumps(payload),
            "trace": {"event": {"text": f"INVOKE_TOOL {name}"}},
        },
    }
    _log_response("INVOKE_TOOL return", response)
    return response


def _finish(text: str):
    response = {
        "version": "1.0",
        "actionEvent": "FINISH",
        "output": {"text": text, "trace": {"event": {"text": "FINISH"}}},
    }
    _log_response("FINISH return", response)
    return response


def _first_tool_use_from_converse_response(resp: Dict[str, Any]):
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

def lambda_handler(event: Dict[str, Any], context):
    """Main entry‑point for the custom‑orchestration Lambda."""
    log.info("Incoming event: %s", json.dumps(event))

    state = event.get("state")
    input_text = event.get("input", {}).get("text", "")
    session_attrs: Dict[str, str] = event.get("context", {}).get("sessionAttributes", {}) or {}

    # ————————————— START
    if state == "START":
        user_payload = _as_text(input_text)
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

    # ————————————— MODEL_INVOKED
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

            tool_input = dict(tool_use.get("input") or {})
            for key in REQ_KEYS | OPT_KEYS:
                if key not in tool_input and key in session_attrs:
                    tool_input[key] = session_attrs[key]

            missing = [k for k in REQ_KEYS if k not in tool_input or not tool_input[k]]
            if missing:
                return _finish(f"Missing required ReadFile parameters: {', '.join(missing)}")

            return _invoke_tool(name, tool_input)

        final_text = _final_text_from_converse_response(resp)
        return _finish(final_text)

    # ————————————— TOOL_INVOKED
    if state == "TOOL_INVOKED":
        tool_result_text = _as_text(input_text).strip()
        assistant_summary = (
            "Tool results received. Use them to refine or finish the Dockerfile. "
            "If more details are needed, request another file; otherwise, output only "
            "the final Dockerfile in triple backticks."
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
        return _invoke
