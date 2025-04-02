import json
import traceback

def lambda_handler(event, context):
    try:
        state = event.get('state', 'START')
        response_event = ''
        payload_data = {}
        
        # Parse input text
        input_text = json.loads(event.get('input', {}).get('text', '{}'))
        
        if state == 'START':
            # Initial analysis of repository structure
            repo_structure = input_text.get('repositoryStructure', [])
            
            payload_data = {
                "prompt": f"""Analyze this repository structure to generate a Dockerfile:
Repository Structure: {repo_structure}

Consider:
1. Programming language/framework detection
2. Build requirements
3. Dependency management files
4. Existing Docker-related files

If you need to inspect file contents, call read_Files with specific file paths.""",
                "modelParameters": {
                    "temperature": 0.1,
                    "topP": 0.9
                }
            }
            response_event = 'INVOKE_MODEL'

        elif state == 'MODEL_INVOKED':
            model_response = input_text
            
            if model_response.get('stopReason') == 'tool_use' and model_response.get('toolName') == 'read_Files':
                # Handle file reading request
                payload_data = {
                    "tool": "read_Files",
                    "parameters": {
                        "filePaths": model_response.get('parameters', {}).get('filePaths', [])
                    }
                }
                response_event = 'INVOKE_TOOL'
                
            elif model_response.get('stopReason') == 'end_turn':
                # Final Dockerfile output
                payload_data = {
                    "text": f"Final Dockerfile:\n{model_response.get('dockerfile', '')}",
                    "isFinal": True
                }
                response_event = 'FINISH'
                
            else:
                raise ValueError("Unexpected model response")

        elif state == 'TOOL_INVOKED':
            # Process file contents from read_Files tool
            file_contents = input_text.get('fileContents', {})
            
            payload_data = {
                "prompt": f"""File contents received:
{json.dumps(file_contents, indent=2)}

Generate a Dockerfile considering:
1. Appropriate base image
2. Dependency installation steps
3. Build process configuration
4. Security best practices
5. Runtime optimization""",
                "modelParameters": {
                    "temperature": 0.1,
                    "topP": 0.9
                }
            }
            response_event = 'INVOKE_MODEL'

        else:
            raise ValueError(f"Unhandled state: {state}")

        return {
            "statusCode": 200,
            "body": json.dumps({
                "actionEvent": response_event,
                "output": {
                    "text": json.dumps(payload_data),
                    "trace": {
                        "event": {
                            "text": f"State transition: {state} → {response_event}"
                        }
                    }
                },
                "context": event.get('context', {})
            })
        }

    except Exception as e:
        return {
            "statusCode": 500,
            "body": json.dumps({
                "error": f"Orchestration error: {str(e)}",
                "stackTrace": str(traceback.format_exc())
            })
        }