"""
tests/test_integration.py — Integration test for the full agent loop.

Injects a fake EC2 spike + v2.3.1 deploy, runs agent.run() against
mocked AWS and Elastic, and asserts the full 7-step chain completed correctly.
"""

import json
import os
import unittest
from unittest.mock import MagicMock, patch, call

# Set required env vars before importing agent
os.environ.setdefault("ELASTIC_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:123456789:secret:test-elastic")
os.environ.setdefault("SLACK_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:123456789:secret:test-slack")
os.environ.setdefault("SPIKE_THRESHOLD_PCT", "25.0")
os.environ.setdefault("AGENT_MAX_ITERATIONS", "20")
os.environ.setdefault("AWS_BEDROCK_REGION", "us-east-1")

from agent import CloudCostAnomalyAgent, lambda_handler  # noqa: E402


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------
FAKE_SPIKE = {
    "service": "Amazon EC2",
    "team": "checkout-team",
    "today_usd": 847.20,
    "baseline_usd": 592.10,
    "delta_usd": 255.10,
    "pct_change": 43.1,
}

FAKE_TIMESERIES = [
    {"timestamp": "2025-05-25T08:00:00Z", "cost_usd": 24.63},
    {"timestamp": "2025-05-25T09:00:00Z", "cost_usd": 24.71},
    {"timestamp": "2025-05-25T10:00:00Z", "cost_usd": 25.02},
    {"timestamp": "2025-05-25T11:00:00Z", "cost_usd": 24.98},
    {"timestamp": "2025-05-25T12:00:00Z", "cost_usd": 25.10},
    {"timestamp": "2025-05-25T13:00:00Z", "cost_usd": 25.04},
    {"timestamp": "2025-05-25T14:00:00Z", "cost_usd": 25.03},  # deploy at 14:00
    {"timestamp": "2025-05-25T15:00:00Z", "cost_usd": 26.11},
    {"timestamp": "2025-05-25T16:00:00Z", "cost_usd": 28.44},
    {"timestamp": "2025-05-25T17:00:00Z", "cost_usd": 34.92},  # spike starts here
    {"timestamp": "2025-05-25T18:00:00Z", "cost_usd": 41.20},
    {"timestamp": "2025-05-25T19:00:00Z", "cost_usd": 43.87},
    {"timestamp": "2025-05-25T20:00:00Z", "cost_usd": 44.01},
]

FAKE_DEPLOY = {
    "service": "checkout",
    "version": "v2.3.1",
    "team": "checkout-team",
    "deployed_by": "alice@acme.com",
    "commit_sha": "a3f9c12d",
    "timestamp": "2025-05-25T14:00:00Z",
    "hours_before_spike": 3.0,
}

FAKE_AUDIT_RESULT = {"indexed": True, "index": "cost-anomaly-audit-2025.05.25", "doc_id": "abc123", "error": None}


