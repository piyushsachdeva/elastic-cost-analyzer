---
inclusion: always
---

# Project Structure & Conventions

## Directory layout
```
agent.py                   ← Lambda entry point + Bedrock converse loop (CloudCostAnomalyAgent class)
tools/
  elastic_search.py        ← All Elasticsearch query functions (5 functions)
  slack_notify.py          ← Slack Block Kit builder + HTTP POST
  audit_writer.py          ← Audit record writer to cost-anomaly-audit-*
  __init__.py
scripts/
  seed_billing.py          ← Demo data seeder (7-day baseline + today's spike)
tests/
  test_integration.py      ← Full agent integration test with mocked AWS + ES
requirements.txt           ← Pinned Python dependencies
Dockerfile                 ← Lambda container image
Makefile                   ← build / test / zip / deploy helpers
.env.example               ← Environment variable template
.kiro/
  steering/                ← Persistent Kiro workspace guidance
  specs/cost-anomaly-agent/ ← Feature spec (requirements, design, tasks)
  hooks/                   ← Automation hooks
```

## Import conventions
- Tools are imported at the `agent.py` module level: `from tools.elastic_search import find_spike_services, ...`
- When patching in tests, patch at the import location: `patch("agent.find_spike_services", ...)` NOT `patch("tools.elastic_search.find_spike_services", ...)`

## Naming conventions
- ES client variable: `_es_client` (module-level singleton, lazy init)
- Secret retrieval functions: `_get_es_client()`, `_get_webhook_url()`
- All tool functions are top-level functions in their module (not methods)
- Lambda handler: `lambda_handler(event, context)` in `agent.py`
- Agent class: `CloudCostAnomalyAgent` with `run()` as the main public method

## Error handling pattern
```python
try:
    result = self._dispatch_tool(tool_name, tool_input)
    results.append({"toolResult": {"toolUseId": tool_use_id, "content": [{"json": result}]}})
except Exception as exc:
    logger.error("Tool %s raised %s: %s", tool_name, type(exc).__name__, exc, exc_info=True)
    results.append({"toolResult": {"toolUseId": tool_use_id, "status": "error",
                                    "content": [{"text": f"{type(exc).__name__}: {exc}"}]}})
```

## Test conventions
- All integration tests in `tests/test_integration.py`
- Mock Bedrock converse via `side_effect` function that simulates the tool-calling sequence turn by turn
- Mock Bedrock response format: `{"stopReason": "tool_use", "output": {"message": {"role": "assistant", "content": [{"toolUse": {...}}]}}, "usage": {...}}`
- Never make real AWS or ES calls in tests
