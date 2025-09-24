import os
import boto3
import sys
from pathlib import Path
from typing import Set, Dict
import hashlib

def calculate_md5(file_path: str) -> str:
    """Calculate MD5 hash of a file"""
    md5_hash = hashlib.md5()
    with open(file_path, "rb") as f:
        # Read the file in chunks to handle large files
        for chunk in iter(lambda: f.read(4096), b""):
            md5_hash.update(chunk)
    return md5_hash.hexdigest()

def get_local_files(local_path: str) -> Dict[str, str]:
    """
    Get a dictionary of all files in local directory with their MD5 hashes
    Returns: Dict[relative_path, md5_hash]
    """
    local_files = {}
    bucket_dir = Path(local_path)
    
    if not bucket_dir.exists():
        print(f"Error: {local_path} directory not found")
        sys.exit(1)
    
    for root, _, files in os.walk(bucket_dir):
        for file in files:
            local_file_path = Path(root) / file
            relative_path = str(local_file_path.relative_to(bucket_dir))
            local_files[relative_path] = calculate_md5(str(local_file_path))
            
    return local_files

def get_s3_files(s3_client, bucket_name: str) -> Set[str]:
    """
    Get a set of all files currently in the S3 bucket
    """
    s3_files = set()
    paginator = s3_client.get_paginator('list_objects_v2')
    
    try:
        for page in paginator.paginate(Bucket=bucket_name):
            if 'Contents' in page:
                for obj in page['Contents']:
                    s3_files.add(obj['Key'])
    except Exception as e:
        print(f"Error listing S3 bucket contents: {str(e)}")
        sys.exit(1)
        
    return s3_files

def sync_with_s3(local_path: str, bucket_name: str) -> None:
    """
    Sync contents of the DaveBucket folder with S3:
    - Upload new and modified files
    - Delete files that no longer exist locally
    """
    s3_client = boto3.client('s3')
    
    # Get current state of local files and S3 bucket
    local_files = get_local_files(local_path)
    s3_files = get_s3_files(s3_client, bucket_name)
    
    # Find files to upload (new or modified) and delete
    files_to_delete = s3_files - set(local_files.keys())
    
    # Upload new and modified files
    bucket_dir = Path(local_path)
    for relative_path, local_hash in local_files.items():
        local_file_path = bucket_dir / relative_path
        
        try:
            # Check if file exists in S3 and compare ETags (MD5 hashes)
            try:
                s3_obj = s3_client.head_object(Bucket=bucket_name, Key=relative_path)
                s3_hash = s3_obj['ETag'].strip('"')  # Remove quotes from ETag
                
                if s3_hash != local_hash:
                    print(f"File modified: {relative_path}")
                    s3_client.upload_file(str(local_file_path), bucket_name, relative_path)
            except s3_client.exceptions.ClientError:
                # File doesn't exist in S3, upload it
                print(f"New file: {relative_path}")
                s3_client.upload_file(str(local_file_path), bucket_name, relative_path)
                
        except Exception as e:
            print(f"Error processing {local_file_path}: {str(e)}")
            sys.exit(1)
    
    # Delete files that no longer exist locally
    for s3_key in files_to_delete:
        try:
            print(f"Deleting removed file: {s3_key}")
            s3_client.delete_object(Bucket=bucket_name, Key=s3_key)
        except Exception as e:
            print(f"Error deleting {s3_key}: {str(e)}")
            sys.exit(1)

def main():
    # Get the S3 bucket name from environment variable
    bucket_name = os.environ.get('S3_BUCKET')
    if not bucket_name:
        print("Error: S3_BUCKET environment variable not set")
        sys.exit(1)
    
    # Get the repository root directory
    repo_root = os.getcwd()
    dave_bucket_path = os.path.join(repo_root, 'DaveBucket')
    
    print(f"Starting sync with S3 bucket: {bucket_name}")
    sync_with_s3(dave_bucket_path, bucket_name)
    print("Sync completed successfully!")

if __name__ == '__main__':
    main()