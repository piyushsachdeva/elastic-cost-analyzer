"""
agent.py — Cloud Cost Anomaly Agent
Entry point for AWS Lambda. Runs a Bedrock converse loop that detects
AWS cost spikes, correlates them with deploy events, and posts a root-cause
Slack alert.
"""

import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import boto3

from tools.elastic_search import find_spike_services, get_cost_timeseries, find_deploys_near_spike
from tools.slack_notify import post_slack_alert
from tools.audit_writer import write_audit

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Configuration — all values come from Lambda environment variables
# ---------------------------------------------------------------------------
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
MAX_ITERATIONS = int(os.environ.get("AGENT_MAX_ITERATIONS", "20"))
SPIKE_THRESHOLD_PCT = float(os.environ.get("SPIKE_THRESHOLD_PCT", "25.0"))
BEDROCK_REGION = os.environ.get("AWS_BEDROCK_REGION", "us-east-1")

# ---------------------------------------------------------------------------
# System prompt — defines Claude's reasoning sequence for every run
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are a FinOps AI agent. Every morning you detect AWS cost anomalies,
find their root cause by correlating with recent deployments, and post one Slack alert.

Follow this exact 7-step sequence every run:

Step 1: Call find_spike_services with the provided threshold_pct.
        If the result list is empty, call write_audit with anomalies_found=0,
        slack_delivered=false, then stop. Do NOT post to Slack when there are no anomalies.

Step 2: For each spiked service, call get_cost_timeseries with hours=48.
        Identify the exact hour the spike started (first hour where cost exceeded
        1.5x the prior 6-hour rolling average). Record this as spike_start_iso.

Step 3: Call find_deploys_near_spike for each spiked service using the
        spike_start_iso from step 2 and window_hours=12.

Step 4: Reason about the most likely cause. One sentence, under 30 words.
        Cite the specific technical reason (HPA misconfiguration, traffic spike,
        data transfer, orphaned resource). Do not write vague summaries.
        Example: "HPA scaled checkout pods 3→12 replicas 6h after deploy v2.3.1
        at 14:00 UTC — CPU utilisation held at 18%, minReplicas set too high."

Step 5: Write one actionable fix, under 25 words, with an estimated dollar saving.
        Example: "Reduce minReplicas to 3 in checkout-service/k8s/hpa.yaml.
        Estimated saving if fixed today: ~$220."

Step 6: Call post_slack_alert with all anomalies sorted by delta_usd descending,
        the causes list, the suggestions list, and run_meta containing the run_id
        and duration_seconds so far.

Step 7: Call write_audit with the complete run result. Always call this,
        even if Slack delivery failed.

