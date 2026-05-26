"""
tools/slack_notify.py — Slack Block Kit alert builder for the cost anomaly agent.

Builds and posts one structured message per agent run covering all anomalies,
sorted by dollar delta descending.
"""

import json
import logging
import os
from typing import Any

import boto3
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

SLACK_SECRET_ARN = os.environ["SLACK_SECRET_ARN"]

# Cache the webhook URL within a Lambda execution environment
_WEBHOOK_URL_CACHE: str | None = None


def _get_webhook_url() -> str:
    """Retrieve the Slack webhook URL from Secrets Manager (cached)."""
    global _WEBHOOK_URL_CACHE
    if _WEBHOOK_URL_CACHE:
        return _WEBHOOK_URL_CACHE

    secretsmanager = boto3.client("secretsmanager")
    secret_value = secretsmanager.get_secret_value(SecretId=SLACK_SECRET_ARN)
    secret = json.loads(secret_value["SecretString"])
    _WEBHOOK_URL_CACHE = secret["webhook_url"]
    return _WEBHOOK_URL_CACHE


def _build_anomaly_section(
    anomaly: dict[str, Any],
    cause: str,
    suggestion: str,
) -> list[dict]:
    """Build Block Kit blocks for a single anomaly."""
    svc = anomaly["service"]
    team = anomaly["team"]
    today = anomaly["today_usd"]
    baseline = anomaly["baseline_usd"]
    delta = anomaly["delta_usd"]
    pct = anomaly["pct_change"]

    return [
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*{svc}*  ·  `{team}`\n"
                    f"Today: *${today:,.2f}*  (+{pct}% vs 7-day avg)\n"
                    f"Baseline: ${baseline:,.2f}/day  ·  Delta: *+${delta:,.2f}*"
                ),
            },
        },
        {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": f"*Likely cause*\n{cause}",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Suggested fix*\n{suggestion}",
                },
            ],
        },
    ]


def post_slack_alert(
    anomalies: list[dict[str, Any]],
    causes: list[str],
    suggestions: list[str],
    run_meta: dict[str, Any],
) -> dict[str, Any]:
    """
    Build and POST a Block Kit message to the #finops Slack channel.

    Args:
        anomalies:   List of anomaly dicts (service, team, today_usd, baseline_usd, delta_usd, pct_change).
        causes:      One cause string per anomaly, same order.
        suggestions: One fix suggestion per anomaly, same order.
        run_meta:    Dict with at minimum run_id and duration_seconds.

    Returns:
        Dict with keys: delivered (bool), status_code (int), error (str | None).
    """
    count = len(anomalies)
    run_id: str = run_meta.get("run_id", "unknown")
    duration: float = run_meta.get("duration_seconds", 0.0)
    tokens: int = run_meta.get("total_tokens", run_meta.get("token_count", 0))
    cost_usd = round(tokens * 0.000003, 4)  # rough estimate at Sonnet 4 pricing

    # Header block
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"⚠️ Cost Anomaly Detected — {count} service{'s' if count != 1 else ''}",
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Agent run `{run_id[:8]}` · {duration:.1f}s · {tokens:,} tokens · ~${cost_usd}",
                }
            ],
        },
    ]

    # One section per anomaly
    for anomaly, cause, suggestion in zip(anomalies, causes, suggestions):
        blocks.extend(_build_anomaly_section(anomaly, cause, suggestion))

    blocks.append({"type": "divider"})

    payload = json.dumps({"blocks": blocks}).encode("utf-8")
    webhook_url = _get_webhook_url()

    try:
        req = urllib.request.Request(
            webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            status_code: int = resp.status
            body: str = resp.read().decode("utf-8")

        if status_code == 200 and body == "ok":
            logger.info("Slack alert delivered", extra={"run_id": run_id, "anomaly_count": count})
            return {"delivered": True, "status_code": status_code, "error": None}
        else:
            logger.error("Slack returned unexpected response: %d %s", status_code, body, extra={"run_id": run_id})
            return {"delivered": False, "status_code": status_code, "error": body}

    except urllib.error.URLError as exc:
        logger.error("Slack POST failed: %s", exc, extra={"run_id": run_id}, exc_info=True)
        return {"delivered": False, "status_code": 0, "error": str(exc)}
