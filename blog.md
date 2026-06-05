# Build an AI Agent That Detects AWS Cost Spikes and Posts Root-Cause Slack Alerts

AWS cost surprises are easy to miss. Usually you only find out in the morning standup, by which time a misconfigured autoscaler or forgotten EC2 instance has already run for hours. Most FinOps tools tell you that spending went up. They don't tell you why, or what to fix.

This article walks you through building an AI agent that runs every morning, detects billing anomalies, correlates them with recent deployments, and posts a Slack alert with a root cause and a fix estimate. The agent runs on AWS Lambda, uses Amazon Bedrock's Claude Sonnet 4 for reasoning, and stores billing data and audit records in Elastic Cloud Serverless. The total infrastructure cost is approximately $3-5/month.

---

## Summary of Key Concepts

| Concept | Description |
|---|---|
| Bedrock Converse tool-calling loop | The agent sends a system prompt and a list of registered Python functions to Amazon Bedrock. Claude picks which function to call, calls it, reads the result, and continues until it completes a 7-step sequence. No orchestration framework is needed. |
| Elastic Cloud Serverless as the data backbone | AWS billing metrics flow into Elastic via an agentless integration every 5 minutes. The agent queries billing and deploy-event indices to detect and explain anomalies. |
| Agentless AWS Billing integration | Elastic runs the billing data collector on their own infrastructure. There is no Elastic Agent binary to install or manage. |
| Least-privilege API key scoping | The Lambda API key has read access to billing and deploy indices, and write access only to the audit index. A broader key silently returns empty results on scoped queries, with no error to warn you. |
| Cross-region Bedrock inference profile | Bedrock requires the `us.` prefixed cross-region inference profile ID. The bare model ID returns a `ValidationException` with no other indication of what went wrong. |
| EventBridge daily trigger | A cron rule fires Lambda at 08:00 UTC every day. No polling, no always-on server needed. |

---

## Video Walkthrough

> `<!-- VIDEO EMBED -->`

---

## How the Agent Works

Here is the full architecture. EventBridge triggers Lambda every morning. Lambda runs a Bedrock Converse loop that calls four Python functions: querying Elasticsearch, posting to Slack, and writing an audit record.

![](<images/Screenshot From 2026-06-05 00-03-34.png>)

There is no orchestration framework involved. The loop is roughly 60 lines of Python driving the Bedrock Converse API directly. Claude reads the system prompt, decides which tool to call, gets the result back, and keeps going until it finishes a fixed 7-step sequence:

1. `find_spike_services`: find services above the spike threshold
2. `get_cost_timeseries`: pinpoint the exact hour the spike started
3. `find_deploys_near_spike`: check for deployments in the plus or minus 12-hour window
4. Reason about root cause in one sentence, under 30 words
5. Produce an actionable fix with a dollar estimate
6. `post_slack_alert`: post the alert to Slack
7. `write_audit`: write the run result to Elasticsearch

If no spike is found, the agent calls `write_audit` with zero anomalies and stops. It never posts to Slack when there is nothing to report.

---

## Building with Kiro IDE

The agent was designed using **Kiro**, an AI-powered IDE built on VS Code. The key idea behind Kiro is spec-driven development: you write requirements, a design document, and implementation tasks before writing any code. Kiro reads those files on every interaction, so the code it generates stays consistent with your full architecture rather than just the function you happen to be working on.

