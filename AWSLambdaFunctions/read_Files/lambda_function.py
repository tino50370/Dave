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
                results.append({
                    "path": p,
                    "source_url": raw_url,
                    "encoding": "utf-8",
                    "length": len(blob),
                    "content": text,
                    "error": None
                })
            except UnicodeDecodeError:
                b64 = base64.b64encode(blob).decode("ascii")
                results.append({
                    "path": p,
                    "source_url": raw_url,
                    "encoding": "base64",
                    "length": len(blob),
                    "content": b64,
                    "error": None
                })
        except urllib.error.HTTPError as e:
            results.append({
                "path": p,
                "source_url": raw_url,
                "encoding": None,
                "length": 0,
                "content": None,
                "error": f"HTTPError {e.code}: {e.reason}"
            })
        except urllib.error.URLError as e:
            results.append({
                "path": p,
                "source_url": raw_url,
                "encoding": None,
                "length": 0,
                "content": None,
                "error": f"URLError: {e.reason}"
            })
        except Exception as e:
            results.append({
                "path": p,
                "source_url": raw_url,
                "encoding": None,
                "length": 0,
                "content": None,
                "error": f"Exception: {type(e).__name__}: {e}"
            })
    return results


def handler(event, context):
    """
    Core logic. Reads specified files from a GitHub repository.
    """
    try:
        provider = event.get("provider", "github").lower()
        if provider != "github":
            return _response(400, {"message": "Only provider 'github' is currently supported."})

        owner, repo = _parse_repo(event)
        ref = event.get("ref") or "main"
        paths = event.get("paths") or []
        if not isinstance(paths, list) or not paths:
            return _response(400, {"message": "'paths' must be a non-empty array of file paths."})
        if len(paths) > 100:
            return _response(400, {"message": "Too many paths; max is 100 per call."})

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
        return _response(400, {"message": str(ve)})
    except Exception as e:
        return _response(500, {"message": f"UnhandledException: {type(e).__name__}: {e}"})


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body)
    }


# ---------------------------
# Bedrock AgentCore Gateway entry point
# ---------------------------
def lambda_handler(event, context):
    """
    This is the entry point Lambda will call.
    It checks the tool name coming from AgentCore.
    """
    # Default to our tool if no special context is provided
    tool_name = "readFiles"
    try:
        # When invoked by AgentCore Gateway, the tool name is passed here:
        tool_name = context.client_context.custom.get("bedrockAgentCoreToolName", "readFiles")
    except Exception:
        # context.client_context.custom may not exist when testing manually
        pass

    if tool_name == "readFiles":
        return handler(event, context)
    else:
        return _response(400, {"message": f"Unknown tool name: {tool_name}"})
