import requests
import boto3
import json
import os

def get_github_files(owner, repo, branch='main', token=None, path=''):
    """Fetch all files from a GitHub repository with branch support."""
    headers = {'Authorization': f'token {token}'} if token else {}
    base_url = f'https://api.github.com/repos/{owner}/{repo}/contents/{path}'
    files = []
    
    params = {'ref': branch, 'per_page': 100}
    
    while True:
        response = requests.get(base_url, headers=headers, params=params)
        response.raise_for_status()
        items = response.json()
        
        for item in items:
            if item['type'] == 'file':
                files.append(item['path'])
            elif item['type'] == 'dir':
                files.extend(get_github_files(owner, repo, branch, token, item['path']))
        
        if 'next' in response.links:
            base_url = response.links['next']['url']
            params = {}  # Parameters are included in next URL
        else:
            break
            
    return files

def lambda_handler(event, context):
    # Extract parameters from event or environment variables
    owner = event.get('GITHUB_OWNER') or os.environ.get('GITHUB_OWNER')
    repo = event.get('GITHUB_REPO') or os.environ.get('GITHUB_REPO')
    branch = event.get('BRANCH') or os.environ.get('BRANCH', 'main')
    token = event.get('GITHUB_TOKEN') or os.environ.get('GITHUB_TOKEN')
    
    if not owner or not repo:
        return {
            'statusCode': 400,
            'body': 'Missing required parameters: GITHUB_OWNER and GITHUB_REPO'
        }
    
    try:
        # Get repository structure
        files = get_github_files(owner, repo, branch, token)
        
        # Prepare data for response
        repo_details = {
            "repository_metadata": {
                "GITHUB_OWNER": owner,
                "GITHUB_REPO": repo,
                "BRANCH": branch,
                "private": bool(token),
                "total_files": len(files)
            },
            "file_structure": files
        }
        
        return {
            'body': json.dumps(repo_details)
        }
        
    except Exception as e:
        return {
            'statusCode': 500,
            'body': f'Error: {str(e)}'
        }