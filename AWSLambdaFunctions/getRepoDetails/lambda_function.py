import requests

def get_github_files(owner, repo, token=None, path=''):
    headers = {'Authorization': f'token {token}'} if token else {}
    url = f'https://api.github.com/repos/{owner}/{repo}/contents/{path}'
    files = []
    
    while True:
        response = requests.get(url, headers=headers, params={'per_page': 100})
        response.raise_for_status()
        items = response.json()
        
        for item in items:
            if item['type'] == 'file':
                files.append(item['path'])
            elif item['type'] == 'dir':
                files.extend(get_github_files(owner, repo, token, item['path']))
        
        if 'next' in response.links:
            url = response.links['next']['url']
        else:
            break
            
    return files

def lambda_handler(event, context):
    # Extract parameters from the event
    owner = event.get('owner')
    repo = event.get('repo')
    token = event.get('token', None)
    path = event.get('path', '')
    
    if not owner or not repo:
        return {
            'statusCode': 400,
            'body': 'Missing required parameters: owner or repo'
        }
    
    try:
        files = get_github_files(owner, repo, token, path)
        return {
            'statusCode': 200,
            'body': {
                'count': len(files),
                'files': files
            }
        }
    except Exception as e:
        return {
            'statusCode': 500,
            'body': f'Error: {str(e)}'
        }