Download Kiro from [kiro.dev](https://kiro.dev) (free public preview). Open the project:

```bash
kiro .
```

The `.kiro/` directory in this repo has three components:

- **Steering files**: Kiro reads these on every interaction. `tech.md` covers the Bedrock model ID, Converse tool call format, and Elasticsearch client pattern. `product.md` and `structure.md` cover what the agent does and how files are organized.
- **Spec files**: `requirements.md` has 5 user stories, `design.md` has the architecture diagram and index schemas, and `tasks.md` has 17 implementation tasks, all checked off.
- **Hooks**: saving `agent.py` or any `tools/*.py` file automatically runs `pytest tests/test_integration.py`. No terminal switching needed.

---

## Part 1 — Set Up Elastic Cloud

### Option A: Subscribe via AWS Marketplace

If your organization bills through AWS, you can subscribe to Elastic Cloud directly from the AWS Marketplace. This routes Elastic charges through your AWS bill and keeps billing in one place.

Go to the [AWS Marketplace](https://aws.amazon.com/marketplace) and search for **Elastic Cloud**. You will land on the Elastic Cloud (Elasticsearch Service) product page.

![](<images/Screenshot From 2026-06-05 00-10-28.png>)

![](<images/Screenshot From 2026-06-05 00-10-40.png>)

Click **Subscribe**, then **Set up your account**. Review the purchase details. Pricing is usage-based with no upfront commitment.

![](<images/Screenshot From 2026-06-05 00-18-11.png>)

You will be redirected to `cloud.elastic.co` to complete setup. If an Elastic Cloud account is already linked to your AWS billing account, the login page shows a banner confirming the subscription already exists.

![](<images/Screenshot From 2026-06-05 00-18-30.png>)

After subscribing, your AWS Marketplace agreement page shows the status as **Active** and a **Set up your account** button that takes you into Elastic Cloud.

![](<images/Screenshot From 2026-06-05 00-19-02.png>)

### Create a Serverless Project

In Elastic Cloud, click **Create serverless project**.

![](<images/Screenshot From 2026-06-05 00-21-47.png>)

Choose **Elastic for Observability** as the project type. This type is required. It unlocks ML-powered log spike detection, SLOs, APM, and the AWS Billing data integration.

![](<images/Screenshot From 2026-06-05 00-22-09.png>)

Give the project a name and select **Observability Complete**. This tier includes the full ML and AIOps features. Choose your cloud provider and region. Try to match the region to where your Lambda will run to keep latency low.

![](<images/Screenshot From 2026-06-05 00-22-34.png>)

Click **Create project**. After about 60 seconds, the confirmation screen appears.

![](<images/Screenshot From 2026-06-05 00-22-58.png>)

### Find Your Elasticsearch Endpoint

From the project overview, click **Elasticsearch** under **Application endpoints, cluster and component IDs**. Copy the public endpoint URL.

![](<images/Screenshot From 2026-06-05 00-23-36.png>)

The format looks like this:
```
https://<project-id>.es.<region>.<provider>.elastic.cloud
```

Serverless uses port 443 only. Do not append `:9243`.

### Create the Agent API Key

Navigate to **Admin and settings > API keys > Create API key**.

![](<images/Screenshot From 2026-06-05 00-26-26.png>)

![](<images/Screenshot From 2026-06-05 00-26-56.png>)

Name the key `cost-anomaly-agent` and toggle on **Control security privileges**. Paste the JSON below. This gives Lambda exactly the access it needs: read on billing and deploy indices, and write-only on the audit index.

![](<images/Screenshot From 2026-06-05 00-27-34.png>)

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

Click **Create API key** and immediately copy the **Encoded** value. Elastic shows it only once.

![](<images/Screenshot From 2026-06-05 00-27-49.png>)

![](<images/Screenshot From 2026-06-05 00-27-58.png>)

### Create a Superuser Key for Data Seeding

You need a second, unrestricted key for the seeding script. This key is never stored in Lambda or Secrets Manager. It is used once to write demo billing data and then discarded.

Create another API key, name it `superuser`, and leave the privilege restrictions off.

![](<images/Screenshot From 2026-06-05 00-41-56.png>)

Copy the encoded value from the confirmation screen.

![](<images/Screenshot From 2026-06-05 00-42-06.png>)

---

## Part 2 — Set Up Slack

### Create a Slack App

Go to [api.slack.com/apps](https://api.slack.com/apps) and click **Create New App**.

![](<images/Screenshot From 2026-06-05 00-28-43.png>)

Name the app `cost anomaly agent`, choose your workspace, and click **Create App**.

![](<images/Screenshot From 2026-06-05 00-29-03.png>)

### Add an Incoming Webhook

In the left sidebar, click **Incoming Webhooks** and toggle **Activate Incoming Webhooks** on.

![](<images/Screenshot From 2026-06-05 00-29-36.png>)

Click **Add New Webhook to Workspace** and select the channel where you want alerts to appear. In this demo the `#general` channel is used. In production, use a dedicated `#finops` or `#alerts` channel so the alerts are easy to find and act on.

![](<images/Screenshot From 2026-06-05 00-29-46.png>)

After clicking **Allow**, your webhook URL appears on the page. Copy it. This is your `SLACK_WEBHOOK`.

![](<images/Screenshot From 2026-06-05 00-30-01.png>)

Test it before moving on:
```bash
curl -X POST https://hooks.slack.com/services/YOUR/WEBHOOK/URL \
  -H 'Content-type: application/json' \
  --data '{"text":"Webhook test - cost anomaly agent setup"}'
```

You should see `ok` in the response and a message appear in the channel.

---

## Part 3 — Set Shell Variables

Before running any AWS CLI commands, set these variables in your terminal. You will reuse them across all the steps that follow.

![](<images/Screenshot From 2026-06-05 00-41-24.png>)

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=us-east-1
ES_URL=https://your-project.es.us-central1.gcp.elastic.cloud
ES_API_KEY=VGVxNWdwNEJfUkQwNGRTYmxrbHE6dVZpb...   # restricted key from Part 1
ES_SUPERUSER_KEY=aC1yQmdwNEJfUkQwNGRTYmhrbDY6YzJS...  # superuser key from Part 1
SLACK_WEBHOOK=https://hooks.slack.com/services/T.../B.../...
```

![](<images/Screenshot From 2026-06-05 00-43-14.png>)

---

## Part 4 — Connect AWS Billing Data

Elastic pulls billing metrics from AWS Cost Explorer via an agentless integration. Elastic runs the collector on their own infrastructure. There is no Elastic Agent binary to install and no server to manage on your side.

### Create an IAM User for the Integration

This IAM user's credentials are given to Elastic so it can call the Cost Explorer API on your behalf.

```bash
aws iam create-user --user-name elastic-billing-reader
```

Attach a policy with read-only Cost Explorer and CloudWatch permissions:

```bash
aws iam put-user-policy \
  --user-name elastic-billing-reader \
  --policy-name ElasticBillingReadOnly \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Action": [
        "ce:GetCostAndUsage", "ce:GetTags", "ce:GetDimensionValues",
        "cloudwatch:GetMetricData", "cloudwatch:ListMetrics",
        "iam:ListAccountAliases", "sts:GetCallerIdentity", "tag:GetResources"
      ],
      "Resource": "*"
    }]
  }'
