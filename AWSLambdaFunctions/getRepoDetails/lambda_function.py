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
        
        # Prepare data for Bedrock Agent
        bedrock_data = {
            "repository_metadata": {
                "owner": owner,
                "repo": repo,
                "branch": branch,
                "private": bool(token),
                "total_files": len(files)
            },
            "file_structure": files,
            "analysis_instructions": "Analyze this repository structure according to your predefined instructions."
        }
        
        # Initialize Bedrock client
        bedrock = boto3.client(service_name='bedrock-agent-runtime')
        
        # Send to Bedrock Agent (replace YOUR_AGENT_ID and YOUR_AGENT_ALIAS)
        response = bedrock.invoke_agent(
            agentId='QVOJVU85TW',
            agentAliasId='AXV71ARW9J',
            sessionId=context.aws_request_id,
            inputText=json.dumps(bedrock_data)
        )
        
        # Process Bedrock response
        result = ""
        for event in response.get('completion'):
            result += event['chunk']['bytes'].decode()
        
        return {
            'statusCode': 200,
            'body': {
                'bedrock_response': json.loads(result),
                'github_metadata': bedrock_data['repository_metadata']
            }
        }
        
    except Exception as e:
        return {
            'statusCode': 500,
            'body': f'Error: {str(e)}'
        }