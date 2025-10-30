"""
app.py — Strands Agent (model-driven tool calling via AgentCore Gateway)

Env vars:
  AWS_REGION=us-east-1
  BEDROCK_MODEL_ID=us.anthropic.claude-3-7-sonnet-20250219-v1:0
  GATEWAY_MCP_URL=https://{gatewayId}.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp
  COG_DOMAIN=my-domain.auth.us-east-1.amazoncognito.com
  COG_SCOPE=gateway.invoke
  COG_SECRET_NAME=cognitoCreds   # Secret JSON: {"client_id":"...","client_secret":"..."}
"""

import os, json, time, base64, logging
from typing import Dict, Optional

import boto3, requests
from botocore.exceptions import ClientError

from strands import Agent
from strands.tools.mcp import MCPClient
from mcp.client.streamable_http import streamablehttp_client

from dotenv import load_dotenv
load_dotenv()


AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "openai.gpt-oss-120b-1:0")
GATEWAY_MCP_URL = os.environ["GATEWAY_MCP_URL"]
COG_DOMAIN      = os.environ["COG_DOMAIN"]
COG_SCOPE       = os.environ.get("COG_SCOPE", "genesis-gateway:invoke")
COG_SECRET_NAME = os.environ.get("COG_SECRET_NAME", "cognitoCreds")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("strands-agent")

# --- Compressed system prompt (≈200 tokens) ---
SYSTEM_PROMPT = (
    "You are a DevOps automation engineer who analyzes code repositories and generates secure, production-ready "
    "Dockerfiles.\n\n"
    "1. Determine if the repository is single-component (one service) or multi-component (e.g., frontend + backend).\n"
    "2. Identify languages, frameworks, and dependencies. Use the `readFiles` tool only when specific files "
    "(package.json, requirements.txt, pom.xml, go.mod, etc.) are needed for confirmation.\n"
    "3. For single-component repos, create one optimized Dockerfile using lightweight base images, multi-stage builds, "
    "pinned versions, and a non-root user.\n"
    "4. For multi-component repos, either build all parts in one multi-stage Dockerfile (e.g., frontend → backend) or "
    "output separate Dockerfiles per service for independent microservices.\n"
    "5. Apply best practices: minimize image size, remove build tools, use `.dockerignore`, define `ENV` and `EXPOSE`, "
    "and avoid secrets.\n"
    "6. Output each Dockerfile in fenced ```Dockerfile``` blocks followed by concise “### Notes” summarizing repo type, "
    "stack, base images, and optimizations."
)

# --- Secrets Manager (AWS sample style) ---
def get_cognito_secret() -> Dict[str, str]:
    session = boto3.session.Session(region_name=AWS_REGION)
    client = session.client("secretsmanager", region_name=AWS_REGION)
    try:
        resp = client.get_secret_value(SecretId=COG_SECRET_NAME)
    except ClientError as e:
        raise RuntimeError(f"Secrets Manager error for '{COG_SECRET_NAME}': {e}")
    payload = resp.get("SecretString") or base64.b64decode(resp["SecretBinary"]).decode("utf-8")
    data = json.loads(payload)
    cid, csec = data.get("client_id"), data.get("client_secret")
    if not cid or not csec:
        raise RuntimeError(f"Secret '{COG_SECRET_NAME}' must contain client_id and client_secret")
    return {"client_id": cid, "client_secret": csec}

