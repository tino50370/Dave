import base64
import json
import os
import re
import urllib.request
import urllib.error

GITHUB_URL_RE = re.compile(r"^https?://github\.com/([^/]+)/([^/]+)(?:\.git)?/?$")

def _parse_repo(event):
    owner = event.get("owner")
    repo = event.get("repo")
    repo_url = event.get("repo_url")
    if owner and repo:
        return owner, repo
    if repo_url:
        m = GITHUB_URL_RE.match(repo_url.strip())
        if not m:
            raise ValueError("repo_url must look like https://github.com/<owner>/<repo>[.git]")
        return m.group(1), m.group(2).removesuffix(".git")
    raise ValueError("Provide either (owner and repo) or repo_url.")

def _http_get(url, token=None, timeout=20):
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github.raw")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(), resp.getcode()

def _read_github_files(owner, repo, ref, paths, token):
    results = []
    for p in paths:
        norm = p.lstrip("/")
        raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{norm}"
        try:
            blob, _ = _http_get(raw_url, token=token)
            try:
                text = blob.decode("utf-8")
                encoding, content = "utf-8", text
            except UnicodeDecodeError:
                encoding, content = "base64", base64.b64encode(blob).decode("ascii")

            results.append({
                "path": p,
                "source_url": raw_url,
                "encoding": encoding,
                "length": len(blob),
                "content": content,
                "error": None
            })
        except urllib.error.HTTPError as e:
            results.append({
                "path": p, "source_url": raw_url, "error": f"HTTPError {e.code}: {e.reason}"
            })
        except urllib.error.URLError as e:
            results.append({
                "path": p, "source_url": raw_url, "error": f"URLError: {e.reason}"
            })
        except Exception as e:
            results.append({
                "path": p, "source_url": raw_url, "error": f"Exception: {type(e).__name__}: {e}"
            })
    return results

def handler(event, context):
    try:
        provider = event.get("provider", "github").lower()
        if provider != "github":
            return _response(400, {"error": "Only 'github' provider is supported."})

        owner, repo = _parse_repo(event)
        ref = event.get("ref") or "main"
        paths = event.get("paths") or []
        if not paths:
            return _response(400, {"error": "'paths' must be a non-empty array."})

        token = os.environ.get("GITHUB_TOKEN")
        results = _read_github_files(owner, repo, ref, paths, token)

        return _response(200, {
            "provider": "github",
            "owner": owner,
            "repo": repo,
            "ref": ref,
            "results": results
        })
    except ValueError as ve:
        return _response(400, {"error": str(ve)})
    except Exception as e:
        return _response(500, {"error": f"UnhandledException: {type(e).__name__}: {e}"})

def _response(status, body):
    return {"statusCode": status, "body": json.dumps(body)}

def lambda_handler(event, context):
    """Entry point for Bedrock AgentCore Gateway."""
    tool_name = "readFiles"
    if isinstance(context, dict):
        tool_name = context.get("bedrockAgentCoreToolName", "readFiles")
    elif getattr(context, "client_context", None):
        tool_name = getattr(context.client_context.custom, "bedrockAgentCoreToolName", "readFiles")
    if tool_name != "readFiles":
        return _response(400, {"error": f"Unknown tool name: {tool_name}"})
    return handler(event, context)
