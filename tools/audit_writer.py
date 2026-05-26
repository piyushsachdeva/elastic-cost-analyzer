"""
tools/audit_writer.py — Write a run audit record to Elasticsearch.

Every agent run writes one document to cost-anomaly-audit-YYYY.MM.dd,
regardless of whether anomalies were found or Slack delivered.
This provides a full audit trail for debugging, alerting on agent failures,
and tracking cost savings over time.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from tools.elastic_search import _get_es_client

logger = logging.getLogger(__name__)

AUDIT_INDEX_PREFIX = "cost-anomaly-audit"


def write_audit(
    run_id: str,
    anomalies_found: int,
    slack_delivered: bool,
    duration_seconds: float,
    token_count: int,
    error: str | None = None,
) -> dict[str, Any]:
    """
    Index one audit document for this agent run.

    Args:
        run_id:           UUID of this run.
        anomalies_found:  Number of cost anomalies detected.
        slack_delivered:  True if the Slack message was successfully posted.
        duration_seconds: Total wall-clock seconds for the run.
        token_count:      Total Bedrock tokens consumed (input + output).
        error:            Error message if the run failed; None on success.

    Returns:
        Dict with keys: indexed (bool), index (str), error (str | None).
    """
    now = datetime.now(timezone.utc)
    index_name = f"{AUDIT_INDEX_PREFIX}-{now.strftime('%Y.%m.%d')}"

    doc: dict[str, Any] = {
        "@timestamp": now.isoformat(),
        "run_id": run_id,
        "anomalies_found": anomalies_found,
        "slack_delivered": slack_delivered,
        "duration_seconds": round(duration_seconds, 2),
        "token_count": token_count,
        "estimated_cost_usd": round(token_count * 0.000003, 6),
        "status": "error" if error else "success",
    }
    if error:
        doc["error"] = error

    try:
        es = _get_es_client()
        resp = es.index(index=index_name, document=doc)
        result_id: str = resp.get("_id", "unknown")
        logger.info(
            "Audit record written",
            extra={
                "run_id": run_id,
                "index": index_name,
                "doc_id": result_id,
                "anomalies_found": anomalies_found,
            },
        )
        return {"indexed": True, "index": index_name, "doc_id": result_id, "error": None}

    except Exception as exc:
        # Audit write failure must never crash the Lambda — log and return error.
        logger.error(
            "Audit write failed: %s", exc,
            extra={"run_id": run_id, "index": index_name},
            exc_info=True,
        )
        return {"indexed": False, "index": index_name, "doc_id": None, "error": str(exc)}
