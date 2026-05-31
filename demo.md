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
- [ ] AWS Billing integration set up in Elastic (Agentless, Healthy status confirmed)
- [ ] `metrics-aws.billing-*` and `deploy-events-*` indices EMPTY before recording — both seeded ON CAMERA in Segment 9
- [ ] API key `cost-anomaly-agent` created in Elastic with correct index privileges
- [ ] Slack `#finops` channel exists; incoming webhook URL tested (`curl` test returns `ok`)
- [ ] AWS Secrets Manager: `cost-anomaly-agent/elastic-creds` and `cost-anomaly-agent/slack-webhook` created
- [ ] IAM role `cost-anomaly-agent-lambda-role` created with inline policy (Bedrock + Secrets Manager + Marketplace)
- [ ] Lambda function `cost-anomaly-agent` deployed with correct env vars, 300s timeout, 256MB
- [ ] EventBridge cron rule created (not required to fire during recording — manual invoke is enough)
- [ ] `source .venv/bin/activate && make test` → 8/8 passing locally
- [ ] Bedrock model access active: `aws bedrock list-inference-profiles` shows `us.anthropic.claude-sonnet-4-5-20250929-v1:0  ACTIVE`

### Browser tabs (pre-load, pre-authenticated)
- [ ] Elastic → Discover → `metrics-aws.billing-*` (showing the spike data)
- [ ] Elastic → Dev Tools console
- [ ] Elastic → Dev Tools → `cost-anomaly-audit-*` search ready in Dev Tools
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
- [ ] Browser zoom: 110% for Elastic and AWS console
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

> **This is the only endpoint you need.** The agent code, API keys, seeding script, and Secrets Manager all use this `.es.` URL. The Elastic UI is built in — open it from your project page, no separate URL needed in code.

> **Port:** Serverless uses port **443** only. Do NOT append `:9243`.

#### 1c. Create the agent API key

**Elastic → Admin and settings → Access → API keys → Create API key**

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
ES_SUPERUSER_KEY=your-superuser-api-key         # from Elastic Admin settings — needed for seeding
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

# Generate access keys — copy both values, you will need them in Elastic
aws iam create-access-key --user-name elastic-billing-reader \
  --query 'AccessKey.{KeyId:AccessKeyId,Secret:SecretAccessKey}' \
  --output table
```

Save the **AccessKeyId** and **SecretAccessKey** — you enter these in Elastic next.

#### 3a-ii. Add AWS Billing integration in Elastic (Agentless)

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

Verify in **Elastic → Discover → `metrics-aws.billing-*`** — you should see the EC2 spike in the histogram.

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
Elastic Dev Tools:
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

### SEGMENT 1 — Cold Open: The Working Agent (0:00 – 1:30)

**Screen:** Slack `#finops` — show the Slack notification popping in. No intro. No title card. Just the message arriving.

**[editor note: start recording mid-demo. Lambda has already run. Switch straight to Slack.]**

**Spoken script:**
> "[no intro — just point at the screen]
>
> This just fired automatically. Let me show you what it says.
>
> EC2 cost spiked 76% today — $847, baseline was $592.
>
> Cause: a deploy went out at 14:00 UTC, 3 hours before the spike. The agent found it,
> correlated it, and wrote this in plain English.
>
> Fix: one line — reduce minReplicas to 3 in the HPA config. Saves $220 today.
>
> Footer: 23 seconds to run. Half a cent.
>
> [pause 1 second]
>
> An AI agent did this. Automatically. Every morning at 8am. No human touched it.
> By the end of this video you'll have built the same thing."

**[CUT — title card]**

---

### SEGMENT 2 — The Problem + What We're Building (1:30 – 3:00)

**Screen:** AWS Cost Explorer showing a cost spike — no context.

**Spoken script:**
> "Here's the problem this solves.
>
> You open your laptop on Monday morning. AWS bill spiked overnight. EC2 is up 43%.
> That's it — that's all you know. Now you've got 30 minutes of investigation: CloudWatch,
> recent deploys, HPA configs, which team owns the service.
>
> [pause]
>
> We're going to build an agent that does all of that automatically using three tools:
> Kiro IDE, Amazon Bedrock, and Elastic.
>
> Kiro writes the code from a spec. Bedrock is the AI reasoning engine — Claude deciding
> what to look at and why. Elastic is the data layer — billing data, deploy events, audit trail.
>
> Let me show you the architecture, then we'll build it live."

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

### SEGMENT 6 — Elastic Cloud Setup (14:00 – 16:00)

**Screen:** cloud.elastic.co → project → Manage → copy Elasticsearch endpoint

**Spoken script:**
> "Let's wire everything up live. First stop: Elastic Cloud.
>
> I've got a Serverless Observability project running. Click Manage, scroll down to
> Application endpoints — copy the Elasticsearch public endpoint. That's the only URL
> we need. the Elastic UI is built in — no separate endpoint.""

