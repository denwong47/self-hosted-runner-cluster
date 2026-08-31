"""Fetches GitHub App secrets from AWS. Runs host-side only -- never called
from inside a runner container, and the values it returns are kept in memory
for the lifetime of a single CLI invocation, never written to disk.
"""

from __future__ import annotations

import boto3

from ghrunner.config import GithubAppConfig


def fetch_private_key_pem(cfg: GithubAppConfig) -> str:
    s3 = boto3.client("s3", region_name=cfg.aws_region)
    obj = s3.get_object(Bucket=cfg.private_key_s3.bucket, Key=cfg.private_key_s3.key)
    return obj["Body"].read().decode("utf-8")


def fetch_client_secret(cfg: GithubAppConfig) -> str | None:
    if not cfg.client_secret_ssm_param:
        return None
    ssm = boto3.client("ssm", region_name=cfg.aws_region)
    param = ssm.get_parameter(Name=cfg.client_secret_ssm_param, WithDecryption=True)
    return param["Parameter"]["Value"]
