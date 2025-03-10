import boto3
import sys
import os

# AWS Configuration
aws_region = os.getenv("AWS_REGION", "us-east-1")  # Double-check for typos in variable names!
lambda_role = os.getenv("LAMBDA_EXECUTION_ROLE_ARN")  # CORRECTED: Use Lambda-specific role
lambda_runtime = os.getenv("LAMBDA_RUNTIME", "python3.8")
lambda_handler = os.getenv("LAMBDA_HANDLER", "lambda_function.lambda_handler")

# Initialize client with explicit credential chain
lambda_client = boto3.client(
    "lambda",
    region_name=aws_region
)

def lambda_exists(function_name):
    """Check function existence with better error handling"""
    try:
        lambda_client.get_function(FunctionName=function_name)
        return True
    except lambda_client.exceptions.ResourceNotFoundException:
        return False
    except Exception as e:
        print(f"❌ Error checking function existence: {str(e)}")
        sys.exit(1)

def deploy_lambda(function_name, zip_file):
    """Deploy with comprehensive error handling"""
    try:
        with open(zip_file, "rb") as f:
            zip_data = f.read()
    except FileNotFoundError:
        print(f"🚫 Deployment package not found: {zip_file}")
        sys.exit(1)

    try:
        if lambda_exists(function_name):
            print(f"🔄 Updating {function_name}")
            response = lambda_client.update_function_code(
                FunctionName=function_name,
                ZipFile=zip_data
            )
            print(f"✅ Updated {function_name}. Version: {response['Version']}")
        else:
            if not lambda_role:
                print("🚨 Missing LAMBDA_EXECUTION_ROLE_ARN environment variable")
                sys.exit(1)

            print(f"🆕 Creating {function_name}")
            response = lambda_client.create_function(
                FunctionName=function_name,
                Runtime=lambda_runtime,
                Role=lambda_role,
                Handler=lambda_handler,
                Code={"ZipFile": zip_data},
                Publish=True
            )
            print(f"✅ Created {function_name}. ARN: {response['FunctionArn']}")
    except Exception as e:
        print(f"🔥 Deployment failed: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    # Validate inputs
    if len(sys.argv) != 3:
        print("❌ Usage: python3 deploy_lambda.py <function_name> <zip_file>")
        sys.exit(1)

    function_name, zip_file = sys.argv[1], sys.argv[2]
    print(f"🏁 Starting deployment for {function_name}")
    print(f"📦 Using package: {zip_file}")
    print(f"🌍 AWS Region: {aws_region}")
    print(f"🔑 IAM Role: {lambda_role}")

    deploy_lambda(function_name, zip_file)
    print("🎉 Deployment completed successfully")