```

![](<images/Screenshot From 2026-06-05 00-43-43.png>)

Generate access keys and save both values. You will need them in the next step.

```bash
aws iam create-access-key --user-name elastic-billing-reader \
  --query 'AccessKey.{KeyId:AccessKeyId,Secret:SecretAccessKey}' \
  --output table
```

![](<images/Screenshot From 2026-06-05 00-44-34.png>)

### Add the AWS Billing Integration in Elastic

In Elastic, go to **Observability > Add data**. Select **Cloud** as the monitoring type.

![](<images/Screenshot From 2026-06-05 00-45-33.png>)

Choose **AWS** as the cloud provider.

![](<images/Screenshot From 2026-06-05 00-45-40.png>)

Search for `aws bill` in the integration search box and select **AWS Billing**.

![](<images/Screenshot From 2026-06-05 00-46-06.png>)

![](<images/Screenshot From 2026-06-05 00-46-14.png>)

Click **Add AWS Billing**. Fill in the **Access Key ID** and **Secret Access Key** from the previous step.

![](<images/Screenshot From 2026-06-05 00-47-08.png>)

For the deployment mode, select **Agentless**. Toggle **Collect billing metrics** on.

![](<images/Screenshot From 2026-06-05 00-47-17.png>)

Set **Collection Period** to `5m`. For **Cost Explorer Group By Dimension Keys**, keep only `SERVICE` and remove everything else. AWS Cost Explorer allows a maximum of two group keys. Using too many causes the API call to fail without any error message.

![](<images/Screenshot From 2026-06-05 00-47-49.png>)

Expand **Advanced options** and set **Default AWS Region** to `us-east-1`. This field is required because Cost Explorer's API endpoint only exists in `us-east-1`. If you leave it blank, the integration shows as Healthy but writes no data. There is no error message to clue you in.

![](<images/Screenshot From 2026-06-05 00-48-09.png>)

Click **Save and deploy**. The confirmation screen shows that agentless enrollment was successful.

![](<images/Screenshot From 2026-06-05 00-48-28.png>)

### Verify Billing Data in Elastic Discover

After the integration deploys, create a data view so you can browse the incoming billing data in Discover.

In Elastic, go to **Discover** and create a data view named `billing data` with the index pattern `metrics-aws.billing-default`.

![](<images/Screenshot From 2026-06-05 00-49-35.png>)

Switch to the `billing data` view in Discover.

![](<images/Screenshot From 2026-06-05 00-49-49.png>)

You should see billing documents arriving in the histogram. On a development account the values will be near zero, which is why the next step seeds realistic data for a meaningful test run.

![](<images/Screenshot From 2026-06-05 00-50-14.png>)

### Seed Realistic Billing Data for Testing

A development AWS account has near-zero spend, so the agent finds no anomaly on its own. The repo includes a script that writes 7 days of baseline billing data plus an EC2 spike for today, using the same field schema the integration writes:

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
Done. 768 documents written to metrics-aws.billing-*
   Expected pct_change: ~76% above 7-day baseline
```

