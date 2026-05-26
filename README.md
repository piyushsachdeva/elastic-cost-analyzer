# Cloud Cost Anomaly Agent

An AI agent that runs daily, detects AWS cost spikes, correlates them with recent
deployments, and posts a root-cause Slack alert to `#finops` — automatically.

Built with **Amazon Bedrock** (Claude Sonnet 4), **Elastic Cloud Serverless**, and
**AWS Lambda**. Zero manual triage. ~$3–5/month to run.

---

## How it works

```
EventBridge (cron 0 8 * * ? *)
  → Lambda  (thin host — creates agent, calls run())
      → Bedrock  (Claude Sonnet 4 converse loop, max 20 iterations)
          ↔ Elasticsearch
              aws-billing-*          ← Elastic Agent fills automatically
              deploy-events-*        ← your CI/CD pipeline writes one doc/deploy
              cost-anomaly-audit-*   ← agent writes after every run
          → Slack  (Block Kit message, only when anomalies are found)
      ← Elastic Observability  (OTel traces → Kibana APM, zero instrumentation)
```

The agent follows a fixed 7-step reasoning sequence every run:

1. Detect services where today's spend exceeds the 7-day baseline by ≥ 25 %
2. Pull hourly cost data to pinpoint when the spike started
3. Look up deployments near the spike start time
4. Write a one-sentence root cause
5. Write one actionable fix with an estimated dollar saving
6. Post a Slack Block Kit message (only if anomalies found)
7. Write an audit record to Elasticsearch (always — even if Slack fails)

---

## Repository layout

```
agent.py                  Lambda handler + Bedrock converse loop
tools/
  elastic_search.py       Five ES query functions (billing + deploy lookups)
  slack_notify.py         Slack Block Kit builder
  audit_writer.py         Audit record writer
  __init__.py
scripts/
  seed_billing.py         Seeds aws-billing-* with 7-day baseline + today's spike
tests/
  test_integration.py     8 integration tests — fully mocked, no real AWS calls
requirements.txt          Pinned Python deps
Dockerfile                Multi-arch Lambda container image (amd64 + arm64)
Makefile                  build / test / zip / deploy helpers
.env.example              Environment variable template
demo.md                   Full setup guide + video recording script (start here)
```

---

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | 3.12+ | matches Lambda runtime |
| AWS CLI | v2 | configured with credentials |
| AWS account | — | Bedrock model access needed (free to request) |
| Elastic Cloud | Serverless | subscribe via AWS Marketplace |
| Slack workspace | — | incoming webhooks enabled |

---

## Setup

All setup instructions are in **[`demo.md`](demo.md)** — it covers both the full infrastructure setup guide and the video recording script in a single file. Follow the "FULL INFRASTRUCTURE SETUP" section at the top.

| Step | Service | What you create |
|---|---|---|
| 1 | Elastic Cloud | Serverless Observability project |
| 2 | Kibana Dev Tools | `deploy-events-*` + `aws-billing-*` index + seed data |
| 3 | Kibana Stack Mgmt | API key `cost-anomaly-agent` |
| 4 | Slack | Incoming webhook → `#finops` |
| 5 | AWS Secrets Manager | `elastic-creds` + `slack-webhook` secrets |
| 6 | AWS IAM | `cost-anomaly-agent-lambda-role` + inline policy |
| 7 | Amazon Bedrock | Claude Sonnet 4.5 model access (use case form) |
| 8 | AWS Lambda | `cost-anomaly-agent` function (Python 3.12) |
| 9 | Amazon EventBridge | Daily cron rule (08:00 UTC) |
| 10 | Verification | End-to-end smoke test |

---

## Local development

### 1. Install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Run tests (no AWS credentials needed)

```bash
make test
```

Expected: **8 tests pass** in ~2 seconds. All AWS and Elastic calls are mocked.

