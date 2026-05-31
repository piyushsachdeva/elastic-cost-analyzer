"""
tools/elastic_search.py — Elasticsearch query functions for the cost anomaly agent.

All five functions used by the Bedrock tool-calling loop:
  - get_7day_baseline      : rolling 7-day average spend per service
  - get_todays_cost        : today's spend since midnight UTC
  - find_spike_services    : compare today vs baseline, return anomalies
  - get_cost_timeseries    : hourly cost for spike-start pinpointing
  - find_deploys_near_spike: correlate a spike with deploy events
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

import boto3
from elasticsearch import Elasticsearch

logger = logging.getLogger(__name__)

ELASTIC_SECRET_ARN = os.environ["ELASTIC_SECRET_ARN"]
BILLING_INDEX = "metrics-aws.billing-*"
DEPLOY_INDEX = "deploy-events-*"


# ---------------------------------------------------------------------------
# Client — lazy-initialised and cached for Lambda container reuse
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _get_es_client() -> Elasticsearch:
    """
    Retrieve Elasticsearch credentials from Secrets Manager and return
    an authenticated client. Called once per Lambda execution environment.
    """
    secretsmanager = boto3.client("secretsmanager")
    secret_value = secretsmanager.get_secret_value(SecretId=ELASTIC_SECRET_ARN)
    secret = json.loads(secret_value["SecretString"])

    es_url: str = secret["es_url"]
    api_key: str = secret["es_api_key"]

    client = Elasticsearch(
        es_url,
        api_key=api_key,
        request_timeout=30,
        retry_on_timeout=True,
        max_retries=3,
    )
    logger.info("Elasticsearch client initialised for %s", es_url)
    return client


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _midnight_utc() -> str:
    """Return today's midnight in ISO 8601 UTC."""
    now = datetime.now(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.isoformat()


def _days_ago_utc(days: int) -> str:
    """Return the ISO 8601 UTC timestamp N days ago."""
    ts = datetime.now(timezone.utc) - timedelta(days=days)
    return ts.isoformat()


# ---------------------------------------------------------------------------
# Public tool functions
# ---------------------------------------------------------------------------
def get_7day_baseline(service: str | None = None) -> dict[str, float]:
    """
    Calculate the 7-day rolling average daily spend per AWS service.

    Args:
        service: If provided, return only this service's baseline.
                 If None, return all services.

    Returns:
        Dict mapping service name → average daily USD spend.
    """
    es = _get_es_client()
    query: dict[str, Any] = {
        "range": {
            "@timestamp": {
                "gte": _days_ago_utc(7),
                "lt": _midnight_utc(),
            }
        }
    }
    if service:
        query = {
            "bool": {
                "filter": [
                    {"range": {"@timestamp": {"gte": _days_ago_utc(7), "lt": _midnight_utc()}}},
                    {"term": {"aws.billing.ServiceName": service}},
                ]
            }
        }

    body = {
        "size": 0,
        "query": query,
        "aggs": {
            "by_service": {
                "terms": {"field": "aws.billing.ServiceName", "size": 50},
                "aggs": {
                    "by_day": {
                        "date_histogram": {"field": "@timestamp", "calendar_interval": "day"},
                        "aggs": {"daily_cost": {"sum": {"field": "aws.billing.UnblendedCost.amount"}}},
                    },
                    "avg_daily_cost": {"avg_bucket": {"buckets_path": "by_day>daily_cost"}},
                },
            }
        },
    }

    response = es.search(index=BILLING_INDEX, body=body)
    result: dict[str, float] = {}
    for bucket in response["aggregations"]["by_service"]["buckets"]:
        svc = bucket["key"]
        avg = bucket["avg_daily_cost"]["value"] or 0.0
        result[svc] = round(avg, 4)

    logger.info("7-day baseline computed for %d services", len(result))
    return result


def get_todays_cost(service: str | None = None) -> dict[str, float]:
    """
    Get today's spend (since midnight UTC) per AWS service.

    Args:
        service: If provided, return only this service's cost.

    Returns:
        Dict mapping service name → today's USD spend.
    """
    es = _get_es_client()
    query: dict[str, Any] = {"range": {"@timestamp": {"gte": _midnight_utc()}}}
    if service:
        query = {
            "bool": {
                "filter": [
                    {"range": {"@timestamp": {"gte": _midnight_utc()}}},
                    {"term": {"aws.billing.ServiceName": service}},
                ]
            }
        }

    body = {
        "size": 0,
        "query": query,
        "aggs": {
            "by_service": {
                "terms": {"field": "aws.billing.ServiceName", "size": 50},
                "aggs": {"today_cost": {"sum": {"field": "aws.billing.UnblendedCost.amount"}}},
            }
        },
    }

    response = es.search(index=BILLING_INDEX, body=body)
    result: dict[str, float] = {}
    for bucket in response["aggregations"]["by_service"]["buckets"]:
        result[bucket["key"]] = round(bucket["today_cost"]["value"] or 0.0, 4)

    return result


def find_spike_services(threshold_pct: float) -> list[dict[str, Any]]:
    """
    Compare today's spend against the 7-day rolling average and return
    services that exceed the threshold.

    Args:
        threshold_pct: Spike threshold as a percentage (e.g. 25.0 = 25% above baseline).

    Returns:
        List of anomaly dicts, each containing:
          service, team, today_usd, baseline_usd, delta_usd, pct_change
        Sorted by delta_usd descending. Empty list if no anomalies.
    """
    baseline = get_7day_baseline()
    today = get_todays_cost()
    es = _get_es_client()

    # Fetch team tag per service from today's billing documents
    team_query = {
        "size": 0,
        "query": {"range": {"@timestamp": {"gte": _midnight_utc()}}},
        "aggs": {
            "by_service": {
                "terms": {"field": "aws.billing.ServiceName", "size": 50},
                "aggs": {
                    "top_tag": {
                        "top_hits": {
                            "size": 1,
                            "_source": ["tags.team", "aws.billing.ServiceName"],
                        }
                    }
                },
            }
        },
    }
    team_resp = es.search(index=BILLING_INDEX, body=team_query)
    team_map: dict[str, str] = {}
    for bucket in team_resp["aggregations"]["by_service"]["buckets"]:
        hits = bucket["top_tag"]["hits"]["hits"]
        if hits:
            src = hits[0].get("_source", {})
            team_map[bucket["key"]] = src.get("tags", {}).get("team", "unknown-team")

    anomalies: list[dict[str, Any]] = []
    for svc, today_usd in today.items():
        base_usd = baseline.get(svc, 0.0)
        if base_usd == 0:
            continue
        pct_change = ((today_usd - base_usd) / base_usd) * 100
        if pct_change >= threshold_pct:
            anomalies.append({
                "service": svc,
                "team": team_map.get(svc, "unknown-team"),
                "today_usd": round(today_usd, 2),
                "baseline_usd": round(base_usd, 2),
                "delta_usd": round(today_usd - base_usd, 2),
                "pct_change": round(pct_change, 1),
            })

    anomalies.sort(key=lambda a: a["delta_usd"], reverse=True)
    logger.info("Spike detection complete: %d anomalies found", len(anomalies))
    return anomalies


def get_cost_timeseries(service: str, hours: int) -> list[dict[str, Any]]:
    """
    Get hourly cost data for a service over the past N hours.
    Used to pinpoint the exact hour a spike started.

    Args:
        service: AWS service name (e.g. 'Amazon EC2').
        hours:   Number of hours of history to retrieve (e.g. 48).

    Returns:
        List of hourly buckets: [{"timestamp": "...", "cost_usd": 12.34}, ...]
        Sorted oldest-first.
    """
    es = _get_es_client()
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

    body = {
        "size": 0,
        "query": {
            "bool": {
                "filter": [
                    {"range": {"@timestamp": {"gte": since}}},
                    {"term": {"aws.billing.ServiceName": service}},
                ]
            }
        },
        "aggs": {
            "by_hour": {
                "date_histogram": {"field": "@timestamp", "fixed_interval": "1h"},
                "aggs": {"hourly_cost": {"sum": {"field": "aws.billing.UnblendedCost.amount"}}},
            }
        },
    }

    response = es.search(index=BILLING_INDEX, body=body)
    result = [
        {
            "timestamp": bucket["key_as_string"],
            "cost_usd": round(bucket["hourly_cost"]["value"] or 0.0, 4),
        }
        for bucket in response["aggregations"]["by_hour"]["buckets"]
    ]
    logger.info("Timeseries for '%s': %d hourly buckets returned", service, len(result))
    return result


def find_deploys_near_spike(
    service: str,
    spike_start_iso: str,
    window_hours: int = 12,
) -> list[dict[str, Any]]:
    """
    Search the deploy-events index for deployments to `service` within
    ±window_hours of the spike start time.

    Args:
        service:         Service name as stored in deploy-events (e.g. 'checkout').
        spike_start_iso: ISO 8601 UTC timestamp of spike start.
        window_hours:    Search window in hours before and after spike_start_iso.

    Returns:
        List of deploy dicts, sorted by timestamp desc. Each contains:
          service, version, team, deployed_by, commit_sha, timestamp, hours_before_spike.
        Empty list if no matching deploys found.
    """
    es = _get_es_client()

    spike_dt = datetime.fromisoformat(spike_start_iso.replace("Z", "+00:00"))
    window_start = (spike_dt - timedelta(hours=window_hours)).isoformat()
    window_end = (spike_dt + timedelta(hours=window_hours)).isoformat()

    body = {
        "size": 10,
        "query": {
            "bool": {
                "filter": [
                    {"term": {"service.keyword": service}},
                    {"range": {"@timestamp": {"gte": window_start, "lte": window_end}}},
                ]
            }
        },
        "sort": [{"@timestamp": {"order": "desc"}}],
    }

    response = es.search(index=DEPLOY_INDEX, body=body)
    deploys: list[dict[str, Any]] = []
    for hit in response["hits"]["hits"]:
        src = hit["_source"]
        deploy_dt = datetime.fromisoformat(src["@timestamp"].replace("Z", "+00:00"))
        hours_before = round((spike_dt - deploy_dt).total_seconds() / 3600, 1)
        deploys.append({
            "service": src.get("service", service),
            "version": src.get("version", "unknown"),
            "team": src.get("team", "unknown"),
            "deployed_by": src.get("deployed_by", "unknown"),
            "commit_sha": src.get("commit_sha", "unknown"),
            "timestamp": src.get("@timestamp"),
            "hours_before_spike": hours_before,
        })

    logger.info("Deploy lookup for '%s': %d deploys found near spike at %s", service, len(deploys), spike_start_iso)
    return deploys