In production this index is populated automatically by the integration every 5 minutes with real Cost Explorer data. No manual seeding step is needed once you have real spend flowing.

---

## Part 5 — Enable Amazon Bedrock

Go to **AWS Console > Amazon Bedrock > Bedrock configurations > Model access**. The model catalog shows all available Claude models. Enable **Claude Sonnet 4** or the latest Sonnet model available in your region.

![](<images/Screenshot From 2026-06-05 00-52-00.png>)

After enabling, verify that the cross-region inference profile is active:

```bash
aws bedrock list-inference-profiles --region us-east-1 \
  --query 'inferenceProfileSummaries[?contains(inferenceProfileId,`sonnet-4-5`)].[inferenceProfileId,status]' \
  --output table
```

![](<images/Screenshot From 2026-06-05 00-52-47.png>)

The output should show both profile IDs as **ACTIVE**.

![](<images/Screenshot From 2026-06-05 00-52-59.png>)

Always use the cross-region profile ID with the `us.` prefix in your Lambda environment variable. This is a common gotcha:

```
# Correct
us.anthropic.claude-sonnet-4-5-20250929-v1:0

# Wrong - returns ValidationException
anthropic.claude-sonnet-4-5
```

---

## Part 6 — Deploy to Lambda

### Store Credentials in Secrets Manager

Never put credentials directly in Lambda environment variables. Store them in Secrets Manager and retrieve them at runtime. This keeps credentials out of your deployment package and out of your version control history.

```bash
ELASTIC_ARN=$(aws secretsmanager create-secret \
  --region $REGION \
  --name "cost-anomaly-agent/elastic-creds" \
  --secret-string "{\"es_url\":\"$ES_URL\",\"es_api_key\":\"$ES_API_KEY\"}" \
  --query 'ARN' --output text)

SLACK_ARN=$(aws secretsmanager create-secret \
  --region $REGION \
  --name "cost-anomaly-agent/slack-webhook" \
  --secret-string "{\"webhook_url\":\"$SLACK_WEBHOOK\"}" \
  --query 'ARN' --output text)
```

![](<images/Screenshot From 2026-06-05 00-54-11.png>)

![](<images/Screenshot From 2026-06-05 00-57-40.png>)

![](<images/Screenshot From 2026-06-05 00-57-47.png>)

![](<images/Screenshot From 2026-06-05 00-58-13.png>)

### Create the IAM Role

```bash
aws iam create-role \
  --role-name "cost-anomaly-agent-lambda-role" \
  --assume-role-policy-document '{
    "Version":"2012-10-17",
    "Statement":[{
      "Effect":"Allow",
      "Principal":{"Service":"lambda.amazonaws.com"},
      "Action":"sts:AssumeRole"
    }]
  }'
```

![](<images/Screenshot From 2026-06-05 00-58-42.png>)

![](<images/Screenshot From 2026-06-05 00-58-56.png>)

Attach an inline policy granting Bedrock invocation and Secrets Manager read access:

```bash
cat > /tmp/policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "BedrockInvoke",
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream",
        "bedrock:Converse",
        "bedrock:ConverseStream"
      ],
      "Resource": "*"
    },
    {
      "Sid": "SecretsManagerRead",
      "Effect": "Allow",
      "Action": "secretsmanager:GetSecretValue",
      "Resource": [
        "arn:aws:secretsmanager:${REGION}:${ACCOUNT_ID}:secret:cost-anomaly-agent/elastic-creds-*",
        "arn:aws:secretsmanager:${REGION}:${ACCOUNT_ID}:secret:cost-anomaly-agent/slack-webhook-*"
      ]
    }
  ]
}
EOF

aws iam put-role-policy \
  --role-name "cost-anomaly-agent-lambda-role" \
  --policy-name "cost-anomaly-agent-inline" \
  --policy-document file:///tmp/policy.json
```

### Build and Deploy

Clone the repo, create a virtual environment, and install dependencies. Run `pip install` from inside the project directory so Python can find `requirements.txt`.

