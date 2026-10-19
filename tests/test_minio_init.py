import json

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber
from moto import mock_aws

from scripts.minio_init import ensure_bucket


def test_bucket_init_is_idempotent_and_only_grants_public_object_reads():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        ensure_bucket(client, "handmade-media")
        client.put_object(Bucket="handmade-media", Key="image.png", Body=b"image")
        ensure_bucket(client, "handmade-media")
        assert (
            client.get_object(Bucket="handmade-media", Key="image.png")["Body"].read() == b"image"
        )
        policy = json.loads(client.get_bucket_policy(Bucket="handmade-media")["Policy"])
        assert policy["Statement"] == [
            {
                "Effect": "Allow",
                "Principal": "*",
                "Action": "s3:GetObject",
                "Resource": "arn:aws:s3:::handmade-media/*",
            }
        ]


def test_bucket_init_does_not_ignore_access_denied():
    client = boto3.client("s3", aws_access_key_id="test", aws_secret_access_key="test")
    with Stubber(client) as stub:
        stub.add_client_error("head_bucket", service_error_code="403", http_status_code=403)
        with pytest.raises(ClientError):
            ensure_bucket(client, "handmade-media")
        stub.assert_no_pending_responses()
