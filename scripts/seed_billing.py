#!/usr/bin/env python3
"""
scripts/seed_billing.py — Seed metrics-aws.billing-* with demo data.

Seeds 7 days of baseline EC2 costs (~$25/hr) plus today's spike
(~$44/hr starting at 17:00 UTC) so the agent detects an anomaly.

Uses the same field schema as the Elastic AWS Billing integration
(aws.billing.ServiceName, aws.billing.UnblendedCost.amount) so the
agent code is identical whether reading real or seeded data.

Usage:
    python3 scripts/seed_billing.py \
        --es-url https://your-project.es.us-east-1.aws.elastic.cloud \
        --api-key YOUR_BASE64_API_KEY
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

import urllib.request
import urllib.error


def post_doc(es_url: str, api_key: str, index: str, doc: dict) -> bool:
    url = f"{es_url}/{index}/_doc"
    data = json.dumps(doc).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"ApiKey {api_key}",
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status in (200, 201)
    except urllib.error.HTTPError as e:
        print(f"  ERROR {e.code}: {e.read().decode()[:200]}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="Seed aws-billing-* demo data")
    parser.add_argument("--es-url", required=True, help="Elasticsearch URL")
    parser.add_argument("--api-key", required=True, help="Base64 API key")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    services = [
        {"name": "Amazon EC2", "team": "checkout-team", "base_hourly": 25.0},
        {"name": "Amazon RDS", "team": "data-team", "base_hourly": 8.5},
        {"name": "AWS Lambda", "team": "platform-team", "base_hourly": 1.2},
        {"name": "Amazon S3", "team": "data-team", "base_hourly": 0.8},
    ]

    docs_written = 0

    # 7 days of baseline data (past 7 days, hourly)
    print("Seeding 7-day baseline...")
    for day_offset in range(7, 0, -1):
        day_start = today - timedelta(days=day_offset)
        index = f"metrics-aws.billing-{day_start.strftime('%Y.%m.%d')}"
        for hour in range(24):
            ts = day_start + timedelta(hours=hour)
            for svc in services:
                import random
                noise = random.uniform(0.95, 1.05)
                doc = {
                    "@timestamp": ts.isoformat(),
                    "aws.billing.ServiceName": svc["name"],
                    "aws.billing.UnblendedCost.amount": round(svc["base_hourly"] * noise, 4),
                    "aws.billing.UnblendedCost.unit": "USD",
                    "tags": {"team": svc["team"]},
                    "cloud.provider": "aws",
                    "event.dataset": "aws.billing",
                }
                if post_doc(args.es_url, args.api_key, index, doc):
                    docs_written += 1
        print(f"  {index}: {len(services) * 24} docs")

    # Today: full 24h with EC2 spiking all day (~$44/hr vs $25/hr baseline = 76% above)
    # Seeding all 24 hours ensures the daily-total comparison fires regardless of run time.
    print(f"Seeding today ({today.strftime('%Y-%m-%d')}) with EC2 spike all day...")
    index = f"metrics-aws.billing-{today.strftime('%Y.%m.%d')}"
    spike_hour = 0
    for hour in range(24):
        ts = today + timedelta(hours=hour)
        for svc in services:
            import random
            if svc["name"] == "Amazon EC2" and hour >= spike_hour:
                amount = round(44.0 + random.uniform(-0.5, 0.5), 4)
            else:
                amount = round(svc["base_hourly"] * random.uniform(0.95, 1.05), 4)
            doc = {
                "@timestamp": ts.isoformat(),
                "aws.billing.ServiceName": svc["name"],
                "aws.billing.UnblendedCost.amount": amount,
                "aws.billing.UnblendedCost.unit": "USD",
                "tags": {"team": svc["team"]},
                "cloud.provider": "aws",
                "event.dataset": "aws.billing",
            }
            if post_doc(args.es_url, args.api_key, index, doc):
                docs_written += 1

    print(f"\n✅ Done — {docs_written} documents written to metrics-aws.billing-*")
    print(f"   EC2 spike starts at {today.strftime('%Y-%m-%d')}T17:00:00Z")
    print(f"   Expected pct_change: ~43% above 7-day baseline")
    print(f"\nVerify in Kibana → Discover → metrics-aws.billing-*")


if __name__ == "__main__":
    main()
