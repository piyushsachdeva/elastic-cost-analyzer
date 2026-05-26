# Design — Cloud Cost Anomaly Agent

## Architecture Overview

```
EventBridge (cron 0 8 * * ? *)
  → Lambda  (thin host — creates agent, calls run())
      → Bedrock Converse API  (Claude Sonnet 4, max 20 iterations)
          ↔ Tool dispatcher  (routes tool calls to Python functions)
              → Elasticsearch  (3 indices)
                  aws-billing-*          ← daily cost data per service
                  deploy-events-*        ← CI/CD deploy records
                  cost-anomaly-audit-*   ← agent writes one doc per run
              → Slack  (Block Kit message, only when anomalies found)
      ← AWS Secrets Manager  (elastic-creds + slack-webhook, cached in module)
```

## Component Design

### Lambda (agent.py)
- Single class: `CloudCostAnomalyAgent`
- Constructor: creates Bedrock client, initialises run_id and start_time
- `run()`: main converse loop — builds initial message, iterates until `end_turn` or max iterations
- `_invoke_bedrock()`: calls `bedrock.converse()` with system prompt, messages, and tool definitions
- `_process_tool_calls(content_blocks)`: extracts `toolUse` blocks, dispatches each, returns `toolResult` blocks
- `_dispatch_tool(name, input)`: routes to the correct tool function by name
- `lambda_handler(event, context)`: AWS entry point — creates agent, calls `run()`, returns result

**Bedrock message flow:**
```
user: "Analyse AWS cost anomalies for 2025-05-25. Spike threshold: 25%."
  → assistant: toolUse(find_spike_services, {threshold_pct: 25.0})
  → user: toolResult([{service: "Amazon EC2", pct_change: 43.1, ...}])
  → assistant: toolUse(get_cost_timeseries, {service: "Amazon EC2", hours: 48})
  → user: toolResult([{timestamp: "...", cost_usd: 44.01}, ...])
  → assistant: toolUse(find_deploys_near_spike, {service: "checkout", spike_start_iso: "...", window_hours: 12})
  → user: toolResult([{version: "v2.3.1", deployed_by: "alice@acme.com", ...}])
  → assistant: toolUse(post_slack_alert, {anomalies: [...], causes: [...], suggestions: [...]})
  → user: toolResult({delivered: true, ts: "..."})
  → assistant: toolUse(write_audit, {run_id: "...", anomalies_found: 1, slack_delivered: true, ...})
  → user: toolResult({indexed: true})
  → assistant: end_turn "Done."
```

### Tool Functions (tools/)

#### elastic_search.py
```python
def find_spike_services(threshold_pct: float) -> list[dict]:
    """
    Returns list of dicts: {service, team, today_usd, baseline_usd, delta_usd, pct_change}
    Queries aws-billing-* with date_histogram agg for today vs 7-day avg per service.
    Empty list = no anomalies.
    """

def get_cost_timeseries(service: str, hours: int) -> list[dict]:
    """
    Returns [{timestamp: ISO str, cost_usd: float}, ...] for the past `hours` hours.
    Hourly granularity. Used to pinpoint spike start hour.
    """

def find_deploys_near_spike(service: str, spike_start_iso: str, window_hours: int = 12) -> list[dict]:
    """
    Returns [{service, version, team, deployed_by, commit_sha, timestamp, hours_before_spike}, ...]
    Queries deploy-events-* within ±window_hours of spike_start_iso.
    Empty list if no deploy found.
    """
```

ES client is a module-level singleton:
```python
_es_client: Elasticsearch | None = None

def _get_es_client() -> Elasticsearch:
    global _es_client
    if _es_client is None:
        secret = _get_secret(os.environ["ELASTIC_SECRET_ARN"])
        _es_client = Elasticsearch(hosts=[secret["es_url"]], api_key=secret["es_api_key"])
    return _es_client
```

#### slack_notify.py
```python
def post_slack_alert(anomalies: list[dict], causes: list[str],
                     suggestions: list[str], run_meta: dict) -> dict:
    """
    Builds a Block Kit message and POSTs to the Slack webhook URL from Secrets Manager.
    Returns {"delivered": bool, "ts": str, "error": str | None}
    Only called when anomalies is non-empty.
    """
```

Block Kit structure:
- Header block: "⚠️ Cost Anomaly Detected — N service(s)"
- For each anomaly: section with service/team/cost/delta, cause, suggestion, deploy info
- Footer: run duration, token count, estimated cost

#### audit_writer.py
```python
def write_audit(run_id: str, anomalies_found: int, slack_delivered: bool,
                duration_seconds: float, token_count: int, error: str | None = None) -> dict:
    """
    Indexes one document to cost-anomaly-audit-YYYY.MM.dd.
    Returns {"indexed": bool, "index": str, "doc_id": str, "error": str | None}
    """
```

### Elasticsearch Index Schemas

**aws-billing-*** (written by Elastic Agent / seed script):
```json
{
  "@timestamp": "2025-05-25T17:00:00Z",
  "aws.billing.service_name": "Amazon EC2",
  "aws.billing.billed_cost_amount": 44.0,
  "tags": {"team": "checkout-team"},
  "aws.billing.currency": "USD"
}
```

**deploy-events-*** (written by CI/CD pipeline):
```json
{
  "@timestamp": "2025-05-25T14:00:00Z",
  "service": "checkout",
  "version": "v2.3.1",
  "team": "checkout-team",
  "deployed_by": "alice@acme.com",
  "commit_sha": "a3f9c12d",
  "environment": "production"
}
```

**cost-anomaly-audit-*** (written by this agent):
```json
{
  "@timestamp": "2025-05-25T08:14:22Z",
  "run_id": "uuid-v4",
  "anomalies_found": 1,
  "slack_delivered": true,
  "duration_seconds": 14.2,
  "token_count": 4380,
  "error": null
}
```

## Agent Reasoning System Prompt (key sections)

The system prompt instructs Claude to follow a strict 7-step sequence:
1. `find_spike_services(threshold_pct)` — if empty, write audit and stop
2. `get_cost_timeseries(service, hours=48)` — identify spike start hour
3. `find_deploys_near_spike(service, spike_start_iso, window_hours=12)` — correlate
4. Reason: one sentence cause, under 30 words, cite specific technical reason
5. Reason: one actionable fix, under 25 words, with dollar estimate
6. `post_slack_alert(anomalies, causes, suggestions, run_meta)`
7. `write_audit(...)` — always, even if Slack failed

## Security Design
- Zero credentials in code — all via Secrets Manager
- IAM role uses least-privilege inline policy:
  - `bedrock:Converse` + `bedrock:InvokeModel` on `*`
  - `secretsmanager:GetSecretValue` scoped to the two specific secret ARNs only
  - `aws-marketplace:ViewSubscriptions` (required for Bedrock first-time subscription check)
- ES API key has read-only access on `aws-billing-*` and `deploy-events-*`
- ES API key has create/index access on `cost-anomaly-audit-*` only