```
tests/test_integration.py::TestAgentIntegration::test_full_chain_completes_successfully    PASSED
tests/test_integration.py::TestAgentIntegration::test_find_spike_services_called_once      PASSED
tests/test_integration.py::TestAgentIntegration::test_timeseries_called_for_spiked_service PASSED
tests/test_integration.py::TestAgentIntegration::test_deploy_lookup_called_with_correct_service PASSED
tests/test_integration.py::TestAgentIntegration::test_slack_message_contains_required_fields    PASSED
tests/test_integration.py::TestAgentIntegration::test_audit_written_on_success             PASSED
tests/test_integration.py::TestAgentIntegration::test_no_anomalies_exits_silently          PASSED
tests/test_integration.py::TestAgentIntegration::test_lambda_handler_returns_200           PASSED
```

### 3. Build and upload the Lambda zip

```bash
make zip
```

This produces `cost-anomaly-agent.zip` (≈ 6 MB). Upload it in the Lambda console:
**Code** tab → **Upload from** → **.zip file** → confirm handler is `agent.lambda_handler`.

### 4. Run locally against real AWS (optional)

Copy `.env.example` to `.env.local`, fill in real values, then:

```bash
make run-local
```

This builds the Docker image and invokes the Lambda runtime locally. Hits real
Bedrock and real Elasticsearch — use only after infrasetup.md is complete.

---

## Environment variables

All set on the Lambda function. See `infrasetup.md §8` for where to find each value.

| Variable | Description |
|---|---|
| `ELASTIC_SECRET_ARN` | ARN of the `cost-anomaly-agent/elastic-creds` secret |
| `SLACK_SECRET_ARN` | ARN of the `cost-anomaly-agent/slack-webhook` secret |
| `AWS_BEDROCK_REGION` | Region where Bedrock is used (e.g. `us-east-1`) |
| `SPIKE_THRESHOLD_PCT` | Spike threshold percentage, default `25.0` |
| `AGENT_MAX_ITERATIONS` | Bedrock converse loop limit, default `20` |
| `BEDROCK_MODEL_ID` | Optional override, default `anthropic.claude-sonnet-4-5` |

---

## Verifying the end-to-end setup

After completing infrasetup.md:

**1. Trigger the Lambda manually**

AWS Console → Lambda → `cost-anomaly-agent` → **Test** tab → use event:
```json
{"source": "manual-test"}
```

Expected response:
```json
{
  "statusCode": 200,
  "body": {
    "run_id": "...",
    "duration_seconds": 14.2,
    "total_tokens": 4380
  }
}
```

**2. Confirm Slack message**

Slack → `#finops` → new Block Kit message present (only if a cost spike was detected).

**3. Confirm audit record**

Kibana → Dev Tools:
```http
GET cost-anomaly-audit-*/_search
{
  "sort": [{"@timestamp": {"order": "desc"}}],
  "size": 1
}
```

**4. Confirm OTel trace**

Kibana → Observability → APM → Services → `cost-anomaly-agent` → latest transaction.

---

## Cost estimate

| Component | Approximate cost |
|---|---|
| Lambda (runs once/day, ~15s) | < $0.01/month |
| Bedrock Claude Sonnet 4 (~4k tokens/run) | ~$0.12/month |
| Elastic Cloud Serverless | ~$2–4/month (depends on data volume) |
| Secrets Manager (2 secrets) | ~$0.80/month |
| **Total** | **~$3–5/month** |

---

## Makefile targets

| Target | What it does |
|---|---|
| `make test` | Run 8 integration tests (no AWS needed) |
| `make zip` | Build `cost-anomaly-agent.zip` for Lambda upload |
| `make build` | Build Docker image for native platform |
| `make build-amd64` | Build Docker image for Lambda x86_64 |
| `make ecr-push` | Build multi-arch image and push to ECR |
| `make run-local` | Run agent locally via Docker (needs `.env.local`) |
| `make clean` | Remove build artefacts |
