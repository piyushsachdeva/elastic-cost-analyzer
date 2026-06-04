# Cloud Cost Anomaly Agent — Demo & Setup Guide

**Video title:** "I built an AI agent that catches AWS cost spikes — here's exactly how"  
**Goal:** Build a FinOps agent using **Kiro IDE + Amazon Bedrock + Elastic** that detects AWS cost anomalies, correlates them with deployments, and posts a root-cause Slack alert — automatically, every morning.

**What viewers will learn:**
1. How to use Kiro IDE's spec-driven workflow to design and build an AI agent
2. How the Amazon Bedrock Converse API's tool-calling loop works in practice
3. How to use Elasticsearch as both the data backbone and audit trail for an agentic app
4. How to test AI agents with mocked LLM responses (no real API calls needed)
5. How to wire three cloud services into a production agent that costs ~$3–5/month to run


## FULL INFRASTRUCTURE SETUP

Follow this section to build everything from scratch. 

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
git clone https://github.com/piyushsachdeva/elastic-cost-analyzer.git
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

#### 3a-iii. Seed billing data for demo 

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

> **Note:** *"In production, this index is populated automatically by the Elastic AWS Billing integration every 5 minutes — real Cost Explorer data, no manual step. For this demo I seeded data at production EC2 scale so you can see the agent actually fire."*

---

#### 3b. Enable Amazon Bedrock model access

**AWS Console → Amazon Bedrock → left sidebar: Bedrock configurations → Model access**

Note: Below steps for the first time using bedrock

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

cat > /tmp/policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {"Sid":"BedrockInvoke","Effect":"Allow",
     "Action":["bedrock:InvokeModel","bedrock:InvokeModelWithResponseStream","bedrock:Converse","bedrock:ConverseStream"],
     "Resource":"*"},
    {"Sid":"BedrockMarketplace","Effect":"Allow",
     "Action":["aws-marketplace:ViewSubscriptions","aws-marketplace:Subscribe","aws-marketplace:Unsubscribe"],
     "Resource":"*"},
    {"Sid":"SecretsManagerRead","Effect":"Allow",
     "Action":"secretsmanager:GetSecretValue",
     "Resource":[
       "arn:aws:secretsmanager:${REGION}:${ACCOUNT_ID}:secret:cost-anomaly-agent/elastic-creds-*",
       "arn:aws:secretsmanager:${REGION}:${ACCOUNT_ID}:secret:cost-anomaly-agent/slack-webhook-*"
     ]}
  ]
}
EOF

aws iam put-role-policy \
  --role-name "cost-anomaly-agent-lambda-role" \
  --policy-name "cost-anomaly-agent-inline" \
  --policy-document file:///tmp/policy.json
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
  --environment "Variables={ELASTIC_SECRET_ARN=$ELASTIC_ARN,SLACK_SECRET_ARN=$SLACK_ARN,AWS_BEDROCK_REGION=$REGION,SPIKE_THRESHOLD_PCT=25.0,AGENT_MAX_ITERATIONS=20,BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-4-5-20250929-v1:0}"
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
