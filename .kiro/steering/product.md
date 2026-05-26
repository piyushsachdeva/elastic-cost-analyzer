---
inclusion: always
---

# Product: Cloud Cost Anomaly Agent

## What this product does
An autonomous FinOps agent that runs every morning at 08:00 UTC. It detects AWS cost spikes
by comparing today's spend against a 7-day rolling baseline per service, correlates any spike
with recent deployments, reasons about the most likely root cause, and posts a single Slack
alert to #finops with a plain-English explanation and an actionable fix.

## Who it is for
Engineering teams and FinOps engineers who want to eliminate end-of-month AWS bill surprises
without spending 30–45 minutes manually cross-referencing CloudWatch, deploy history, and HPA config.

## The core value
Basic alerts tell you *that* costs spiked. This agent tells you *why* and *what to do*.

## Key constraints
- Never fabricate numbers — only report what tool results contain
- One Slack message per run maximum — no spam
- Always write an audit record to Elasticsearch, even if Slack fails
- Keep all generated text concise — engineers are busy
- Agent must complete within Lambda timeout (300 seconds)
- Total run cost target: under $0.01 per run