---

**Screen:** Elastic → Admin and settings → API keys → Create API key

> "Now create the API key the agent will use. Give it restricted privileges — read-only on
> the billing and deploy indices, write-only on the audit index. Copy the encoded value.
> That's the es_api_key."

> **[editor note: show creating key with the JSON privileges block from §1d]**

---

**[CUT]**

---

### SEGMENT 7 — AWS Infrastructure Setup (16:00 – 18:30)

**Screen:** Terminal

**Spoken script:**
> "Now the AWS side. Three things: Secrets Manager, IAM role, Lambda."

**Step 1 — Secrets Manager:**

```bash
ELASTIC_ARN=$(aws secretsmanager create-secret \
  --region us-east-1 \
  --name "cost-anomaly-agent/elastic-creds" \
  --secret-string "{\"es_url\":\"$ES_URL\",\"es_api_key\":\"$ES_API_KEY\"}" \
  --query 'ARN' --output text)

SLACK_ARN=$(aws secretsmanager create-secret \
  --region us-east-1 \
  --name "cost-anomaly-agent/slack-webhook" \
  --secret-string "{\"webhook_url\":\"$SLACK_WEBHOOK\"}" \
  --query 'ARN' --output text)
```

> "The Elastic endpoint and API key go into Secrets Manager — never hardcoded, never in
> environment variables directly. The Lambda reads them at runtime."

---

**Step 2 — IAM role + Lambda deploy:**

```bash
# Create role, attach policies, deploy Lambda zip
make zip
aws lambda create-function \
  --function-name cost-anomaly-agent \
  --runtime python3.12 \
  --role arn:aws:iam::$ACCOUNT_ID:role/cost-anomaly-agent-lambda-role \
  --handler agent.lambda_handler \
  --zip-file fileb://cost-anomaly-agent.zip \
  --timeout 300 --memory-size 256 \
  --environment "Variables={ELASTIC_SECRET_ARN=$ELASTIC_ARN,SLACK_SECRET_ARN=$SLACK_ARN,AWS_BEDROCK_REGION=us-east-1,SPIKE_THRESHOLD_PCT=25.0,BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-4-5-20250929-v1:0}"
```

> "Lambda deployed. 300 second timeout, 256 MB. The BEDROCK_MODEL_ID must have the 'us.' prefix
> — that's the cross-region inference profile. Without it you get a ValidationException."

---

**Screen:** Run integration tests

```bash
source .venv/bin/activate && make test
```

> "8 tests, all green. These mock out Bedrock entirely — testing the agent loop, tool dispatch,
> and Slack format without a single real API call. Under half a second."

**[CUT]**

---

### SEGMENT 8 — AWS Billing Integration (18:30 – 20:00)

**Screen:** Terminal — create IAM user for Elastic

```bash
aws iam create-user --user-name elastic-billing-reader

aws iam put-user-policy \
  --user-name elastic-billing-reader \
  --policy-name ElasticBillingReadOnly \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Action": ["ce:GetCostAndUsage","ce:GetTags","ce:GetDimensionValues",
                 "cloudwatch:GetMetricData","cloudwatch:ListMetrics",
                 "iam:ListAccountAliases","sts:GetCallerIdentity","tag:GetResources"],
      "Resource": "*"
    }]
  }'

aws iam create-access-key --user-name elastic-billing-reader \
  --query 'AccessKey.{ID:AccessKeyId,Secret:SecretAccessKey}' --output table
```

> "This IAM user is for the Elastic billing integration — not for the Lambda. It gives Elastic
> permission to call the AWS Cost Explorer API on our behalf. Copy the ID and secret."

---

**Screen:** Elastic Observability → Add data → Cloud → AWS → AWS Billing → Add AWS Billing

> "Now back in Elastic. Observability, Add data, Cloud, AWS, AWS Billing.
>
> [show the integration overview page]
>
> Look at what's included — 53 dashboards, 8 alerting templates, 40 ingest pipelines.
> All built in. The data lands in metrics-aws.billing-* which is exactly what our agent queries.
>
> Click Add AWS Billing."

**Screen:** Fill in the agentless form

> "Deployment mode: Agentless. Elastic runs the collector — nothing to install.
>
> Paste the Access Key ID and Secret from the IAM user we just created.
>
> Default settings work fine. The only thing to set: expand Advanced options,
> set Default AWS Region to us-east-1. Cost Explorer only lives in us-east-1 — leave
> this blank and the integration shows Healthy but writes nothing.
>
> Save and deploy."

**Screen:** Integration policies page → show agentless policy → **Healthy**