# --- Cognito token provider (client_credentials) ---
class GatewayTokenProvider:
    def __init__(self, domain: str, scope: str):
        self.domain, self.scope = domain, scope
        self._token: Optional[str] = None
        self._exp_ts: float = 0.0

    def _fetch_new_token(self) -> str:
        creds = get_cognito_secret()
        basic = base64.b64encode(f"{creds['client_id']}:{creds['client_secret']}".encode()).decode()
        r = requests.post(
            f"https://{self.domain}/oauth2/token",
            headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials", "scope": self.scope},
            timeout=20,
        )
        if not r.ok:
            raise RuntimeError(f"Token endpoint error {r.status_code}: {r.text}")
        j = r.json()
        self._token = j["access_token"]
        self._exp_ts = time.time() + int(j.get("expires_in", 3600))
        log.info("Fetched new access token; expires in ~%ss", int(j.get("expires_in", 3600)))
        return self._token

    def get_valid_token(self) -> str:
        if not self._token or time.time() >= (self._exp_ts - 300):
            return self._fetch_new_token()
        return self._token

    def auth_headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.get_valid_token()}"}

    def refresh_on_unauthorized(self, func, *args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if "401" in str(e).lower() or "unauthorized" in str(e).lower():
                log.warning("401 from Gateway — refreshing token and retrying once.")
                self._fetch_new_token()
                return func(*args, **kwargs)
            raise

# --- MCP client factory (Gateway) ---
def make_gateway_client(token_provider: GatewayTokenProvider) -> MCPClient:
    def http_factory():
        return streamablehttp_client(GATEWAY_MCP_URL, headers=token_provider.auth_headers())
    return MCPClient(http_factory)

# --- Build model-driven agent (tools attached => model chooses tools) ---
def make_model_driven_agent(tools) -> Agent:
    return Agent(model=BEDROCK_MODEL_ID, tools=tools, system_prompt=SYSTEM_PROMPT)

def main():
    token_provider = GatewayTokenProvider(COG_DOMAIN, COG_SCOPE)
    mcp = make_gateway_client(token_provider)

    with mcp:
        # 1) Discover tools from Gateway (MCP tools/list)
        tools = token_provider.refresh_on_unauthorized(mcp.list_tools_sync)

        # Extra visibility (debug): show the raw first tool object/shape
        if tools:
            log.debug("First tool raw: %r", tools[0])
        else:
            log.warning("No tools returned from Gateway.")

        # Helper to safely extract a display name regardless of wrapper/dict shape
        def _tool_name(t):
            try:
                if hasattr(t, "name"):
                    return t.name
                if hasattr(t, "tool") and hasattr(t.tool, "name"):
                    return t.tool.name
                if isinstance(t, dict):
                    return t.get("name") or (t.get("tool") or {}).get("name")
            except Exception:
                pass
            return str(t)  # fallback for unknown shapes

        tool_names = [_tool_name(t) for t in tools]
        log.info("Gateway tools (%d): %s", len(tool_names), tool_names)

        # Unwrap to the underlying tool spec if the wrapper exposes `.tool`
        model_tools = [getattr(t, "tool", t) for t in tools]

        # ------------------------------------------------------------------
        # 2) DIRECT sanity test: list real names, select a target, call it once
        # ------------------------------------------------------------------
        def _extract_name(t):
            try:
                if hasattr(t, "name") and t.name:
                    return t.name
                if hasattr(t, "tool") and hasattr(t.tool, "name") and t.tool.name:
                    return t.tool.name
                if isinstance(t, dict):
                    return t.get("name") or (t.get("tool") or {}).get("name")
            except Exception:
                pass
            return None

        def _list_names(seq):
            out = []
            for t in seq:
                n = _extract_name(t)
                out.append(n or f"(no-name {type(t).__name__})")
            return out

        raw_names = _list_names(tools)
        unwrapped_names = _list_names(model_tools)
        log.info("Tool names (raw): %s", raw_names)
        log.info("Tool names (unwrapped): %s", unwrapped_names)

        # Prefer 'readFiles' if present (case-insensitive), else first named tool
        TARGET_NAME = None
        for n in unwrapped_names:
            if isinstance(n, str) and n.lower() == "readfiles":
                TARGET_NAME = n
                break
        if not TARGET_NAME:
            for n in unwrapped_names:
                if isinstance(n, str) and n.strip():
                    TARGET_NAME = n
                    break

        if not TARGET_NAME:
            raise RuntimeError("No usable tool name found. Check Gateway tool schema (`name` missing).")

        log.info("Using tool name for probe: %s", TARGET_NAME)

        # Try minimal read to prove the pipe works (match your inputSchema!)
        probe_args = {
            "provider": "github",
            "owner": "tino50370",
            "repo": "Django-MVC",
            "ref": "main",
            "paths": ["manage.py"]
        }
        # If your schema expects repo_url/ref/paths instead, use this:
        # probe_args = {
        #     "repo_url": "https://github.com/tino50370/Django-MVC",
        #     "ref": "main",
        #     "paths": ["manage.py"]
        # }

        try:
            probe = token_provider.refresh_on_unauthorized(
                mcp.call_tool_sync,
                name=TARGET_NAME,
                arguments=probe_args,
            )
            log.info("Direct probe OK. tool=%s args=%s", TARGET_NAME, probe_args)
            log.info("Probe response (truncated 500): %s", str(probe)[:500])
        except Exception as e:
            log.error("Direct probe FAILED. tool=%s args=%s err=%s", TARGET_NAME, probe_args, e)
            raise  # stop here — fix Gateway before involving the model
        # ------------------------------------------------------------------

        # 3) Model-driven agent: model decides which tool(s) to call
        agent = make_model_driven_agent(model_tools)

        # 4) Example user task (should trigger tool use if needed)
        task = (
            "Analyze the following repository metadata and file list, then generate a complete, "
            "secure, production-ready Dockerfile for it. "
            "Use the `readFiles` tool if you need to inspect specific files (like requirements.txt or settings.py) "
            "to confirm dependencies or configurations.\n\n"
            "Repository metadata:\n"
            "GITHUB_OWNER: tino50370\n"
            "GITHUB_REPO: Django-MVC\n"
            "BRANCH: main\n"
            "Private: false\n"
            "Total files: 18\n\n"
            "File structure:\n"
            "README.md\n"
            "manage.py\n"
            "personalizedView/__init__.py\n"
            "personalizedView/settings.py\n"
            "personalizedView/urls.py\n"
            "personalizedView/wsgi.py\n"
            "sbaApp/__init__.py\n"
            "sbaApp/admin.py\n"
            "sbaApp/models.py\n"
            "sbaApp/views.py\n"
            "sbaApp/urls.py\n"
            "sbaApp/tests.py\n"
            "sbaApp/serializers.py\n"
            "sbaApp/apps.py\n"
            "sbaApp/migrations/__init__.py\n"
            "personalizedView/asgi.py\n\n"
            "Goal: Produce the final Dockerfile and a short explanation of your design choices."
        )

        result = agent(task)  # Keep MCP context open so model can call tools
        print(json.dumps({"ok": True, "answer": result}, indent=2, default=str))




if __name__ == "__main__":
    main()
