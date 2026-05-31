# Cloud Cost Anomaly Agent — Demo & Setup Guide

**Video title:** "I built an AI agent that catches AWS cost spikes — here's exactly how"  
**Goal:** Build a FinOps agent using **Kiro IDE + Amazon Bedrock + Elastic** that detects AWS cost anomalies, correlates them with deployments, and posts a root-cause Slack alert — automatically, every morning.

**What viewers will learn:**
1. How to use Kiro IDE's spec-driven workflow to design and build an AI agent
2. How the Amazon Bedrock Converse API's tool-calling loop works in practice
3. How to use Elasticsearch as both the data backbone and audit trail for an agentic app
4. How to test AI agents with mocked LLM responses (no real API calls needed)
5. How to wire three cloud services into a production agent that costs ~$3–5/month to run

**Target length:** 22–25 minutes  
**Platform:** YouTube 1080p, 16:9

---

## PRE-RECORDING CHECKLIST

Complete all of this **before** starting the camera.

### Infrastructure (must be live)
- [ ] Elastic Cloud Serverless project created (Observability type, `us-east-1`)
- [ ] `deploy-events-*` index created and seeded (3+ deploy events, including `v2.3.1` at 14:00 UTC today)
- [ ] `metrics-aws.billing-*` index seeded via `scripts/seed_billing.py` — verify EC2 is ~76% above baseline today
- [ ] API key `cost-anomaly-agent` created in Kibana with correct index privileges
- [ ] Slack `#finops` channel exists; incoming webhook URL tested (`curl` test returns `ok`)
- [ ] AWS Secrets Manager: `cost-anomaly-agent/elastic-creds` and `cost-anomaly-agent/slack-webhook` created
- [ ] IAM role `cost-anomaly-agent-lambda-role` created with inline policy (Bedrock + Secrets Manager + Marketplace)
- [ ] Lambda function `cost-anomaly-agent` deployed with correct env vars, 300s timeout, 256MB
- [ ] EventBridge cron rule created (not required to fire during recording — manual invoke is enough)
- [ ] `source .venv/bin/activate && make test` → 8/8 passing locally
- [ ] Bedrock model access active: `aws bedrock list-inference-profiles` shows `us.anthropic.claude-sonnet-4-5-20250929-v1:0  ACTIVE`

### Browser tabs (pre-load, pre-authenticated)
- [ ] Kibana → Discover → `metrics-aws.billing-*` (showing the spike data)
- [ ] Kibana → Dev Tools console
- [ ] Kibana → `cost-anomaly-audit-*` search ready in Dev Tools
- [ ] AWS Console → Lambda → `cost-anomaly-agent` → Test tab
- [ ] AWS Console → EventBridge → Rules (showing the cron rule)
- [ ] Slack → `#finops` channel open
- [ ] GitHub repo open in browser

### Code editor
- [ ] Kiro IDE open with project root
- [ ] `.kiro/specs/cost-anomaly-agent/requirements.md` open in a tab
- [ ] `agent.py` open in a tab
- [ ] `tools/elastic_search.py` open in a tab
- [ ] `tests/test_integration.py` open in a tab

### Terminal
- [ ] Virtual env activated: `source .venv/bin/activate`
- [ ] `make test` already run and output visible or easy to re-run
- [ ] `scripts/seed_billing.py` command ready to paste (use `$ES_SUPERUSER_KEY`, NOT the agent key)

### Recording settings
- [ ] Notifications silenced (Do Not Disturb on all devices)
- [ ] Browser zoom: 110% for Kibana and AWS console
- [ ] Terminal: 16px+ font, dark theme, high contrast
- [ ] Resolution: 1920x1080 minimum, 2560x1440 preferred

---

## FULL INFRASTRUCTURE SETUP

Follow this section to build everything from scratch. It matches the demo recording flow.

### PART 1 — Elastic Cloud Serverless

#### 1a. Create a Serverless project

