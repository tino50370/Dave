# app.py
# Lambda handler for a Bedrock Agent that generates a Dockerfile.
#
# Flow:
#   START          → ask the model to draft/continue the Dockerfile
#   MODEL_INVOKED  → if the draft calls `ReadFile`, invoke the tool; else FINISH
#   TOOL_INVOKED   → feed tool output back to the model to refine/finish
#
# Docs: https://docs.aws.amazon.com/bedrock/latest/userguide/agents-custom-orchestration.html
#                                                                 :contentReference[oaicite:0]{index=0}
import json
import os
import uuid
from typing import Any, Dict

CONV_MODEL_ID = os.environ.get("MODEL_ID", "")
READ_FILE_TOOL = "ReadFiles"            # must match the tool name in the agent
MAX_HISTORY = 10                       # keep recent turns small to stay <256 KB


# ---------------------------------------------------------------------------
# Helpers that build the three possible responses
# ---------------------------------------------------------------------------

def _invoke_model_req(event: Dict[str, Any], *messages) -> Dict[str, Any]:
    """Create a Converse-API request and wrap it in an INVOKE_MODEL action."""
    body = {
        "modelId": CONV_MODEL_ID,
        "messages": list(messages)
    }
    return {
        "version": "1.0",
        "actionEvent": "INVOKE_MODEL",
        "output": {
            "text": json.dumps(body),
            "trace": {"event": {"text": "Invoking model"}}
        }
    }


def _invoke_tool_req(tool_name: str, tool_input: Dict[str, Any]) -> Dict[str, Any]:
    """Wrap a tool-call request in an INVOKE_TOOL action."""
    payload = {
        "toolUse": {
            "toolUseId": str(uuid.uuid4()),
            "name": tool_name,
            "input": tool_input,
        }
    }
    return {
        "version": "1.0",
        "actionEvent": "INVOKE_TOOL",
        "output": {
            "text": json.dumps(payload),
            "trace": {"event": {"text": f"Calling {tool_name}"}}
        }
    }


def _finish(final_text: str) -> Dict[str, Any]:
    """Return the final answer to the user."""
    return {
        "version": "1.0",
        "actionEvent": "FINISH",
        "output": {
            "text": final_text,
            "trace": {"event": {"text": "Conversation finished"}}
        }
    }


# ---------------------------------------------------------------------------
# Lambda entry-point
# ---------------------------------------------------------------------------

def lambda_handler(event: Dict[str, Any], context) -> Dict[str, Any]:
    """
    Orchestrator entry point.

    event["state"]  == "START" | "MODEL_INVOKED" | "TOOL_INVOKED"
    event["input"]["text"] holds JSON-encoded Converse or tool output
    """
    state = event["state"]

    # ------------------------------------------------------------------ START
    if state == "START":
        # The user's first message contains the repository structure.
        user_text = json.loads(event["input"]["text"])["text"]
        sys_prompt = (
            "You are an expert DevOps assistant.\n"
            "Given a repository’s file tree, generate a complete Dockerfile.\n"
            "If you need to read the contents of any files, respond with a "
            "single `toolUse` named ReadFiles, specifying the file paths.\n"
            "When you have all needed information, return ONLY the Dockerfile "
            "as triple-back-ticked code."
        )
        return _invoke_model_req(
            event,
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_text}
        )

    # ------------------------------------------------------------ MODEL_INVOKED
    if state == "MODEL_INVOKED":
        model_resp = json.loads(event["input"]["text"])

        # 1️⃣ The model asked for a tool
        if "toolUse" in model_resp:
            tool_use = model_resp["toolUse"]
            if tool_use.get("name") != READ_FILE_TOOL:
                raise ValueError(f"Unknown tool requested: {tool_use['name']}")
            return _invoke_tool_req(READ_FILE_TOOL, tool_use.get("input", {}))

        # 2️⃣ No tool call – model has produced the Dockerfile
        generation = model_resp.get("generation", "")
        return _finish(generation)

    # ------------------------------------------------------------- TOOL_INVOKED
    if state == "TOOL_INVOKED":
        # Tool output arrives as plain text in event["input"]["text"]
        tool_output = event["input"]["text"]

        assistant_msg = (
            f"The contents you requested are below:\n```\n{tool_output}\n```\n\n"
            "Update or finish the Dockerfile. If you still need more files, "
            "request them with another `toolUse`. Otherwise, output ONLY the "
            "final Dockerfile."
        )

        # Retrieve a bit of prior history to keep the thread coherent
        prior = (event["context"].get("session") or [])[-MAX_HISTORY:]

        messages = prior + [
            {"role": "assistant", "content": assistant_msg}
        ]

        return _invoke_model_req(event, *messages)

    # ----------------------------------------------------------------- UNKNOWN
    raise ValueError(f"Unhandled state: {state}")
