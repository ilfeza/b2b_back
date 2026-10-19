"""Create the local development image bucket without an external mc image."""

import json
import os
import time

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, ConnectionClosedError, EndpointConnectionError


def ensure_bucket(client, bucket: str, region: str = "us-east-1"):
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError as error:
        if error.response["Error"]["Code"] not in {"404", "NoSuchBucket", "NotFound"}:
            raise
        options = {"Bucket": bucket}
        if region != "us-east-1":
            options["CreateBucketConfiguration"] = {"LocationConstraint": region}
        try:
            client.create_bucket(**options)
        except ClientError as conflict:
            if conflict.response["Error"]["Code"] != "BucketAlreadyOwnedByYou":
                raise
    client.put_bucket_policy(
        Bucket=bucket,
        Policy=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": "*",
                        "Action": "s3:GetObject",
                        "Resource": f"arn:aws:s3:::{bucket}/*",
                    }
                ],
            }
        ),
    )


def main():
    region = os.environ.get("S3_REGION", "us-east-1")
    bucket = os.environ.get("S3_BUCKET", "handmade-media")
    client = boto3.client(
        "s3",
        endpoint_url=os.environ["S3_ENDPOINT"],
        aws_access_key_id=os.environ["S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["S3_SECRET_KEY"],
        region_name=region,
        config=Config(connect_timeout=2, read_timeout=3, retries={"max_attempts": 1}),
    )
    for attempt in range(60):
        try:
            ensure_bucket(client, bucket, region)
            print(f"Development image bucket ready: {bucket}")
            return
        except (EndpointConnectionError, ConnectionClosedError):
            if attempt == 59:
                raise
            time.sleep(1)


if __name__ == "__main__":
    main()
