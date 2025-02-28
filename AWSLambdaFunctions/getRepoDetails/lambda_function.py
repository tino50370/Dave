import json
import urllib.request
import os
import boto3

# GitHub repository details
GITHUB_OWNER = "tino50370"  # Replace with your GitHub username/org
GITHUB_REPO = "Django-MVC"  # Replace with your repo name
BRANCH = "main"

GITHUB_TOKEN = os.getenv('GITHUB_TOKEN')  # Optional: Needed for private repos

# Initialize Bedrock client
bedrock_runtime = boto3.client("bedrock-runtime")

# Placeholder for Bedrock Agent ID
BEDROCK_AGENT_ID = "<YOUR_AGENT_ID>"

def list_files_from_github(path=""):
    """
    Recursively fetches all file paths from a GitHub repository.
    """
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{path}?ref={BRANCH}"
    
    headers = {}
    if GITHUB_TOKEN:
        headers['Authorization'] = f'token {GITHUB_TOKEN}'

    req = urllib.request.Request(url, headers=headers)
    
    try:
        with urllib.request.urlopen(req) as response:
            files_data = json.loads(response.read().decode())

        all_files = []

        for item in files_data:
            if item['type'] == 'file':
                all_files.append({"name": item['name'], "path": item['path']})
            elif item['type'] == 'dir':
                all_files.extend(list_files_from_github(item['path']))  # Recursive call for subdirectories

        return all_files

    except urllib.error.HTTPError as e:
        return {"error": f"GitHub API error: {e.code} - {e.reason}"}


def send_to_bedrock_agent(files):
    """
    Sends the structured list of files to an Amazon Bedrock agent for processing and returns the agent's response.
    """
    formatted_files = "\n".join([f"- {file['name']} (Path: {file['path']})" for file in files])
    request_body = {
        "inputText": f"Repository Owner: {GITHUB_OWNER}\nRepository: {GITHUB_REPO}\nBranch: {BRANCH}\n\nHere is the list of files:\n{formatted_files}\nPlease process them accordingly.",
        "githubToken": GITHUB_TOKEN if GITHUB_TOKEN else ""
    }

    try:
        response = bedrock_runtime.invoke_agent(
            agentId=BEDROCK_AGENT_ID,
            body=json.dumps(request_body)
        )
        
        response_body = json.loads(response["body"].read())
        return response_body

    except Exception as e:
        return {"error": str(e)}


def lambda_handler(event, context):
    """
    AWS Lambda function to fetch and return file names from a GitHub repository, then send them to Bedrock and return the agent's response.
    """
    try:
        files = list_files_from_github()
        
        if isinstance(files, dict) and "error" in files:
            return {"statusCode": 500, "body": json.dumps(files)}

        bedrock_response = send_to_bedrock_agent(files)
        
        return {
            'statusCode': 200,
            'body': json.dumps(bedrock_response)
        }

    except Exception as e:
        return {
            'statusCode': 500,
            'body': json.dumps({"error": str(e)})
        }