> "Healthy. The collector is running on Elastic's infrastructure, polling Cost Explorer
> every 24 hours by default.
>
> Now — this is a dev account used only for credentials. There's essentially zero AWS spend
> here, so Cost Explorer returns near-zero values. The agent would find no anomaly on real data.
>
> That's fine — in a production account with real EC2 spend, this integration feeds the index
> automatically. For this demo I'll seed production-scale data using the exact same field
> schema the integration writes."

**[CUT]**

---

### SEGMENT 9 — Seed, Invoke, Verify (20:00 – 23:00)

**Step 1 — Seed all data on camera**

**Screen:** Terminal

First seed the deploy events — replace the date with today's:

```bash
# Seed deploy events (use today's date)
TODAY=$(date -u +%Y.%m.%d)
TODAY_ISO=$(date -u +%Y-%m-%d)

curl -s -X POST "$ES_URL/deploy-events-$TODAY/_doc" \
  -H "Authorization: ApiKey $ES_SUPERUSER_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"service\":\"checkout\",\"version\":\"v2.3.1\",\"team\":\"checkout-team\",\"deployed_by\":\"alice@acme.com\",\"commit_sha\":\"a3f9c12d\",\"environment\":\"production\",\"@timestamp\":\"${TODAY_ISO}T14:00:00Z\"}"

curl -s -X POST "$ES_URL/deploy-events-$TODAY/_doc" \
  -H "Authorization: ApiKey $ES_SUPERUSER_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"service\":\"payment-service\",\"version\":\"v1.7.4\",\"team\":\"payments-team\",\"deployed_by\":\"carol@acme.com\",\"commit_sha\":\"c7d2f891\",\"environment\":\"production\",\"@timestamp\":\"${TODAY_ISO}T16:30:00Z\"}"

echo "✅ Deploy events seeded"
```

Then seed billing data:

```bash
source .venv/bin/activate
python3 scripts/seed_billing.py \
  --es-url "$ES_URL" \
  --api-key "$ES_SUPERUSER_KEY"
```

> "Two data sources. Deploy events first — these are the deployments the agent will correlate
> against. Then billing data — 7 days of EC2 baseline plus today's spike.
> Same field schema as the real AWS Billing integration. Watch it write."

**[PAUSE — let seeding scroll, ~60 seconds]**

> "768 billing documents, 2 deploy events. Let me verify in Elastic Discover."

**Screen:** Elastic → Discover → `metrics-aws.billing-*`

> "7 flat days at $25, today at $44 — 76% above baseline. Agent is ready."

---

**Step 2 — Invoke Lambda**

**Screen:** AWS Console → Lambda → `cost-anomaly-agent` → Test tab → enter `{"source":"manual-demo"}` → **Test**

> "Same event EventBridge sends every morning at 8am."

**[PAUSE — ~20 seconds]**

---

**Step 3 — Lambda logs**

**Screen:** Lambda → Monitor → Logs → most recent log stream

> "Status 200. Let me show you the execution logs."

> **[scroll through log lines — point at each tool call]**

> "find_spike_services — EC2 anomaly detected.
> get_cost_timeseries — 48 hours of hourly data retrieved.
> find_deploys_near_spike — deploy lookup.
> post_slack_alert — message sent.
> write_audit — audit record written.
> Agent completed at iteration 6. 23 seconds. About 22,000 tokens."

---

**Step 4 — Show Slack message**

**Screen:** Slack → `#finops`

**[ZOOM IN — point at each section]**

> "There's the alert. EC2 spike — [read actual dollar amounts from message].
>
> Cause: [read Claude's actual root cause analysis]
>
> Fix: [read the fix suggestion]
>
> Deploy: v2.3.1 by alice@acme.com — found 3 hours before the spike window.
>
> [pause]
>
> The agent did this: queried Elastic for billing data, compared 7 days of history,
> found the anomaly, pulled hourly timeseries, correlated with a deploy, wrote a root cause
> in plain English, posted to Slack, and wrote an audit record. Fully automated."

---

**Step 5 — Audit record**

**Screen:** Elastic → Dev Tools

```http
GET cost-anomaly-audit-*/_search
{
  "sort": [{"@timestamp": {"order": "desc"}}],
  "size": 1
}
```

> "anomalies_found: 1, slack_delivered: true, duration, token count.
> Every run lands here — success or failure. That's your paper trail for the agent itself."

**[CUT]**

---

### SEGMENT 10 — What You Just Learned (23:00 – 25:00)

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
> And with the Elastic UI on top, it's the observability layer for the agent itself. One tool doing
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
00:00 Cold open — Slack alert firing live
01:30 The problem + what we're building
03:00 Architecture overview
06:00 Kiro: spec-driven development
11:00 The agent loop and tool code
14:00 Elastic Cloud: endpoint + API key + deploy events
16:00 AWS infra: Secrets Manager + Lambda + IAM role + tests
18:30 AWS Billing integration: IAM → Elastic agentless → Healthy
20:00 Seed data → invoke Lambda → logs → Slack → audit
23:00 What you just learned
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
