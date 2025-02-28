import json
import urllib.request
import os
import base64

# GitHub repository details
GITHUB_OWNER = "tino50370"  # Replace with your GitHub username/org
GITHUB_REPO = "Django-MVC"  # Replace with your repo name
BRANCH = "main"

GITHUB_TOKEN = os.getenv('GITHUB_TOKEN')  # Optional: Needed for private repos

def get_file_content_from_github(file_path):
    """
    Fetches the content of a specified file from the GitHub repository and decodes it.
    """
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{file_path}?ref={BRANCH}"
    
    headers = {}
    if GITHUB_TOKEN:
        headers['Authorization'] = f'token {GITHUB_TOKEN}'
    
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
    AWS Lambda function to fetch file contents from GitHub based on input file paths.
    """
    try:
        body = json.loads(event.get("body", "{}"))
        file_paths = body.get("file_paths", [])
        
        if not file_paths:
            return {"statusCode": 400, "body": json.dumps({"error": "No file paths provided"})}
        
        file_contents = [get_file_content_from_github(file_path) for file_path in file_paths]
        
        return {
            'statusCode': 200,
            'body': json.dumps({"files": file_contents})
        }
    
    except Exception as e:
        return {
            'statusCode': 500,
            'body': json.dumps({"error": str(e)})
        }
