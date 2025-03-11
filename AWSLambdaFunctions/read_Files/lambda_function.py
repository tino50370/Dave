import json
import urllib.request
import base64

def get_file_content_from_github(owner, repo, branch, token, file_path):
    """
    Fetches the content of a specified file from the GitHub repository and decodes it.
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{file_path}?ref={branch}"
    
    headers = {}
    if token:
        headers['Authorization'] = f'token {token}'
    
    req = urllib.request.Request(url, headers=headers)
    
    try:
        with urllib.request.urlopen(req) as response:
            file_data = json.loads(response.read().decode())
            decoded_content = base64.b64decode(file_data['content']).decode('utf-8')
            return {
                "name": file_data['name'],
                "path": file_data['path'],
                "content": decoded_content
            }
    except urllib.error.HTTPError as e:
        return {"error": f"GitHub API error: {e.code} - {e.reason}"}

def lambda_handler(event, context):
    """
    AWS Lambda function to fetch file contents from GitHub based on input parameters.
    """
    try:
        body = json.loads(event.get("body", "{}"))
        
        github_owner = body.get("GITHUB_OWNER")
        github_repo = body.get("GITHUB_REPO")
        branch = body.get("BRANCH", "main")  # Default to 'main' if not provided
        github_token = body.get("GITHUB_TOKEN")
        file_paths = body.get("filePaths", [])
        
        # Validate required parameters
        if not github_owner:
            return {"statusCode": 400, "body": json.dumps({"error": "GITHUB_OWNER is required"})}
        if not github_repo:
            return {"statusCode": 400, "body": json.dumps({"error": "GITHUB_REPO is required"})}
        if not file_paths:
            return {"statusCode": 400, "body": json.dumps({"error": "No file paths provided"})}
        
        # Fetch contents for each file
        file_contents = [
            get_file_content_from_github(github_owner, github_repo, branch, github_token, file_path)
            for file_path in file_paths
        ]
        
        return {
            'statusCode': 200,
            'body': json.dumps({"files": file_contents})
        }
    
    except Exception as e:
        return {
            'statusCode': 500,
            'body': json.dumps({"error": str(e)})
        }