# ---------------------------------------------------------------------------
# Bedrock converse mock — simulates Claude's 7-step tool-calling sequence
# ---------------------------------------------------------------------------
def _make_bedrock_converse_side_effect(has_anomalies: bool = True):
    """
    Returns a function that simulates Claude's tool-calling sequence.
    Each call corresponds to one turn in the converse loop.

    has_anomalies=True  → full 5-tool sequence (find_spike → timeseries →
                          deploys → post_slack_alert → write_audit)
    has_anomalies=False → short 2-tool sequence (find_spike → write_audit,
                          no Slack call)
    """
    turn = {"n": 0}

    def side_effect(**kwargs):
        turn["n"] += 1
        n = turn["n"]

        def tool_use_response(tool_use_id, name, tool_input):
            # Bedrock Converse API uses nested format: {"toolUse": {...}}
            return {
                "stopReason": "tool_use",
                "output": {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"toolUse": {"toolUseId": tool_use_id, "name": name, "input": tool_input}}
                        ],
                    }
                },
                "usage": {"inputTokens": 500, "outputTokens": 200},
            }

        def end_turn_response():
            return {
                "stopReason": "end_turn",
                "output": {"message": {"role": "assistant", "content": [{"text": "Done."}]}},
                "usage": {"inputTokens": 100, "outputTokens": 20},
            }

        if not has_anomalies:
            # No anomalies: step 1 = find_spike_services, step 2 = write_audit, step 3 = end_turn
            if n == 1:
                return tool_use_response("tu-1", "find_spike_services", {"threshold_pct": 25.0})
            elif n == 2:
                return tool_use_response("tu-2", "write_audit", {
                    "run_id": "test-run-id",
                    "anomalies_found": 0,
                    "slack_delivered": False,
                    "duration_seconds": 1.1,
                    "token_count": 600,
                })
            else:
                return end_turn_response()

        # Full anomaly sequence
        if n == 1:
            return tool_use_response("tu-1", "find_spike_services", {"threshold_pct": 25.0})
        elif n == 2:
            return tool_use_response("tu-2", "get_cost_timeseries", {"service": "Amazon EC2", "hours": 48})
        elif n == 3:
            return tool_use_response("tu-3", "find_deploys_near_spike", {
                "service": "checkout",
                "spike_start_iso": "2025-05-25T17:00:00Z",
                "window_hours": 12,
            })
        elif n == 4:
            return tool_use_response("tu-4", "post_slack_alert", {
                "anomalies": [FAKE_SPIKE],
                "causes": ["HPA scaled checkout pods 3→12 replicas 3h after deploy v2.3.1 at 14:00 UTC. CPU held at 18% — minReplicas too high."],
                "suggestions": ["Reduce minReplicas to 3 in checkout-service/k8s/hpa.yaml. Saves ~$220 today."],
                "run_meta": {"run_id": "test-run-id", "duration_seconds": 12.5, "total_tokens": 2800},
            })
        elif n == 5:
            return tool_use_response("tu-5", "write_audit", {
                "run_id": "test-run-id",
                "anomalies_found": 1,
                "slack_delivered": True,
                "duration_seconds": 13.1,
                "token_count": 2800,
            })
        else:
            return end_turn_response()

    return side_effect


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestAgentIntegration(unittest.TestCase):

    def _run_agent_with_mocks(self, spike_list=None):
        """Helper: patch all external calls and run the agent."""
        has_anomalies = spike_list is None or len(spike_list) > 0
        if spike_list is None:
            spike_list = [FAKE_SPIKE]

        with (
            # Patch at the agent module level — that's where functions were imported into
            patch("agent.find_spike_services", return_value=spike_list) as mock_spikes,
            patch("agent.get_cost_timeseries", return_value=FAKE_TIMESERIES) as mock_ts,
            patch("agent.find_deploys_near_spike", return_value=[FAKE_DEPLOY]) as mock_deploys,
            # post_slack_alert and write_audit run through real functions but with mocked internals
            patch("tools.slack_notify._get_webhook_url", return_value="https://hooks.slack.com/test"),
            patch("tools.slack_notify.urllib.request.urlopen") as mock_urlopen,
            patch("tools.audit_writer._get_es_client") as mock_audit_es,
            patch("boto3.client") as mock_boto,
        ):
            # Mock Slack HTTP response
            mock_http_resp = MagicMock()
            mock_http_resp.__enter__ = lambda s: s
            mock_http_resp.__exit__ = MagicMock(return_value=False)
            mock_http_resp.status = 200
            mock_http_resp.read.return_value = b"ok"
            mock_urlopen.return_value = mock_http_resp

            # Mock audit ES index call
            mock_audit_es.return_value.index.return_value = {"_id": "audit-doc-123"}

            # Mock Bedrock converse (drives the tool-calling sequence)
            mock_bedrock_client = MagicMock()
            mock_bedrock_client.converse.side_effect = _make_bedrock_converse_side_effect(has_anomalies)
            mock_boto.return_value = mock_bedrock_client

            agent = CloudCostAnomalyAgent()
            result = agent.run()

            return result, {
                "mock_spikes": mock_spikes,
                "mock_ts": mock_ts,
                "mock_deploys": mock_deploys,
                "mock_urlopen": mock_urlopen,
                "mock_audit_es": mock_audit_es,
                "mock_bedrock": mock_bedrock_client,
            }

    # ------------------------------------------------------------------

    def test_full_chain_completes_successfully(self):
        """Happy path: spike detected → all 7 steps execute → status 200."""
        result, _ = self._run_agent_with_mocks()

        self.assertEqual(result["statusCode"], 200)
        self.assertIn("run_id", result["body"])
        self.assertIn("duration_seconds", result["body"])
        self.assertIn("total_tokens", result["body"])

    def test_find_spike_services_called_once(self):
        """find_spike_services must be called exactly once per run."""
        _, mocks = self._run_agent_with_mocks()
        mocks["mock_spikes"].assert_called_once_with(threshold_pct=25.0)

    def test_timeseries_called_for_spiked_service(self):
        """get_cost_timeseries must be called with the EC2 service."""
        _, mocks = self._run_agent_with_mocks()
        mocks["mock_ts"].assert_called_once_with(service="Amazon EC2", hours=48)

    def test_deploy_lookup_called_with_correct_service(self):
        """find_deploys_near_spike must be called for the checkout service."""
        _, mocks = self._run_agent_with_mocks()
        call_args = mocks["mock_deploys"].call_args
        self.assertEqual(call_args.kwargs.get("service") or call_args.args[0], "checkout")

    def test_slack_message_contains_required_fields(self):
        """Slack payload must mention checkout-team, v2.3.1, and a dollar figure."""
        _, mocks = self._run_agent_with_mocks()
        call_args = mocks["mock_urlopen"].call_args
        raw_data: bytes = call_args.args[0].data
        payload = json.loads(raw_data.decode("utf-8"))
        full_text = json.dumps(payload)

        self.assertIn("checkout-team", full_text)
        self.assertIn("v2.3.1", full_text)
        self.assertIn("$", full_text)

    def test_audit_written_on_success(self):
        """Audit doc must be indexed to Elasticsearch at end of run."""
        _, mocks = self._run_agent_with_mocks()
        self.assertTrue(mocks["mock_audit_es"].return_value.index.called)

    def test_no_anomalies_exits_silently(self):
        """When no spikes are found, Slack must NOT be called."""
        _, mocks = self._run_agent_with_mocks(spike_list=[])
        mocks["mock_urlopen"].assert_not_called()

    def test_lambda_handler_returns_200(self):
        """lambda_handler must return statusCode 200."""
        with (
            patch("agent.find_spike_services", return_value=[FAKE_SPIKE]),
            patch("agent.get_cost_timeseries", return_value=FAKE_TIMESERIES),
            patch("agent.find_deploys_near_spike", return_value=[FAKE_DEPLOY]),
            patch("tools.slack_notify._get_webhook_url", return_value="https://hooks.slack.com/test"),
            patch("tools.slack_notify.urllib.request.urlopen") as mock_urlopen,
            patch("tools.audit_writer._get_es_client") as mock_audit_es,
            patch("boto3.client") as mock_boto,
        ):
            mock_http_resp = MagicMock()
            mock_http_resp.__enter__ = lambda s: s
            mock_http_resp.__exit__ = MagicMock(return_value=False)
            mock_http_resp.status = 200
            mock_http_resp.read.return_value = b"ok"
            mock_urlopen.return_value = mock_http_resp
            mock_audit_es.return_value.index.return_value = {"_id": "audit-doc-123"}

            mock_bedrock_client = MagicMock()
            mock_bedrock_client.converse.side_effect = _make_bedrock_converse_side_effect(True)
            mock_boto.return_value = mock_bedrock_client

            result = lambda_handler({"source": "test"}, None)
            self.assertEqual(result["statusCode"], 200)
