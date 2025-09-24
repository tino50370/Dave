# file: lambda_function.py
import base64
import json
import os
import re
import urllib.request
import urllib.error

GITHUB_URL_RE = re.compile(r"^https?://github\.com/([^/]+)/([^/]+)(?:\.git)?/?$")

def _parse_repo(event):
    """
    Accepts either:
      - owner, repo
      - repo_url (e.g., https://github.com/owner/repo or .../repo.git)
    """
    owner = event.get("owner")
    repo = event.get("repo")
    repo_url = event.get("repo_url")

    if (owner and repo):
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
    # Ask GitHub for the raw file bytes
    req.add_header("Accept", "application/vnd.github.raw")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(), resp.getcode()

def _read_github_files(owner, repo, ref, paths, token):
    results = []
    for p in paths:
        # Normalize leading slashes
        norm = p.lstrip("/")
        raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{norm}"
        try:
            blob, _ = _http_get(raw_url, token=token)
            # Try UTF-8 first; fall back to base64 for binary/non-utf8
            try:
                text = blob.decode("utf-8")
                results.append({
                    "path": p,
                    "source_url": raw_url,
                    "encoding": "utf-8",
                    "length": len(blob),
                    "content": text,
                    "error": None,
                })
            except UnicodeDecodeError:
                b64 = base64.b64encode(blob).decode("ascii")
                results.append({
                    "path": p,
                    "source_url": raw_url,
                    "encoding": "base64",
                    "length": len(blob),
                    "content": b64,
                    "error": None,
                })
        except urllib.error.HTTPError as e:
            results.append({
                "path": p,
                "source_url": raw_url,
                "encoding": None,
                "length": 0,
                "content": None,
                "error": f"HTTPError {e.code}: {e.reason}",
            })
        except urllib.error.URLError as e:
            results.append({
                "path": p,
                "source_url": raw_url,
                "encoding": None,
                "length": 0,
                "content": None,
                "error": f"URLError: {e.reason}",
            })
        except Exception as e:
            results.append({
                "path": p,
                "source_url": raw_url,
                "encoding": None,
                "length": 0,
                "content": None,
                "error": f"Exception: {type(e).__name__}: {e}",
            })
    return results

def handler(event, context):
    """
    Expected event (see schema below):
    {
      "provider": "github",
      "owner": "octocat",                 # or use "repo_url"
      "repo": "Hello-World",              # or use "repo_url"
      "repo_url": "https://github.com/octocat/Hello-World",  # optional alternative
      "ref": "main",                      # branch, tag or commit SHA (default: main)
      "paths": ["README.md", "src/app.py"]
    }
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

        token = os.environ.get("GITHUB_TOKEN")  # optional; required for private repos
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
    # Plain JSON response for Gateway/Lambda integrations
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }