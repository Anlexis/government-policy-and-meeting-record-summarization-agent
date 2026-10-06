"""AgentCore Platform v1.0"""

# Standalone HTTP entry point for the agent.
# Entry points are adapters only — no business logic here. For platform-level
# routing, AgentGateway calls agent.invoke() directly and this module is unused.
#
# The adapter's three jobs:
#   1. decide the caller's trust level (below) — the framework's input gate keys
#      off it, and the summarisation entry requires a verified external caller;
#   2. hand the caller's options through as input_context, unvalidated — the
#      domain node owns the caller contract, so every rejection names the same
#      field with the same message however the agent was invoked;
#   3. apply the runtime parameters declared in config/config.yaml.

import asyncio
import os
import pathlib
import secrets as secrets_lib
from typing import Any
from uuid import uuid4

import yaml
from fastapi import FastAPI, Request
from pydantic import BaseModel

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from framework.secrets.context import bound_secrets
from shared.secrets import factory as secrets_factory
from src.graph.graph import GovernmentPolicyMeetingRecordSummarizationAgent

_CONFIG_PATH = pathlib.Path(__file__).resolve().parents[2] / "config" / "config.yaml"
_DEFAULT_TIMEOUT_S: float = 30.0


def _load_runtime_config() -> dict[str, Any]:
    """Read config/config.yaml — the runtime parameters, separate from the manifest.

    A missing or unreadable file leaves the graph on its framework defaults
    rather than failing start-up: these are tuning values, not a contract.
    """
    try:
        loaded = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


_RUNTIME_CONFIG = _load_runtime_config()

app = FastAPI(title="GOV-C2-004 Government Policy & Meeting Record Summarization Agent")

# max_retry from config/config.yaml reaches the backbone's retry routing through
# the constructor; the framework validates it at compile time.
agent = GovernmentPolicyMeetingRecordSummarizationAgent(config=_RUNTIME_CONFIG)
agent.compile()
agent.provision_secrets(
    secrets_factory(
        namespace="gov",
        agent_name="GovernmentPolicyMeetingRecordSummarizationAgent",
    )
)


def _request_timeout_s() -> float:
    """Per-request budget, from config/config.yaml `timeout_s`."""
    declared = _RUNTIME_CONFIG.get("timeout_s", _DEFAULT_TIMEOUT_S)
    try:
        budget = float(declared)
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT_S
    return budget if budget > 0 else _DEFAULT_TIMEOUT_S


def _caller_trust_level(request: Request) -> TrustLevel:
    """Resolve the caller's trust level for this request.

    A platform deployment puts its own authenticated value on the request state,
    and that always wins. Standalone, the level comes from a bearer token matched
    against the deployment's configured tokens. An absent, malformed or unknown
    token is ANONYMOUS — the least privilege — so the entry contract fails closed
    when a deployment forgets to configure a token, rather than admitting every
    caller as verified.
    """
    platform_level = getattr(request.state, "trust_level", None)
    if isinstance(platform_level, TrustLevel):
        return platform_level

    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        return TrustLevel.ANONYMOUS

    internal_token = os.environ.get("STG_INTERNAL_RUNNER_TOKEN", "")
    if internal_token and secrets_lib.compare_digest(token, internal_token):
        return TrustLevel.INTERNAL

    external_token = os.environ.get("INVOKE_AUTH_TOKEN", "")
    if external_token and secrets_lib.compare_digest(token, external_token):
        return TrustLevel.VERIFIED_EXTERNAL

    return TrustLevel.ANONYMOUS


class InvokeRequest(BaseModel):
    """Caller payload.

    The option fields are deliberately untyped: they are forwarded verbatim to
    the domain node, which is the single place that decides what is acceptable.
    Typing them here would reject some malformed values with a schema error and
    others with a domain error, for the same class of mistake.
    """

    input: str
    session_id: str = ""
    audience_mode: Any = None
    channel: Any = None
    max_summary_chars: Any = None


def _input_context(req: InvokeRequest) -> dict[str, Any]:
    """Build the options mapping, omitting the options the caller did not send."""
    supplied = {
        "audience_mode": req.audience_mode,
        "channel": req.channel,
        "max_summary_chars": req.max_summary_chars,
    }
    return {key: value for key, value in supplied.items() if value is not None}


@app.post("/invoke")
async def invoke(req: InvokeRequest, request: Request) -> Any:
    ctx = InvocationContext(
        session_id=req.session_id or str(uuid4()),
        caller_trust_level=_caller_trust_level(request),
        caller_id=getattr(request.state, "caller_id", ""),
    )
    options = _input_context(req)

    def _run() -> Any:
        with bound_secrets(agent._secrets_provider):
            return agent.invoke(req.input, ctx=ctx, input_context=options)

    try:
        return await asyncio.wait_for(asyncio.to_thread(_run), timeout=_request_timeout_s())
    except asyncio.TimeoutError:
        # The declared budget is a response deadline: the caller is told the run
        # exceeded it rather than being left waiting.
        return {
            "output": None,
            "status": "timeout",
            "error_log": [f"request exceeded the configured budget of {_request_timeout_s()}s"],
        }


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "agent": "GovernmentPolicyMeetingRecordSummarizationAgent",
        "template": "GOV-C2-004",
    }
