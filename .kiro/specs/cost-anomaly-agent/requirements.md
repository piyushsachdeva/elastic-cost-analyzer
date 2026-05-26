# Requirements — Cloud Cost Anomaly Agent

## Background
Engineering teams get surprised by AWS bills at the end of the month. CloudWatch alerts
only tell you *that* a service spiked — not *why*. Manual investigation takes 30–45 minutes:
check CloudWatch, look at recent deploys, check HPA config. This agent automates all of that.

---

## User Stories

### US-1: Daily anomaly detection
**As a** FinOps engineer,  
**I want** an agent to automatically compare today's AWS costs against the 7-day baseline every morning,  
**So that** I am alerted to cost spikes before I see them on the monthly bill.

**Acceptance criteria:**
- [ ] Agent runs automatically at 08:00 UTC daily via EventBridge cron
- [ ] Agent compares each AWS service's spend today vs 7-day rolling average
- [ ] Services with spend > 25% above baseline are flagged as anomalies
- [ ] If no anomalies are found, the agent exits silently (no Slack message sent)
- [ ] Threshold percentage is configurable via `SPIKE_THRESHOLD_PCT` environment variable

---

### US-2: Root-cause correlation with deployments
**As a** DevOps engineer receiving a Slack alert,  
**I want** the alert to include which deployment may have caused the spike,  
**So that** I can immediately identify the responsible change without manual investigation.

**Acceptance criteria:**
- [ ] For each spiked service, agent retrieves hourly cost timeseries for the past 48 hours
- [ ] Agent identifies the exact hour the spike started (first hour > 1.5x prior 6-hour rolling average)
- [ ] Agent queries `deploy-events-*` index for any deployments within ±12 hours of spike start
- [ ] If a deploy is found, the Slack alert includes: version, team, deployed_by, commit_sha, hours_before_spike
- [ ] If no deploy is found, the alert states "No deploy found in ±12h window"

---

### US-3: Actionable Slack alert
**As a** on-call engineer,  
**I want** a single Slack message per day that tells me the cause and the fix,  
**So that** I can act immediately without switching tools.

**Acceptance criteria:**
- [ ] One Slack message per agent run (not one per anomaly)
- [ ] Message includes: service name, team, today's cost, 7-day baseline, delta ($ and %)
- [ ] Message includes a one-sentence cause (under 30 words), citing technical specifics
- [ ] Message includes a one-sentence fix with estimated dollar saving (under 25 words)
- [ ] Anomalies are sorted by dollar delta descending
- [ ] Message footer shows: run duration, token count, cost per run
- [ ] Message is sent only to `#finops` channel

---

### US-4: Audit trail
**As a** FinOps team lead,  
**I want** every agent run recorded in Elasticsearch,  
**So that** I can audit agent behavior, track token costs, and detect if the agent stopped running.

**Acceptance criteria:**
- [ ] Every run writes one document to `cost-anomaly-audit-YYYY.MM.dd`
- [ ] Audit record includes: run_id, anomalies_found, slack_delivered, duration_seconds, token_count
- [ ] Audit record is written even when Slack delivery fails
- [ ] Audit record includes error field if the run encountered an exception

---

### US-5: Reliability and cost guardrails
**As a** platform engineer,  
**I want** the agent to have hard limits on iterations and always finish cleanly,  
**So that** it cannot run away with tokens or hang the Lambda function.

**Acceptance criteria:**
- [ ] Bedrock converse loop has a hard cap of 20 iterations
- [ ] Lambda timeout is set to 300 seconds
- [ ] Agent returns `statusCode: 200` even when no anomalies are found
- [ ] If max iterations is hit without `end_turn`, agent logs an error but does not crash
- [ ] Total token cost per run should be under 10,000 tokens (approximately $0.01)
