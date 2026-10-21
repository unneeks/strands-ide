"""Strands + Amazon Bedrock AgentCore code-editor agent.

This is "the server" the AgentCore dev brings. Run it locally and the IDE
(../ide) invokes this runtime over HTTP.

Run:
    python -m agent.agent            # or: python agent/agent.py

Local HTTP endpoints (provided by BedrockAgentCoreApp):
    POST /invocations   { "prompt": "..." }   -> runs the agent loop
    GET  /ping                                 -> 200 (health check)

The LLM is the AgentCore-provided Amazon Bedrock model. For local testing,
give the SDK permission to call Bedrock using any of:
    - AWS_BEARER_TOKEN_BEDROCK   (a Bedrock API key — quickest for local dev)
    - AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY (+ AWS_SESSION_TOKEN) env vars
    - `aws configure` credentials / an IAM role on an AWS compute service
Also enable the model you use in the Amazon Bedrock console.

Everything the agent does (file edits, shell commands, code execution) runs
locally on this machine against the shared workspace — that is intentional for
local testing. Be mindful of that before pointing it at a real project.
"""
from __future__ import annotations

import logging
import os

# Run strands_tools autonomously. In a headless AgentCore HTTP runtime there is
# no terminal to answer their per-operation confirmation prompts, and an
# unanswered prompt parks the agent in an "interrupt" state — which then
# rejects the next string prompt with:
#   "must resume from interrupt with list of interruptResponse's"
# These flags skip the prompts so shell / python_repl / editor execute
# directly. Use os.environ.setdefault so an explicit value is still respected.
os.environ.setdefault("STRANDS_NON_INTERACTIVE", "true")
os.environ.setdefault("BYPASS_TOOL_CONSENT", "true")

from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent
from strands_tools import editor, file_read, file_write, python_repl, shell

from agent.system_prompt import SYSTEM_PROMPT
from agent.tools import grep_files, list_files

log = logging.getLogger("strands-ide.agent")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# ---------------------------------------------------------------------------
# Workspace: the agent and the IDE share this directory. The agent's file and
# shell tools resolve paths against the process working directory, so we chdir
# into the workspace at startup. (A fixed chdir is deterministic state, so it is
# safe under AgentCore's snapshot-based startup too.)
# ---------------------------------------------------------------------------
WORKSPACE_DIR = os.environ.get(
    "WORKSPACE_DIR",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "workspace")),
)
os.makedirs(WORKSPACE_DIR, exist_ok=True)
os.chdir(WORKSPACE_DIR)
log.info("agent workspace: %s", WORKSPACE_DIR)

# Optional: pin a specific Bedrock model id. Defaults to Strands' built-in
# Bedrock default (a current Claude model) when unset.
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID")

# ---------------------------------------------------------------------------
# Build the agent. BedrockModel is the default Strands provider, so the agent
# uses the AgentCore-provided LLM out of the box.
# ---------------------------------------------------------------------------
agent_kwargs: dict[str, Any] = {
    "system_prompt": SYSTEM_PROMPT,
    "tools": [list_files, grep_files, file_read, file_write, editor, shell, python_repl],
    "callback_handler": None,  # quiet stdout; the IDE renders tool calls itself
}
if MODEL_ID:
    from strands.models import BedrockModel

    agent_kwargs["model"] = BedrockModel(model_id=MODEL_ID)

agent = Agent(**agent_kwargs)

# ---------------------------------------------------------------------------
# AgentCore runtime
# ---------------------------------------------------------------------------
app = BedrockAgentCoreApp()


def _extract_tool_calls(messages: list[dict]) -> list[dict]:
    """Pull tool-use blocks out of the Strands conversation history."""
    calls: list[dict] = []
    for msg in messages or []:
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                calls.append(
                    {
                        "name": block.get("name"),
                        "input": block.get("input", {}) or {},
                    }
                )
    return calls


def _files_changed(tool_calls: list[dict]) -> list[str]:
    """Infer which file paths the agent modified (create/edit operations)."""
    changed: set[str] = set()
    for call in tool_calls:
        name = call.get("name")
        inp = call.get("input", {}) or {}
        if name in ("file_write", "editor"):
            path = inp.get("path")
            if isinstance(path, str):
                changed.add(path)
    return sorted(changed)


def _final_text(result: Any) -> str:
    """Best-effort extraction of the agent's final text response."""
    try:
        blocks = result.message["content"]
        parts = [
            b["text"]
            for b in blocks
            if isinstance(b, dict) and b.get("type") == "text" and b.get("text")
        ]
        if parts:
            return "\n".join(parts)
    except (KeyError, IndexError, TypeError):
        pass
    return str(result)


def _drive_to_completion(prompt, max_approvals: int = 25) -> Any:
    """Run the agent, auto-approving any consent interrupts until it finishes.

    ``strands_tools`` (shell, python_repl, editor) can pause for user
    confirmation via Strands interrupts. In a headless AgentCore HTTP runtime
    nobody is there to answer, so we approve ("y") and continue. Each
    /invocations is one autonomous turn and must end with the agent NOT parked
    in an interrupt state — otherwise the next string prompt is rejected with
    "must resume from interrupt with list of interruptResponse's".
    """
    result = agent(prompt)
    approvals = 0
    while (
        getattr(result, "stop_reason", None) == "interrupt"
        and getattr(result, "interrupts", None)
        and approvals < max_approvals
    ):
        approvals += 1
        responses = [
            {"interruptResponse": {"interruptId": i.id, "response": "y"}}
            for i in result.interrupts
        ]
        log.info(
            "auto-approving %d interrupt(s): %s",
            len(responses),
            [getattr(i, "name", "?") for i in result.interrupts],
        )
        result = agent(responses)
    if getattr(result, "stop_reason", None) == "interrupt":
        log.warning("hit max_approvals=%d while still interrupted", max_approvals)
    return result


@app.entrypoint
def edit_agent(payload, context=None):
    """Handle POST /invocations.

    Payload: { "prompt": "<user instruction>" }
    Returns: { "text": "...", "tool_calls": [...], "files_changed": [...] }
    """
    prompt = (payload or {}).get("prompt", "")
    if not isinstance(prompt, str) or not prompt.strip():
        return {"error": "'prompt' must be a non-empty string"}

    log.info("invocation: %s", prompt[:200])
    try:
        result = _drive_to_completion(prompt)
    except TypeError as exc:
        # A previous turn may have left the agent parked in an interrupted
        # state (e.g. that turn crashed before reaching the approval loop).
        # Clear it once and treat this as a fresh turn.
        if "must resume from interrupt" in str(exc):
            log.warning("clearing stale interrupt state; running as a new turn")
            agent._interrupt_state.deactivate()  # access private state by design
            result = _drive_to_completion(prompt)
        else:
            raise

    tool_calls = _extract_tool_calls(result.messages)
    return {
        "text": _final_text(result),
        "tool_calls": tool_calls,
        "files_changed": _files_changed(tool_calls),
    }


if __name__ == "__main__":
    _port = int(os.environ.get("AGENT_PORT", "8080"))
    log.info("agent runtime -> http://localhost:%d", _port)
    app.run(port=_port)