Constraints:
- Never fabricate numbers. Only report what the tool results contain.
- If a deploy lookup returns no results, say "No deploy found in ±12h window."
- Keep all text concise — this message goes to engineers who are busy.
"""

# ---------------------------------------------------------------------------
# Tool definitions — registered with Bedrock so Claude can call them
# ---------------------------------------------------------------------------
BEDROCK_TOOL_DEFINITIONS: list[dict] = [
    {
        "toolSpec": {
            "name": "find_spike_services",
            "description": (
                "Compare today's AWS spend against the 7-day rolling average per service. "
                "Returns services where today's cost exceeds the threshold percentage above baseline. "
                "Returns an empty list if no anomalies are found."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "threshold_pct": {
                            "type": "number",
                            "description": (
                                "Percentage above 7-day baseline that counts as a spike. "
                                "E.g. 25.0 flags anything up 25% or more."
                            ),
                        }
                    },
                    "required": ["threshold_pct"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "get_cost_timeseries",
            "description": (
                "Get hourly cost data for a specific AWS service over the past N hours. "
                "Use to pinpoint exactly when a spike started."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "service": {
                            "type": "string",
                            "description": "AWS service name as it appears in billing data. E.g. 'Amazon EC2'.",
                        },
                        "hours": {
                            "type": "integer",
                            "description": "Hours of history to retrieve. Use 48 for a 2-day view.",
                        },
                    },
                    "required": ["service", "hours"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "find_deploys_near_spike",
            "description": (
                "Search the deploy-events index for any deployments to this service "
                "within a time window around the spike start. Returns deploy metadata: "
                "version, team, deployed_by, commit_sha, hours_before_spike."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "service": {
                            "type": "string",
                            "description": "Service name to look up in deploy history.",
                        },
                        "spike_start_iso": {
                            "type": "string",
                            "description": "ISO 8601 UTC timestamp of when the spike started. E.g. '2025-05-25T14:00:00Z'.",
                        },
                        "window_hours": {
                            "type": "integer",
                            "description": "Search this many hours before AND after spike_start_iso. Default 12.",
                        },
                    },
                    "required": ["service", "spike_start_iso", "window_hours"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "post_slack_alert",
            "description": (
                "Post a formatted cost anomaly Block Kit message to the #finops Slack channel. "
                "Include all anomalies sorted by delta_usd descending."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "anomalies": {
                            "type": "array",
                            "description": (
                                "List of anomaly dicts. Each must have: "
                                "service, team, today_usd, baseline_usd, delta_usd, pct_change."
                            ),
                            "items": {"type": "object"},
                        },
                        "causes": {
                            "type": "array",
                            "description": "One cause string per anomaly, in the same order.",
                            "items": {"type": "string"},
                        },
                        "suggestions": {
                            "type": "array",
                            "description": "One fix suggestion per anomaly, in the same order.",
                            "items": {"type": "string"},
                        },
                        "run_meta": {
                            "type": "object",
                            "description": "Dict with run_id (str) and duration_seconds (float).",
                        },
                    },
                    "required": ["anomalies", "causes", "suggestions", "run_meta"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "write_audit",
            "description": (
                "Write an audit record to cost-anomaly-audit-YYYY.MM.dd. "
                "Always call this at the end of every run."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "run_id": {"type": "string"},
                        "anomalies_found": {"type": "integer"},
                        "slack_delivered": {"type": "boolean"},
                        "duration_seconds": {"type": "number"},
                        "token_count": {"type": "integer"},
                        "error": {
                            "type": "string",
                            "description": "Error message if the run failed. Omit on success.",
                        },
                    },
                    "required": [
                        "run_id",
                        "anomalies_found",
                        "slack_delivered",
                        "duration_seconds",
                        "token_count",
                    ],
                }
            },
        }
    },
]


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------
class CloudCostAnomalyAgent:
    """
    Runs a Bedrock converse loop to detect AWS cost anomalies and post
    a root-cause Slack alert.
    """

    def __init__(self) -> None:
        self.run_id: str = str(uuid.uuid4())
        self.start_time: float = time.monotonic()
        self.bedrock = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
        self.messages: list[dict] = []
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        logger.info("Agent initialised", extra={"run_id": self.run_id})

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------
    def run(self) -> dict[str, Any]:
        """
        Main loop. Returns a Lambda-compatible response dict.
        """
        logger.info(
            "Run started",
            extra={"run_id": self.run_id, "threshold_pct": SPIKE_THRESHOLD_PCT},
        )

        self.messages = [self._build_initial_message()]

        for iteration in range(MAX_ITERATIONS):
            logger.info("Iteration %d/%d", iteration + 1, MAX_ITERATIONS, extra={"run_id": self.run_id})

            response = self._invoke_bedrock()
            stop_reason: str = response["stopReason"]
            output_message: dict = response["output"]["message"]
            self.messages.append(output_message)

            usage = response.get("usage", {})
            self.total_input_tokens += usage.get("inputTokens", 0)
            self.total_output_tokens += usage.get("outputTokens", 0)

            if stop_reason == "end_turn":
                logger.info(
                    "Agent completed at iteration %d", iteration + 1,
                    extra={"run_id": self.run_id},
                )
                break

            if stop_reason == "tool_use":
                tool_results = self._process_tool_calls(output_message["content"])
                self.messages.append({"role": "user", "content": tool_results})
            else:
                logger.warning(
                    "Unexpected stop_reason '%s' — stopping loop", stop_reason,
                    extra={"run_id": self.run_id},
                )
                break
        else:
            logger.error(
                "Hit max iterations (%d) without end_turn", MAX_ITERATIONS,
                extra={"run_id": self.run_id},
            )

        duration = round(time.monotonic() - self.start_time, 2)
        total_tokens = self.total_input_tokens + self.total_output_tokens

        logger.info(
            "Run finished",
            extra={
                "run_id": self.run_id,
                "duration_seconds": duration,
                "input_tokens": self.total_input_tokens,
                "output_tokens": self.total_output_tokens,
            },
        )

        return {
            "statusCode": 200,
            "body": {
                "run_id": self.run_id,
                "duration_seconds": duration,
                "input_tokens": self.total_input_tokens,
                "output_tokens": self.total_output_tokens,
                "total_tokens": total_tokens,
            },
        }

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------
    def _build_initial_message(self) -> dict:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return {
            "role": "user",
            "content": [
                {
                    "text": (
                        f"Analyse AWS cost anomalies for {today}. "
                        f"Spike threshold: {SPIKE_THRESHOLD_PCT}%. "
                        "Follow your 7-step reasoning sequence."
                    )
                }
            ],
        }

    def _invoke_bedrock(self) -> dict:
        return self.bedrock.converse(
            modelId=MODEL_ID,
            system=[{"text": SYSTEM_PROMPT}],
            messages=self.messages,
            toolConfig={"tools": BEDROCK_TOOL_DEFINITIONS},
            inferenceConfig={"maxTokens": 4096, "temperature": 0},
        )

    def _process_tool_calls(self, content_blocks: list[dict]) -> list[dict]:
        """
        Execute every toolUse block in a response and return a list of
        toolResult blocks ready to send back to Bedrock.

        Bedrock Converse API wraps tool calls under a "toolUse" key:
          {"toolUse": {"toolUseId": "...", "name": "...", "input": {...}}}

        Tool results are sent back in the matching nested format:
          {"toolResult": {"toolUseId": "...", "content": [...]}}
        """
        results: list[dict] = []

        for block in content_blocks:
            # Bedrock Converse wraps tool calls under "toolUse" key
            if "toolUse" not in block:
                continue

            tool_data = block["toolUse"]
            tool_name: str = tool_data["name"]
            tool_input: dict = tool_data["input"]
            tool_use_id: str = tool_data["toolUseId"]

            logger.info(
                "Tool call → %s", tool_name,
                extra={"run_id": self.run_id, "input": tool_input},
            )

            try:
                result = self._dispatch_tool(tool_name, tool_input)
                logger.info(
                    "Tool result ← %s", tool_name,
                    extra={"run_id": self.run_id, "result": result},
                )
                results.append({
                    "toolResult": {
                        "toolUseId": tool_use_id,
                        "content": [{"json": result}],
                    }
                })
            except Exception as exc:
                logger.error(
                    "Tool %s raised %s: %s", tool_name, type(exc).__name__, exc,
                    extra={"run_id": self.run_id},
                    exc_info=True,
                )
                results.append({
                    "toolResult": {
                        "toolUseId": tool_use_id,
                        "status": "error",
                        "content": [{"text": f"{type(exc).__name__}: {exc}"}],
                    }
                })

        return results

    def _dispatch_tool(self, tool_name: str, tool_input: dict) -> Any:
        """Route a tool call to its Python implementation."""
        handlers: dict[str, Any] = {
            "find_spike_services": lambda i: find_spike_services(
                threshold_pct=i["threshold_pct"]
            ),
            "get_cost_timeseries": lambda i: get_cost_timeseries(
                service=i["service"], hours=i["hours"]
            ),
            "find_deploys_near_spike": lambda i: find_deploys_near_spike(
                service=i["service"],
                spike_start_iso=i["spike_start_iso"],
                window_hours=i.get("window_hours", 12),
            ),
            "post_slack_alert": lambda i: post_slack_alert(
                anomalies=i["anomalies"],
                causes=i["causes"],
                suggestions=i["suggestions"],
                run_meta=i["run_meta"],
            ),
            "write_audit": lambda i: write_audit(
                run_id=i["run_id"],
                anomalies_found=i["anomalies_found"],
                slack_delivered=i["slack_delivered"],
                duration_seconds=i["duration_seconds"],
                token_count=i["token_count"],
                error=i.get("error"),
            ),
        }

        handler = handlers.get(tool_name)
        if handler is None:
            raise ValueError(f"Unknown tool '{tool_name}'. Registered tools: {list(handlers)}")

        return handler(tool_input)


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------
def lambda_handler(event: dict, context: Any) -> dict:
    """
    AWS Lambda handler. Accepts any trigger event (EventBridge, API Gateway,
    manual invoke). All logic is in CloudCostAnomalyAgent.run().
    """
    logger.info("Lambda invoked", extra={"event": event})
    agent = CloudCostAnomalyAgent()
    return agent.run()
