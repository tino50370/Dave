# test_lambda.py
import lambda_function  # Import your Lambda function module
import json

# Mock AWS Lambda context (not used here but required for the handler)
class MockContext:
    pass

# Test event with parameters
test_event = {
    "body": {
        "GITHUB_OWNER": "tino50370",
        "GITHUB_REPO": "Django-MVC",
        "filePaths": ["README.md", "manage.py", "personalizedView/settings.py"]
    }
}

# Convert body to JSON string (as it would come from API Gateway)
test_event["body"] = json.dumps(test_event["body"])

# Execute the Lambda handler
response = lambda_function.lambda_handler(test_event, MockContext())

# Print formatted output
print("Status Code:", response["statusCode"])
print("Response Body:")
print(json.dumps(json.loads(response["body"]), indent=2))