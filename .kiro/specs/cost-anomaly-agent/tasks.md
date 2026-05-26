# Tasks — Cloud Cost Anomaly Agent

## Implementation Plan

Tasks are ordered by dependency. Independent tasks within a group can run concurrently.

---

### Phase 1 — Core Infrastructure

- [x] **Task 1.1** — Create `tools/__init__.py` (empty module marker)
  - Outcome: `tools/` is importable as a Python package

- [x] **Task 1.2** — Create `tools/elastic_search.py` with 3 query functions
  - `find_spike_services(threshold_pct)` — date_histogram agg over aws-billing-*, compare today vs 7d avg
  - `get_cost_timeseries(service, hours)` — hourly cost data for spike pinpointing
  - `find_deploys_near_spike(service, spike_start_iso, window_hours)` — deploy correlation query
  - Include `_get_es_client()` singleton and `_get_secret()` helper
  - Outcome: All 3 functions return correctly typed dicts; ES client is cached after first call

- [x] **Task 1.3** — Create `tools/slack_notify.py`
  - `post_slack_alert(anomalies, causes, suggestions, run_meta)` — Block Kit builder + HTTP POST
  - Block Kit format: header, per-anomaly section, footer with run stats
  - Outcome: Function returns `{"delivered": bool, "error": str | None}`

- [x] **Task 1.4** — Create `tools/audit_writer.py`
  - `write_audit(run_id, anomalies_found, slack_delivered, duration_seconds, token_count, error)` 
  - Indexes to `cost-anomaly-audit-{YYYY.MM.dd}` with today's date
  - Outcome: Document indexed in ES; function returns `{"indexed": bool, "doc_id": str}`

---

### Phase 2 — Agent Core

- [x] **Task 2.1** — Create `agent.py` with `CloudCostAnomalyAgent` class
  - Constructor: initialise `run_id`, `start_time`, `bedrock` client, `messages` list, token counters
  - `run()`: converse loop — build initial message, iterate, handle `tool_use` and `end_turn`
  - `_invoke_bedrock()`: call `bedrock.converse()` with system prompt, messages, tool definitions
  - `_process_tool_calls(content_blocks)`: extract `toolUse` blocks, dispatch, return `toolResult` blocks
  - `_dispatch_tool(name, input)`: route by name to the 5 tool functions
  - `lambda_handler(event, context)`: create agent, call `run()`, return result
  - Outcome: Agent runs full 7-step loop; Lambda handler returns `{"statusCode": 200, "body": {...}}`

- [x] **Task 2.2** — Define `BEDROCK_TOOL_DEFINITIONS` in `agent.py`
  - 5 tool specs: `find_spike_services`, `get_cost_timeseries`, `find_deploys_near_spike`, `post_slack_alert`, `write_audit`
  - Each with full `inputSchema` JSON Schema including required fields
  - Outcome: Bedrock accepts tool config without validation errors

- [x] **Task 2.3** — Write `SYSTEM_PROMPT` in `agent.py`
  - 7-step reasoning sequence, constraints (no fabrication, concise text)
  - Outcome: Claude follows the 7-step sequence on every run

---

### Phase 3 — Tests

- [x] **Task 3.1** — Create `tests/test_integration.py`
  - `FAKE_SPIKE`, `FAKE_TIMESERIES`, `FAKE_DEPLOY` fixtures
  - `_make_bedrock_converse_side_effect(has_anomalies)` — smart mock for both paths
  - `test_full_chain_completes_successfully` — happy path, statusCode 200
  - `test_find_spike_services_called_once` — tool called exactly once
  - `test_timeseries_called_for_spiked_service` — service="Amazon EC2"
  - `test_deploy_lookup_called_with_correct_service` — service="checkout"
  - `test_slack_message_contains_required_fields` — "checkout-team", "v2.3.1", "$"
  - `test_audit_written_on_success` — ES index called
  - `test_no_anomalies_exits_silently` — Slack NOT called
  - `test_lambda_handler_returns_200` — entry point test
  - Outcome: `make test` → 8/8 passing in < 2 seconds

---

### Phase 4 — Demo Data & Deployment

- [x] **Task 4.1** — Create `scripts/seed_billing.py`
  - Seeds 7 days of hourly baseline data per service (EC2, RDS, Lambda, S3)
  - Seeds today's EC2 spike: ~$44/hr starting 17:00 UTC (vs ~$25/hr baseline = +43%)
  - Args: `--es-url`, `--api-key`
  - Outcome: `aws-billing-*` index has data; agent detects EC2 anomaly

- [x] **Task 4.2** — Create `Makefile` with targets: `test`, `zip`, `build`, `run-local`, `clean`
  - `make zip` produces `cost-anomaly-agent.zip` ready for Lambda upload
  - Outcome: `make test` passes; `make zip` produces deployable zip

- [x] **Task 4.3** — Create `requirements.txt` with pinned versions
  - `elasticsearch`, `boto3`, `opentelemetry-sdk` (optional), `pytest`
  - Outcome: `pip install -r requirements.txt` succeeds in Python 3.12

- [x] **Task 4.4** — Create `Dockerfile` for Lambda container
  - Base: `public.ecr.aws/lambda/python:3.12`
  - Multi-arch (amd64 + arm64) for `make ecr-push`
  - Outcome: `docker build` succeeds; container invokable locally

---

### Phase 5 — Documentation

- [x] **Task 5.1** — Create `README.md`
  - Architecture diagram, setup table, Makefile targets, cost estimate
  - Outcome: Viewer can follow README to reproduce setup

- [x] **Task 5.2** — Create `demo.md`
  - Full video recording script with timestamps and spoken script
  - Full infrastructure setup guide (Elastic → AWS → test)
  - Outcome: Presenter can follow demo.md to record the video

- [x] **Task 5.3** — Create `.kiro/` spec and steering files
  - `steering/product.md`, `steering/tech.md`, `steering/structure.md`
  - `specs/cost-anomaly-agent/requirements.md`, `design.md`, `tasks.md`
  - Outcome: Kiro IDE understands the project context on every interaction