1. Go to [cloud.elastic.co](https://cloud.elastic.co) — sign up or log in
   - If subscribing via AWS: **AWS Console → AWS Marketplace** → search `Elastic Cloud` → subscribe → **Set up your account** → redirects to `cloud.elastic.co`
2. Click **Create serverless project**
3. Project type: **Observability** *(required — unlocks Fleet, APM, AWS Billing integration)*
4. Name: e.g. `My Observability project`
5. Cloud provider: **AWS**, Region: **same as your Lambda** (e.g. `us-east-1`)
6. Click **Create project** — wait ~60 seconds

#### 1b. Find your Elasticsearch endpoint

1. `cloud.elastic.co` → your project row → click **Manage**
2. Scroll to **"Application endpoints, cluster and component IDs"**
3. Click **Elasticsearch** → copy the **Public endpoint**
   - Format: `https://<project-id>.es.<region>.aws.elastic.cloud`
   - Example: `https://my-observability-project-d374b6.es.us-east-1.aws.elastic.cloud`
4. Also copy the **Kibana** endpoint (click **Kibana** in the same panel)

> **Port:** Serverless uses port **443** only. Do NOT append `:9243`.

#### 1c. Create index templates and seed deploy events

Open **Kibana** → left sidebar → **`</>`** icon (Developer tools) → **Console**

**Create deploy-events template:**
```http
PUT _index_template/deploy-events-template
{
  "index_patterns": ["deploy-events-*"],
  "template": {
    "mappings": {
      "properties": {
        "service":      { "type": "keyword" },
        "version":      { "type": "keyword" },
        "team":         { "type": "keyword" },
        "deployed_by":  { "type": "keyword" },
        "commit_sha":   { "type": "keyword" },
        "@timestamp":   { "type": "date" },
        "environment":  { "type": "keyword" }
      }
    }
  }
}
```

**Seed deploy events** — replace `TODAY` with today's date in both formats shown below.
Example: if today is June 1 2026, use `2026.06.01` for the index and `2026-06-01` in timestamps.

```http
POST deploy-events-2026.06.01/_doc
{
  "service": "checkout", "version": "v2.3.1", "team": "checkout-team",
  "deployed_by": "alice@acme.com", "commit_sha": "a3f9c12d",
  "environment": "production", "@timestamp": "2026-06-01T14:00:00Z"
}
```

```http
POST deploy-events-2026.06.01/_doc
{
  "service": "checkout", "version": "v2.3.0", "team": "checkout-team",
  "deployed_by": "bob@acme.com", "commit_sha": "b9e4a33f",
  "environment": "production", "@timestamp": "2026-06-01T09:15:00Z"
}
```

```http
POST deploy-events-2026.06.01/_doc
{
  "service": "payment-service", "version": "v1.7.4", "team": "payments-team",
  "deployed_by": "carol@acme.com", "commit_sha": "c7d2f891",
  "environment": "production", "@timestamp": "2026-06-01T16:30:00Z"
}
```

> **Important:** Use today's date. The agent looks up deploys within ±12 hours of the spike, so stale deploy data from yesterday won't be found.

**Create billing template (matches real Elastic AWS Billing integration schema):**
```http
PUT _index_template/metrics-aws-billing-template
{
  "index_patterns": ["metrics-aws.billing-*"],
  "template": {
    "mappings": {
      "properties": {
        "@timestamp": { "type": "date" },
        "aws.billing.ServiceName": { "type": "keyword" },
        "aws.billing.UnblendedCost.amount": { "type": "float" },
        "aws.billing.UnblendedCost.unit": { "type": "keyword" },
        "cloud.provider": { "type": "keyword" },
        "event.dataset": { "type": "keyword" },
        "tags": {
          "properties": { "team": { "type": "keyword" } }
        }
      }
    }
  }
}
```

#### 1d. Create the agent API key

**Kibana → left sidebar → gear icon (⚙) → Admin and settings → Access → API keys → Create API key**

1. Name: `cost-anomaly-agent`
2. Expiration: **No expiration** (or 1 year for production)
3. Toggle on **Restrict privileges** and paste:
   ```json
   {
     "cost-agent-role": {
       "indices": [
         {
           "names": ["metrics-aws.billing-*", "deploy-events-*"],
           "privileges": ["read", "view_index_metadata"]
         },
         {
           "names": ["cost-anomaly-audit-*"],
           "privileges": ["create_index", "create", "index"]
         }
       ]
     }
   }
   ```
4. Click **Create API key**
5. Copy the **Encoded** value (long base64 string) — this is your `es_api_key`

> **Navigation note:** In Kibana Serverless the management area is called **"Admin and settings"** — accessed via the gear/settings icon (⚙) at the bottom of the left sidebar. This is equivalent to "Stack Management" in classic Kibana.

---

### PART 2 — Slack Incoming Webhook

1. [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From scratch**
2. App name: `Cost Anomaly Agent`, workspace: yours → **Create App**
3. Left sidebar → **Incoming Webhooks** → toggle **Activate Incoming Webhooks** ON
4. **Add New Webhook to Workspace** → select `#finops` → **Allow**
5. Copy the webhook URL: `https://hooks.slack.com/services/T.../B.../...`

Test:
```bash
curl -X POST https://hooks.slack.com/services/YOUR/WEBHOOK/URL \
  -H 'Content-type: application/json' \
  --data '{"text":"✅ Webhook test — cost anomaly agent setup"}'
```
Confirm `ok` in the response and message in `#finops`.

---

### PART 3 — AWS Infrastructure

**First: clone the repo and install dependencies:**
```bash
git clone https://github.com/itsBaivab/elastic-cost-analyzer.git
cd elastic-cost-analyzer
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
make test   # should show 8/8 passing
```

Set shell variables (used throughout):
```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=us-east-1
ES_URL=https://your-project.es.us-east-1.aws.elastic.cloud
ES_API_KEY=your-base64-encoded-agent-api-key    # from §1e — read-only key for the agent
ES_SUPERUSER_KEY=your-superuser-api-key         # from Kibana Admin settings — needed for seeding
SLACK_WEBHOOK=https://hooks.slack.com/services/T.../B.../...
```

> ⚠️ **Two different API keys are used:**
> - `ES_API_KEY` → restricted read key from §1e → stored in Secrets Manager → used by Lambda
> - `ES_SUPERUSER_KEY` → superuser key (created via Admin and settings → API keys with no restrictions) → used only for seeding and is never stored in code

#### 3a. Create IAM user for Elastic AWS Billing integration

This user's credentials are given to Elastic Cloud so it can call the AWS Cost Explorer API on your behalf — no server required (Elastic runs the collector agentlessly in their cloud).

```bash
# Create the IAM user
aws iam create-user --user-name elastic-billing-reader

# Attach Cost Explorer + CloudWatch read permissions
aws iam put-user-policy \
  --user-name elastic-billing-reader \
  --policy-name ElasticBillingReadOnly \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [
      {
        "Effect": "Allow",
        "Action": [
          "ce:GetCostAndUsage",
          "ce:GetTags",
          "ce:GetDimensionValues",
          "cloudwatch:GetMetricData",
          "cloudwatch:ListMetrics",
          "iam:ListAccountAliases",
          "sts:GetCallerIdentity",
          "tag:GetResources"
        ],
        "Resource": "*"
      }
    ]
  }'

# Generate access keys — copy both values, you will need them in Kibana
aws iam create-access-key --user-name elastic-billing-reader \
  --query 'AccessKey.{KeyId:AccessKeyId,Secret:SecretAccessKey}' \
  --output table
```

Save the **AccessKeyId** and **SecretAccessKey** — you enter these in Kibana next.

#### 3a-ii. Add AWS Billing integration in Kibana (Agentless)

**Observability → Add data → Cloud → AWS → AWS Billing → Add AWS Billing**

1. **Deployment mode** → select **Agentless** *(Elastic runs the collector — no server needed)*
2. **Access Key ID** → paste from §3a
3. **Secret Access Key** → paste from §3a
4. Scroll to **Collect billing metrics** → toggle **ON**; set **Collection Period** → `5m`
5. **Cost Explorer Group By Dimension Keys** → keep only `SERVICE` (remove AZ, INSTANCE_TYPE, LINKED_ACCOUNT — AWS API only allows 2 groups max)
6. **Cost Explorer Group By Tag Keys** → clear this field
7. Expand **Advanced options** → set **Default AWS Region** → `us-east-1` *(required — Cost Explorer endpoint is us-east-1 only)*
8. Click **Save and deploy**

> **Critical:** The `Default AWS Region` field MUST be set to `us-east-1`. If left blank the Cost Explorer API calls fail silently — the integration shows Healthy but writes no data.

> **Agentless:** Elastic Cloud provisions a managed collector on their infrastructure. No Elastic Agent binary to install. Data flows into `metrics-aws.billing-*` automatically every 5 minutes.

> **Real account spend note:** This integration pulls real AWS Cost Explorer data. On a dev/demo account with minimal spend (~$0.000006/day), the Cost Explorer API returns near-zero values and no anomaly is detected. For the demo recording, seed realistic data (§3a-iii) so the agent fires. The integration is still the production story — show it on camera, then seed for the live run.

#### 3a-iii. Seed billing data for demo recording

Even with the integration running, a dev account has near-zero spend — the agent finds no anomaly. Seed 7 days of baseline + today's EC2 spike using the exact same field schema the integration writes:

```bash
source .venv/bin/activate
python3 scripts/seed_billing.py \
  --es-url "$ES_URL" \
  --api-key "$ES_SUPERUSER_KEY"
```

Expected output:
```
Seeding 7-day baseline...
  metrics-aws.billing-2026.05.24: 96 docs
  ... (7 days)
Seeding today with EC2 spike all 24 hours (~$44/hr vs $25/hr baseline)...
✅ Done — 768 documents written to metrics-aws.billing-*
   Expected pct_change: ~76% above 7-day baseline
```

Verify in **Kibana → Discover → `metrics-aws.billing-*`** — you should see the EC2 spike in the histogram.

> **On camera:** *"In production, this index is populated automatically by the Elastic AWS Billing integration every 5 minutes — real Cost Explorer data, no manual step. For this recording I seeded data at production EC2 scale so you can see the agent actually fire."*

---

#### 3b. Enable Amazon Bedrock model access

**AWS Console → Amazon Bedrock → left sidebar: Bedrock configurations → Model access**

1. Click **Enable specific models** (or **Modify model access**)
2. Find **Claude Sonnet 4** under Anthropic — check the box
3. If prompted: fill in the use case form (company, industry, use case description)
   ```
   FinOps cost anomaly detection. Reads AWS billing data from Elasticsearch,
   correlates cost spikes with deployment events, posts Slack alerts.
   ```
4. Click **Save changes** — wait up to 15 minutes for access to propagate

Verify:
```bash
aws bedrock list-inference-profiles --region us-east-1 \
  --query 'inferenceProfileSummaries[?contains(inferenceProfileId,`sonnet-4-5`)].[inferenceProfileId,status]' \
  --output table
```
Expected: `us.anthropic.claude-sonnet-4-5-20250929-v1:0  |  ACTIVE`

> **Critical:** Use the cross-region inference profile ID with the `us.` prefix.
> Bare model ID (`anthropic.claude-sonnet-4-5`) returns `ValidationException`.

#### 3c. Secrets Manager

> **Important:** `ES_API_KEY` must be the key from §1e with `read` access to `metrics-aws.billing-*` and `deploy-events-*`, and `create/index` on `cost-anomaly-audit-*`. A key scoped to only `aws-billing-*` will return empty aggregations without an error.

```bash
ELASTIC_ARN=$(aws secretsmanager create-secret \
  --region $REGION \
  --name "cost-anomaly-agent/elastic-creds" \
  --secret-string "{\"es_url\":\"$ES_URL\",\"es_api_key\":\"$ES_API_KEY\"}" \
  --query 'ARN' --output text)
echo "Elastic ARN: $ELASTIC_ARN"

SLACK_ARN=$(aws secretsmanager create-secret \
  --region $REGION \
  --name "cost-anomaly-agent/slack-webhook" \
  --secret-string "{\"webhook_url\":\"$SLACK_WEBHOOK\"}" \
  --query 'ARN' --output text)
echo "Slack ARN: $SLACK_ARN"
```

#### 3d. IAM role

```bash
aws iam create-role \
  --role-name "cost-anomaly-agent-lambda-role" \
  --assume-role-policy-document '{
    "Version":"2012-10-17",
    "Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]
  }'

aws iam attach-role-policy \
  --role-name "cost-anomaly-agent-lambda-role" \
  --policy-arn "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"

aws iam put-role-policy \
  --role-name "cost-anomaly-agent-lambda-role" \
  --policy-name "cost-anomaly-agent-inline" \
  --policy-document "{
    \"Version\":\"2012-10-17\",
    \"Statement\":[
      {\"Sid\":\"BedrockInvoke\",\"Effect\":\"Allow\",
       \"Action\":[\"bedrock:InvokeModel\",\"bedrock:InvokeModelWithResponseStream\",\"bedrock:Converse\",\"bedrock:ConverseStream\"],
       \"Resource\":\"*\"},
      {\"Sid\":\"BedrockMarketplace\",\"Effect\":\"Allow\",
       \"Action\":[\"aws-marketplace:ViewSubscriptions\",\"aws-marketplace:Subscribe\",\"aws-marketplace:Unsubscribe\"],
       \"Resource\":\"*\"},
      {\"Sid\":\"SecretsManagerRead\",\"Effect\":\"Allow\",
       \"Action\":\"secretsmanager:GetSecretValue\",
       \"Resource\":[
         \"arn:aws:secretsmanager:$REGION:$ACCOUNT_ID:secret:cost-anomaly-agent/elastic-creds-*\",
         \"arn:aws:secretsmanager:$REGION:$ACCOUNT_ID:secret:cost-anomaly-agent/slack-webhook-*\"
       ]}
    ]
  }"
```

#### 3e. Build and deploy Lambda

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
make zip
ls -lh cost-anomaly-agent.zip    # should be 6–15 MB

sleep 15   # wait for IAM propagation

aws lambda create-function \
  --region $REGION \
  --function-name "cost-anomaly-agent" \
  --runtime "python3.12" \
  --role "arn:aws:iam::${ACCOUNT_ID}:role/cost-anomaly-agent-lambda-role" \
  --handler "agent.lambda_handler" \
  --zip-file "fileb://cost-anomaly-agent.zip" \
  --timeout 300 \
  --memory-size 256 \
  --environment "Variables={
    ELASTIC_SECRET_ARN=$ELASTIC_ARN,
    SLACK_SECRET_ARN=$SLACK_ARN,
    AWS_BEDROCK_REGION=$REGION,
    SPIKE_THRESHOLD_PCT=25.0,
    AGENT_MAX_ITERATIONS=20,
    BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-4-5-20250929-v1:0
  }"
```

#### 3f. EventBridge daily cron

```bash
aws events put-rule \
  --region $REGION \
  --name "cost-anomaly-agent-daily" \
  --schedule-expression "cron(0 8 * * ? *)" \
  --state ENABLED \
  --description "Trigger cost anomaly agent every morning at 08:00 UTC"

LAMBDA_ARN=$(aws lambda get-function --function-name cost-anomaly-agent \
  --region $REGION --query 'Configuration.FunctionArn' --output text)

aws events put-targets \
  --region $REGION \
  --rule "cost-anomaly-agent-daily" \
  --targets "[{\"Id\":\"1\",\"Arn\":\"$LAMBDA_ARN\"}]"

aws lambda add-permission \
  --function-name "cost-anomaly-agent" \
  --region $REGION \
  --statement-id "EventBridgeInvoke" \
  --action "lambda:InvokeFunction" \
  --principal "events.amazonaws.com" \
  --source-arn "$(aws events describe-rule --name cost-anomaly-agent-daily --region $REGION --query 'Arn' --output text)"
```

#### 3g. Verify end-to-end

**1. Trigger Lambda manually:**

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

**2. Confirm Slack message:**
Slack → `#finops` → new Block Kit message present

**3. Confirm audit record:**
Kibana Dev Tools:
```http
GET cost-anomaly-audit-*/_search
{
  "sort": [{"@timestamp": {"order": "desc"}}],
  "size": 1
}
```

---

## VIDEO RECORDING SCRIPT

---

### SEGMENT 1 — The Problem (0:00 – 2:00)

**Screen:** AWS Cost Explorer showing a cost graph with a spike. No explanation of why.

**Spoken script:**
> "You open your laptop Monday morning, and there's an alert in Slack. Your AWS bill spiked
> overnight. EC2 is up 43%. That's it. That's all you know.
>
> So now you've got 30, maybe 45 minutes of investigation ahead of you. Check CloudWatch,
> look at what deployed last night, cross-reference your HPA config, figure out which team
> owns the service. By the time you've got an answer, half your morning is gone.
>
> [pause 2 seconds]
>
> What if an AI agent did all of that automatically, every night, and posted the answer to
> Slack before you even opened your laptop? That's what we're building today.
>
> By the end of this video, you'll know exactly how to build an autonomous FinOps agent
> using three tools: Kiro IDE, Amazon Bedrock, and Elastic. And I'll show you every decision
> along the way — because the goal isn't just to give you a repo to clone. The goal is for
> you to understand how these three tools fit together, so you can build your own agentic
> applications on top of them."

**[CUT]**

---

### SEGMENT 2 — Show the Finished Output First (2:00 – 3:30)

**Screen:** Switch to Slack `#finops`. The Block Kit message is visible.

**Spoken script:**
> "Here's what the agent posts. One message. Let me walk through it.
>
> [point at each section as you say it]
>
> Service: Amazon EC2, owned by the checkout team.
> Today's spend: $847. Seven-day baseline: $592. That's +43%, or $255 above normal.
>
> Likely cause: 'HPA scaled checkout pods from 3 to 12 replicas, 6 hours after deploy
> v2.3.1 at 14:00 UTC. CPU held at 18% — minReplicas set too high.'
>
> Suggested fix: 'Reduce minReplicas to 3 in checkout-service/k8s/hpa.yaml. Saves ~$220 today.'
>
> Deploy info: v2.3.1 by alice@acme.com, commit a3f9c12d. Found 3 hours before the spike.
>
> Footer: 14.2 seconds to run. 4,380 tokens. About half a cent.
>
> [pause]
>
> The agent found the spike, pinpointed when it started, looked up whether a deploy happened
> around that time, reasoned about the cause in plain English, and suggested a specific fix.
> All automatically. Let's go build it."

**[CUT]**

---

### SEGMENT 3 — Architecture Overview (3:30 – 6:00)

**Screen:** Architecture diagram (draw on whiteboard or show the ASCII diagram from README)

**Spoken script:**
> "Before we touch any code, let me give you the mental model. Five components.
>
> [point at EventBridge]
> EventBridge fires a cron rule every morning at 8am UTC. It triggers a Lambda function.
>
> [point at Lambda]
> Lambda is thin — just a host. It creates the agent and calls run(). All the logic is
> inside the agent class. Lambda timeout is 300 seconds, 256 megabytes. Cheap.
>
> [point at Bedrock]
> The agent uses Amazon Bedrock's Converse API. That's Claude Sonnet 4 in a reasoning loop.
> Claude reads the system prompt, decides which tools to call, reads the results, decides
> what to call next. Max 20 iterations — hard cap to prevent runaway costs.
>
> [point at Elasticsearch]
> All the data lives in Elasticsearch. Three indices. One holds AWS billing data — cost per
> service per hour. One holds deploy events from your CI/CD pipeline. And one is where the
> agent writes an audit record after every run.
>
> [point at Slack]
> And the output is a Slack Block Kit message to the finops channel.
>
> The whole thing costs about $3–5 a month to run. Lambda is nearly free at one invocation
> per day. Bedrock at 4k tokens per run is about 12 cents a month. Elastic Cloud Serverless
> is the biggest line item at $2–4 depending on data volume.
>
> Now — before we look at the code, let me show you how this project was designed.
> Because the code is the output of a spec, not the starting point."

**[CUT]**

---

### SEGMENT 4 — Kiro: Spec-Driven Development (6:00 – 11:00)

**Screen:** Kiro IDE open. File tree visible. `.kiro/` directory visible.

**Spoken script:**
> "This is Kiro IDE. It's Amazon's agentic IDE — free public preview, VS Code based.
> And the key idea is spec-driven development.
>
> Instead of starting with code, you start with a spec — a description of what you want to
> build, including the requirements, the architecture, and the implementation tasks.
> Kiro reads the spec and generates code that's consistent with your whole system, not just
> the function you asked for right now.
>
> Let me show you what that looks like for this project."

**[ZOOM IN on .kiro/ directory in the file tree]**

> "I've got a .kiro directory with three things: steering files, a spec, and hooks.
> Let me start with the steering files."

**Screen:** Open `.kiro/steering/tech.md`

> "Steering files are persistent context for Kiro. This one tells Kiro everything about
> the tech stack — the Bedrock model ID, the exact format Bedrock expects for tool calls,
> the Elasticsearch client pattern, the code quality rules.
>
> I wrote this once. Now every piece of code Kiro touches in this project follows these
> conventions. That's why every function has type hints. That's why there's no bare
> except: statement anywhere. The steering file enforces it automatically.
>
> [pause 1 second]
>
> Now let me show you the spec."

**Screen:** Open `.kiro/specs/cost-anomaly-agent/requirements.md`

> "This is the requirements file. Five user stories with acceptance criteria.
> Look at user story 2 — deploy correlation. The acceptance criteria say the agent must
> identify the exact hour the spike started, search for deploys within plus or minus 12 hours,
> and return version, team, deployed-by, commit SHA, and hours before spike.
>
> These requirements became the test assertions. They also became the ES query design.
> Writing them first forced me to think about exactly what data I needed — before I wrote
> a single query."

**Screen:** Open `.kiro/specs/cost-anomaly-agent/design.md`

> "From the requirements, Kiro helped produce this design document. The architecture diagram.
> The Bedrock message flow — this sequence here shows exactly what Claude will call and in
> what order, every single run. The Elasticsearch index schemas.
>
> This became the blueprint for the code. Let me show you what the blueprint produced."

**Screen:** Open `agent.py` — show the class structure

> "agent.py. CloudCostAnomalyAgent class. Notice the structure matches the design exactly.
> run() is the converse loop. _invoke_bedrock() calls the Converse API. _process_tool_calls()
> handles the tool dispatch. And lambda_handler at the bottom is the entry point.
>
> [scroll to _process_tool_calls]
>
> Here's a subtlety that tripped me up. Bedrock's Converse API wraps tool calls in a 'toolUse'
> key — nested like this. If you check for the flat format that the Anthropic Messages API uses,
> you'll get zero tool results back, and Claude will see an empty message and throw a
> ValidationException. The design doc caught this because I had to write the message flow out
> explicitly. That's the value of designing before coding."

**Screen:** Open `.kiro/specs/cost-anomaly-agent/tasks.md`

> "And here are the tasks. 17 tasks across 5 phases — all checked off because we already built
> this. But in Kiro's IDE, you'd see each task here, click it, and Kiro executes it — generating
> code that's consistent with the steering files and all the tasks it already completed.
>
> The key thing: Kiro doesn't just write a function. It writes a function that fits your
> architecture, follows your coding standards, and registers itself correctly in all the right places."

**Screen:** Open `.kiro/hooks/on-save-agent.md`

> "Last Kiro feature — hooks. This is a file save hook. Every time I save agent.py or any
> tool file, Kiro automatically runs the integration tests. And the second hook checks whether
> any new tool function I added got registered in the Bedrock tool definitions.
>
> These are the automations that catch mistakes before they become bugs. Let me show the tests."

**[CUT]**

---

### SEGMENT 5 — The Code: Agent Loop and Tools (11:00 – 14:00)

**Screen:** `tools/elastic_search.py` open

**Spoken script:**
> "Three tool files. Let me walk through elastic_search.py first because it's the core
> of what makes this an agentic application — not just an API call.
>
> [scroll to find_spike_services]
>
> find_spike_services. This function runs a date histogram aggregation over the
> metrics-aws.billing index. It computes today's total spend per service, computes the 7-day average, and returns
> any service where today exceeds the threshold. The result is a list of dicts with the service
> name, team, dollar amounts, and percentage change.
>
> [scroll to _get_es_client]
>
> The ES client is a module-level singleton — lazy initialisation, cached after first call.
> Credentials come from Secrets Manager, not environment variables directly. This pattern
> means the Lambda cold start doesn't block on credential fetching — the first tool call
> initialises it, then every subsequent call reuses the connection.
>
> [scroll to find_deploys_near_spike]
>
> find_deploys_near_spike. This is the deploy correlation query. Takes a service name and a
> spike start time — searches deploy-events for anything within plus or minus 12 hours.
> The result tells Claude: there was a deploy 3 hours before the spike started, it was version
> v2.3.1, deployed by alice@acme.com. Claude connects the dots."

**Screen:** Switch to `tools/slack_notify.py`

> "slack_notify builds the Block Kit message. Block Kit is Slack's structured message format.
> The function takes the anomalies list, the cause strings, the fix suggestions, and run metadata
> — and assembles them into blocks. Header, one section per anomaly, a footer with run stats.
> Then it POSTs to the webhook URL it fetches from Secrets Manager.
>
> One important design decision: this function only gets called when there are anomalies.
> If the billing data is clean, the agent writes an audit record and exits silently. No noise
> in the finops channel on good days."

**[CUT]**

---

### SEGMENT 6 — Elastic Setup (14:00 – 17:00)

**Screen:** Kibana → left sidebar → **Integrations** → search "AWS"

**Spoken script:**
> "Before we look at the data, let me show you where it comes from in production.
> This is the Elastic integrations catalog. Search AWS and you'll see over 30 integrations.
> Two of them are the backbone of this agent."

---

**Screen:** Click **AWS Billing** integration page (show the overview — matches screenshot)

> "First one: AWS Billing. This integration connects to your AWS Cost Explorer API and
> pulls your spend per service — every hour, automatically, into Elasticsearch.
>
> Notice the ingestion methods: AWS S3, CloudWatch, and direct API. Three ways to get
> your billing data in. The data lands in `metrics-aws.billing-*` — which is exactly
> the index our agent queries.
>
> 53 pre-built Kibana dashboards. 8 alerting rule templates. 40 ingest pipelines.
> You get all of this for free the moment you enable the integration."

> **[editor note: stay on this screen for 20 seconds — let viewers read the Kibana assets panel]**

---

**Screen:** Click **AWS CloudWatch** integration page (show the overview — matches screenshot)

> "Second one: AWS CloudWatch. This pulls operational metrics — EC2 CPU, RDS connections,
> Lambda error rates, network bytes — from every service in your account.
>
> Here's why this matters for cost anomaly detection specifically:
>
> Billing tells you your EC2 cost jumped $800 today. CloudWatch tells you CPU was at 12%
> the entire time. That combination tells you it wasn't a traffic spike — it was an
> autoscaler that over-provisioned and nobody noticed.
>
> Both data streams live in Elasticsearch. The agent queries them in one place.
> That's the value of Elastic as an agentic data layer — you don't call three separate
> AWS APIs. You run one aggregation against one platform that already has everything."

> **[editor note: pause here — this is the key architectural point of the video]**

---

**Screen:** Kibana → Discover → `metrics-aws.billing-*` (showing 7-day histogram with spike)

> "For this demo I seeded realistic billing data using the same field schema the AWS Billing
> integration writes — `aws.billing.ServiceName`, `aws.billing.UnblendedCost.amount`.
> The agent code is identical whether it's reading real integration data or this.
>
> You can see 7 days of EC2 baseline at ~$25/hr. Today EC2 is running at ~$44/hr all day —
> that's 76% above the 7-day average. That's what the agent is about to analyze."

---

**Screen:** Kibana → Discover → `deploy-events-*`

> "And here's the deploy-events index. In production your CI/CD pipeline writes one document
> here after each successful deploy — three lines in your GitHub Actions workflow.
> This is what enables root-cause correlation. Without this index, the agent tells you
> costs spiked. With it, it tells you why."

---

**Screen:** Kibana → Admin and settings → API keys

> "The API key the agent uses is read-only on billing and deploy indices, write-only on the
> audit index. Least privilege. The key never touches the code — it lives in AWS Secrets
> Manager."

**[CUT]**

---

### SEGMENT 7 — Run the Integration Tests (17:00 – 18:30)

**Screen:** `tests/test_integration.py` open in Kiro. Terminal ready.

**Spoken script:**
> "Before running the real thing, let's run the integration tests. This is important — not
> just to verify the code works, but to show you a technique for testing AI agents without
> making real LLM API calls.
>
> [scroll to FAKE_SPIKE and FAKE_DEPLOY fixtures]
>
> Look at the test fixtures. We inject a fake EC2 spike — $847 today, 43% above baseline.
> A fake v2.3.1 deploy, 3 hours before the spike. Completely synthetic.
>
> [scroll to _make_bedrock_converse_side_effect]
>
> And here's the key function. This mock simulates Claude's tool-calling sequence turn by turn.
> Turn 1: Claude calls find_spike_services. Turn 2: get_cost_timeseries. Turn 3: deploy lookup.
> Turn 4: post_slack_alert. Turn 5: write_audit. Turn 6: end_turn.
>
> We're testing the agent loop, the tool dispatch, and the Slack output format — all without
> a single real Bedrock call. These tests run in under half a second."

**Screen:** Run tests in terminal

```bash
make test
```

**[PAUSE — wait for output]**

> "8 tests, all green, 0.4 seconds. The agent correctly calls the right tools in the right
> order, the Slack payload contains checkout-team, v2.3.1, and a dollar figure, and the
> audit record gets written. Everything is working.
>
> Now let's fire it for real."

**[CUT]**

---

### SEGMENT 8 — Live Demo (18:30 – 22:00)

**Screen:** AWS Console → Lambda → `cost-anomaly-agent` → Test tab

**Spoken script:**
> "The billing data is already seeded — EC2 is running at ~76% above its 7-day baseline today.
> The deploy events are in Elasticsearch. Secrets Manager has the credentials. The IAM role
> has the permissions.
>
> I'm going to trigger the Lambda manually — same as what EventBridge does every morning at 8am."

**Screen:** Enter test event `{"source": "manual-demo"}` and click Test

> "Firing it now."

**[PAUSE — wait for Lambda execution, ~15 seconds]**

> "There's the response. Status 200. Run took [duration] seconds. [total_tokens] tokens.
>
> [switch to Slack `#finops`]
>
> And there's the Slack message. Let me walk through it."

**[ZOOM IN on Slack message, point at each section]**

> "Service: Amazon EC2. Team: checkout-team. Today: $847. Baseline: $592. Delta: +$255, +43%.
>
> Cause: [read the cause from the Slack message]
>
> Fix: [read the fix from the Slack message]
>
> Deploy: v2.3.1 by alice@acme.com. That's the deploy we seeded 3 hours before the spike.
> The agent found it.
>
> Footer: [duration] seconds, [tokens] tokens, $0.00[x] to run.
>
> [pause 2 seconds]
>
> Think about what just happened. The agent queried Elasticsearch for today's billing data,
> compared it to 7 days of history, found the EC2 anomaly, pulled the hourly timeseries,
> identified 17:00 UTC as the spike start, searched the deploy index for events near that time,
> found v2.3.1, reasoned about what a deployment 3 hours before an EC2 spike might mean,
> wrote a human-readable explanation, and posted it to Slack. In 15 seconds. Automatically."

**Screen:** Switch to Kibana Dev Tools

> "And it wrote the audit record."

```http
GET cost-anomaly-audit-*/_search
{
  "sort": [{"@timestamp": {"order": "desc"}}],
  "size": 1
}
```

**[PAUSE for result]**

> "run_id, anomalies_found: 1, slack_delivered: true, duration, token count. Every run is
> recorded here — even if Slack fails, the audit still gets written. That's your observability
> paper trail for the agent itself."

**[CUT]**

---

### SEGMENT 9 — What You Just Learned (22:00 – 24:00)

**Screen:** Split view — Kiro spec on the left, running Lambda response on the right

**Spoken script:**
> "Let me recap what we built and what you can take from it.
>
> [point at Kiro]
>
> First: Kiro. Spec-driven development isn't about AI doing the work for you — it's about
> AI doing the work *correctly*. The steering files mean every function Kiro generates follows
> your conventions. The spec means the code matches the architecture. The hooks mean tests run
> before bugs ship. This is how you build production systems with AI assistance, not just prototypes.
>
> [point at Bedrock]
>
> Second: Bedrock Converse API. The tool-calling loop is the core pattern of every agentic
> application. You define tools with JSON schemas. Claude decides which ones to call and when.
> Your code dispatches the calls and returns results. Claude reasons over the results and decides
> what to do next. That loop — that pattern — works for any domain. Cost analysis today.
> Infrastructure automation tomorrow. Incident response next week.
>
> [point at Elasticsearch]
>
> Third: Elastic as the agentic data layer. Elasticsearch isn't just a search engine here.
> It's the input — billing data and deploy events. It's the output — the audit trail.
> And with Kibana on top, it's the observability layer for the agent itself. One tool doing
> three jobs.
>
> [pause]
>
> The whole stack costs $3–5 a month to run. Less than a coffee. And it runs every morning
> before you're awake.
>
> Everything is in the GitHub repo — link in the description. The Kiro spec files are in
> `.kiro/`. Follow the setup guide in demo.md. If you get stuck, drop a comment.
>
> If this was useful, subscribe — next video I'm adding PagerDuty escalation for spikes above
> a configurable dollar threshold, and I'll show you how to test that with the same mock pattern
> we used today.
>
> See you in the next one."

**[OUTRO — fade out]**

---

## POST-PRODUCTION NOTES

### Chapter markers (YouTube)
```
00:00 The AWS bill problem
02:00 The finished Slack alert
03:30 Architecture overview
06:00 Kiro: spec-driven development
11:00 The agent loop and tool code
14:00 Elastic setup: billing + deploy data
17:00 Integration tests
18:30 Live demo
22:00 What you just learned + recap
```

### B-roll suggestions
- Architecture diagram (animated, show arrows flowing)
- AWS Cost Explorer screenshot with a realistic spike
- Terminal output scrolling during `seed_billing.py`
- Kiro file tree with `.kiro/` directory expanded

### Thumbnail
**Option A (drama):** Left half — AWS Cost Explorer spike. Right half — Slack message with root cause. Text: "AI caught my $800 AWS bill". Face in corner.
**Option B (tech):** Dark background. Three logos: Kiro + Bedrock + Elastic. Text: "Build a FinOps Agent in 25 min".
**Recommended:** Option A — emotion-driven thumbnails outperform tech logos 3:1 on DevOps channels.

### YouTube description template
```
I built an AI agent using Kiro IDE, Amazon Bedrock, and Elastic that automatically detects
AWS cost spikes, correlates them with deployments, and posts a root-cause Slack alert every morning.

In this video you'll learn how spec-driven development in Kiro works, how Bedrock's tool-calling
loop drives agentic reasoning, and how Elastic serves as both the data layer and audit trail
for the agent.

🔗 GitHub repo: [your link]
📖 Kiro docs: https://kiro.dev/docs/
📖 Elastic Cloud: https://elastic.co
📖 Amazon Bedrock Converse API: https://docs.aws.amazon.com/bedrock/latest/userguide/conversation-inference.html

#devops #aws #elasticsearch #amazonbedrock #kiro #finops #aiagent
```

---

## ENVIRONMENT VARIABLES REFERENCE

| Variable | Where to get it | Example |
|---|---|---|
| `ELASTIC_SECRET_ARN` | Output of `aws secretsmanager create-secret` for elastic-creds | `arn:aws:secretsmanager:us-east-1:123...` |
| `SLACK_SECRET_ARN` | Output of `aws secretsmanager create-secret` for slack-webhook | `arn:aws:secretsmanager:us-east-1:123...` |
| `AWS_BEDROCK_REGION` | Region where Bedrock model access is enabled | `us-east-1` |
| `SPIKE_THRESHOLD_PCT` | Percentage above baseline to flag as anomaly | `25.0` |
| `AGENT_MAX_ITERATIONS` | Hard cap on Bedrock converse loop iterations | `20` |
| `BEDROCK_MODEL_ID` | Cross-region inference profile — MUST use `us.` prefix | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` |
