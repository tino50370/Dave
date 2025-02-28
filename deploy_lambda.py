import boto3
import sys
import os

# Fetch environment variables from GitHub Secrets
#testing
aws_region = os.getenv("AWS_REGION", "us-east-1")  # Default to us-east-1 if not set
lambda_role = os.getenv("AWS_ROLE_ARN")  # IAM Role ARN is passed from GitHub Secrets
lambda_runtime = os.getenv("LAMBDA_RUNTIME", "python3.8")  # Default runtime
lambda_handler = os.getenv("LAMBDA_HANDLER", "lambda_function.lambda_handler")  # Default handler

lambda_client = boto3.client("lambda", region_name=aws_region)

def lambda_exists(function_name):
    """
    Checks if the AWS Lambda function already exists.
    """
    try:
        lambda_client.get_function(FunctionName=function_name)
        return True
    except lambda_client.exceptions.ResourceNotFoundException:
        return False

def deploy_lambda(function_name, zip_file):
    """
    Deploys the Lambda function. If the function does not exist, it creates a new one.
    Otherwise, it updates the existing function.
    """
    with open(zip_file, "rb") as f:
        zip_data = f.read()

    if lambda_exists(function_name):
        print(f"Updating existing Lambda function: {function_name}")
        lambda_client.update_function_code(
            FunctionName=function_name,
            ZipFile=zip_data
        )
    else:
        print(f"Creating new Lambda function: {function_name}")
        lambda_client.create_function(
            FunctionName=function_name,
            Runtime=lambda_runtime,
            Role=lambda_role,
            Handler=lambda_handler,
            Code={"ZipFile": zip_data},
            Publish=True
        )

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python3 deploy_lambda.py <function_name> <zip_file>")
        sys.exit(1)

    function_name, zip_file = sys.argv[1], sys.argv[2]

    # IMPORTANT: The function_name should match the folder name in AWSLambdaFunctions/
    # If the folder is named incorrectly, the deployment will fail.
    deploy_lambda(function_name, zip_file)