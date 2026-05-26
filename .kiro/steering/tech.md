---
inclusion: always
---

# Tech Stack

## Runtime
- **Language**: Python 3.12 (matches AWS Lambda runtime)
- **Host**: AWS Lambda — 256MB memory, 300s timeout, x86_64
- **Trigger**: Amazon EventBridge cron rule (0 8 * * ? *) → Lambda
- **Secrets**: AWS Secrets Manager — two secrets: `cost-anomaly-agent/elastic-creds` and `cost-anomaly-agent/slack-webhook`

## AI / Reasoning
- **Model**: Amazon Bedrock Converse API — `us.anthropic.claude-sonnet-4-5-20250929-v1:0`
  - Must use the cross-region inference profile (`us.` prefix) — bare model ID returns ValidationException
- **Loop**: Bedrock converse loop with `toolConfig`, max 20 iterations, `temperature=0`
- **Tool format**: Bedrock Converse uses nested format — `{"toolUse": {"toolUseId":..,"name":..,"input":..}}`
- **Tool results format**: `{"toolResult": {"toolUseId":..,"content":[{"json": result}]}}`

## Data Layer — Elasticsearch
- **Cluster**: Elastic Cloud Serverless (Observability project type)
- **Endpoint**: `https://<project-id>.es.<region>.aws.elastic.cloud` — port 443 only, no :9243
- **Client**: `elasticsearch-py` — `Elasticsearch(hosts=[url], api_key=key)` — lazy init, cached in module scope
- **Three indices**:
  - `aws-billing-*` — hourly cost per service (READ only)
  - `deploy-events-*` — CI/CD deploy records (READ only)
  - `cost-anomaly-audit-*` — agent run audit trail (WRITE, one doc per run)

## Alerting — Slack
- **Method**: Incoming webhook — no OAuth, no Bot token needed
- **Format**: Slack Block Kit (section blocks + dividers)
- **Channel**: #finops
- **Condition**: Only post when anomalies are found (empty list → silent exit)

## Code Quality Rules (enforce in all generated code)
- Type hints on every function signature — no `Any` unless unavoidable
- Google-style docstrings on all public functions
- Specific exception handling — never bare `except:` or `except Exception:` without re-raise or logging
- All logging via the standard `logging` module — no `print()` in production code
- Environment variables read once at module level with `os.environ.get(KEY, default)`
- Credentials never hardcoded — always from Secrets Manager via `boto3`