```bash
git clone https://github.com/piyushsachdeva/elastic-cost-analyzer.git
cd elastic-cost-analyzer
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

![](<images/Screenshot From 2026-06-05 01-00-14.png>)

![](<images/Screenshot From 2026-06-05 01-00-53.png>)

Build the deployment package and confirm the size looks reasonable:

```bash
make test    # 8/8 tests should pass
make zip
ls -lh cost-anomaly-agent.zip   # expect 6-15 MB
```

![](<images/Screenshot From 2026-06-05 01-01-18.png>)

Now deploy the Lambda function. Wait 15 seconds after creating the IAM role first. IAM changes need a moment to propagate globally before Lambda can assume the role.

```bash
sleep 15

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

![](<images/Screenshot From 2026-06-05 01-01-33.png>)

Once the deployment completes, the function appears in the AWS Lambda console.

![](<images/Screenshot From 2026-06-05 01-05-07.png>)

### Schedule the Daily Run with EventBridge

```bash
aws events put-rule \
  --region $REGION \
  --name "cost-anomaly-agent-daily" \
  --schedule-expression "cron(0 8 * * ? *)" \
  --state ENABLED \
  --description "Trigger cost anomaly agent every morning at 08:00 UTC"

LAMBDA_ARN=$(aws lambda get-function \
  --function-name cost-anomaly-agent \
  --region $REGION \
  --query 'Configuration.FunctionArn' --output text)

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
  --source-arn "$(aws events describe-rule \
      --name cost-anomaly-agent-daily \
      --region $REGION --query 'Arn' --output text)"
```

![](<images/Screenshot From 2026-06-05 01-06-48.png>)

When you open the EventBridge console, you will notice that scheduled rules have moved to a separate **Scheduler** section. The **Scheduled rules** tab shows as empty and displays a banner pointing you there. This is expected behavior.

![](<images/Screenshot From 2026-06-05 01-08-33.png>)

![](<images/Screenshot From 2026-06-05 01-08-42.png>)

Go to **EventBridge > Scheduler > Schedules** to find your rule. It shows the cron expression `0 8 * * ? *` and the next 10 trigger dates so you can confirm it is scheduled correctly.

![](<images/Screenshot From 2026-06-05 01-09-01.png>)

---

## Part 7 — Verify End-to-End

### Trigger Lambda Manually

In the AWS Console, go to **Lambda > cost-anomaly-agent > Test**. Enter this event body:

![](<images/Screenshot From 2026-06-05 01-10-00.png>)

```json
{"source": "manual-test"}
```

Click **Test**.

![](<images/Screenshot From 2026-06-05 01-10-10.png>)

A successful run returns a 200 status code with the run ID, duration in seconds, and token counts. The run shown below completed in 12.54 seconds using 6,837 total tokens.

![](<images/Screenshot From 2026-06-05 01-10-25.png>)

The execution log shows each tool call in order: `find_spike_services`, `find_deploys_near_spike`, then `write_audit`.

![](<images/Screenshot From 2026-06-05 01-10-35.png>)

### Check the Slack Alert

Go to the channel you configured. A Block Kit message should be waiting. In this run the agent detected an EC2 anomaly: today's spend was $1,056.21, which is 98.4% above the 7-day baseline of $532.30/day, a delta of $523.92. The message includes a one-sentence root cause and a specific suggested fix with an estimated saving.

![](<images/Screenshot From 2026-06-05 01-11-11.png>)

### Check the CloudWatch Logs

Go to **CloudWatch > Log groups > /aws/lambda/cost-anomaly-agent**. The logs show the full tool-calling sequence with timestamps for each iteration, which is useful for debugging if a run fails or takes longer than expected.

![](<images/Screenshot From 2026-06-05 01-15-13.png>)

### Check the Elastic Audit Record

Open **Dev Tools** in Elastic and run:

```http
GET cost-anomaly-audit-*/_search
{
  "sort": [{"@timestamp": {"order": "desc"}}],
  "size": 1
}
```

The response confirms the run completed. You can see `anomalies_found: 1`, `slack_delivered: true`, and `status: "success"`. The `token_count` and `duration_seconds` fields come in handy for tracking agent cost and performance over time.

![](<images/Screenshot From 2026-06-05 01-15-37.png>)

---

## Conclusion

You now have a working FinOps agent that detects billing spikes, correlates them with deployment events, and delivers an actionable Slack alert every morning without any manual intervention. The decisions that make it reliable in production are the least-privilege API key, the cross-region Bedrock inference profile, the agentless billing integration with the `us-east-1` region set correctly, and credentials stored in Secrets Manager rather than in environment variables.

The full source code is at [github.com/piyushsachdeva/elastic-cost-analyzer](https://github.com/piyushsachdeva/elastic-cost-analyzer).
