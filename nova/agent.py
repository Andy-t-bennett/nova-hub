"""Agent invocation — API client, single-shot calls, streaming, response parsing."""

import json
import os
import time
from collections.abc import Generator
from typing import Any

import anthropic
from dotenv import load_dotenv
from rich.console import Console

from nova.config import ModelConfig
from nova.models import (
    AgentOutput,
    AgentRole,
    AgentStatus,
    CoderOutput,
    DistillerOutput,
    PlannerOutput,
    QAOutput,
)

load_dotenv()
console = Console()

MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2  # seconds


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

def get_client() -> anthropic.Anthropic:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key or api_key == "your-key-here":
        raise RuntimeError(
            "ANTHROPIC_API_KEY not set. Add your key to the .env file in the project root."
        )
    return anthropic.Anthropic(api_key=api_key)


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

ROLE_OUTPUT_MAP: dict[AgentRole, type[AgentOutput]] = {
    AgentRole.CODER: CoderOutput,
    AgentRole.QA: QAOutput,
    AgentRole.PLANNER: PlannerOutput,
    AgentRole.DISTILLER: DistillerOutput,
}


def _extract_json(text: str) -> dict[str, Any]:
    """Extract a JSON object from model response text.

    Handles both raw JSON and JSON inside a ```json code fence.
    """
    stripped = text.strip()

    if stripped.startswith("{"):
        return json.loads(stripped)

    start = stripped.find("```json")
    if start != -1:
        start = stripped.index("\n", start) + 1
        end = stripped.find("```", start)
        if end != -1:
            return json.loads(stripped[start:end].strip())

    start = stripped.find("```")
    if start != -1:
        start = stripped.index("\n", start) + 1
        end = stripped.find("```", start)
        if end != -1:
            candidate = stripped[start:end].strip()
            if candidate.startswith("{"):
                return json.loads(candidate)

    start = stripped.find("{")
    end = stripped.rfind("}") + 1
    if start != -1 and end > start:
        return json.loads(stripped[start:end])

    raise ValueError("No JSON object found in response")


def parse_agent_response(role: AgentRole, raw_response: str) -> AgentOutput:
    """Parse raw model response into the correct typed output model."""
    output_class = ROLE_OUTPUT_MAP[role]
    data = _extract_json(raw_response)

    if "role" not in data:
        data["role"] = role.value

    return output_class.model_validate(data)


# ---------------------------------------------------------------------------
# Single-shot call (pipeline agents: Coder, QA, Distiller)
# ---------------------------------------------------------------------------

def call_agent_single_shot(
    role: AgentRole,
    system_prompt: str,
    model_config: ModelConfig,
    user_message: str = "Execute your task.",
) -> tuple[AgentOutput, dict[str, Any]]:
    """Make a single API call and return parsed output + usage metadata.

    Retries on API errors with exponential backoff.
    On malformed response, retries once asking for valid JSON.
    Returns a blocked AgentOutput if all retries fail.
    """
    client = get_client()
    raw_response = ""
    usage_meta: dict[str, Any] = {}

    for attempt in range(MAX_RETRIES):
        try:
            message = client.messages.create(
                model=model_config.model,
                max_tokens=model_config.max_tokens,
                temperature=model_config.temperature,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )

            raw_response = message.content[0].text
            usage_meta = {
                "input_tokens": message.usage.input_tokens,
                "output_tokens": message.usage.output_tokens,
                "model": model_config.model,
            }

            return parse_agent_response(role, raw_response), usage_meta

        except (json.JSONDecodeError, ValueError):
            if attempt == 0:
                user_message = (
                    "Your previous response was not valid JSON. "
                    "Please respond with ONLY a JSON object matching the required format. "
                    "No markdown, no explanation — just the JSON."
                )
                continue
            break

        except anthropic.APIError as e:
            error_str = str(e)
            is_billing = "credit balance" in error_str.lower() or "billing" in error_str.lower()

            if is_billing:
                console.print(
                    "\n[bold red]Billing error:[/bold red] Your Anthropic API credits are exhausted.\n"
                    "  Top up at [link=https://console.anthropic.com/settings/billing]console.anthropic.com/settings/billing[/link]\n"
                    "  Then re-run your command — all progress is saved."
                )
                break

            if attempt < MAX_RETRIES - 1:
                wait = RETRY_BACKOFF_BASE ** (attempt + 1)
                console.print(f"[yellow]API error (attempt {attempt + 1}): {e}. Retrying in {wait}s...[/yellow]")
                time.sleep(wait)
                continue
            console.print(f"[red]API error after {MAX_RETRIES} attempts: {e}[/red]")
            break

    output_class = ROLE_OUTPUT_MAP[role]
    return output_class(
        role=role,
        status=AgentStatus.BLOCKED,
        summary=f"Failed after {MAX_RETRIES} attempts. Last response: {raw_response[:200]}",
        next_action="escalate",
    ), usage_meta


# ---------------------------------------------------------------------------
# Streaming call (interactive sessions: Planner brainstorm/spec/plan)
# ---------------------------------------------------------------------------

def call_agent_stream(
    role: AgentRole,
    system_prompt: str,
    model_config: ModelConfig,
    messages: list[dict[str, str]],
) -> Generator[str, None, tuple[str, dict[str, Any]]]:
    """Stream a response token-by-token. Yields text chunks as they arrive.

    After the stream completes, returns (full_response, usage_meta) via StopIteration.
    The caller uses this in a for loop to display streaming text,
    then catches the return value.

    Usage:
        gen = call_agent_stream(role, prompt, config, messages)
        full = ""
        try:
            while True:
                chunk = next(gen)
                print(chunk, end="", flush=True)
                full += chunk
        except StopIteration as e:
            full_response, usage_meta = e.value
    """
    client = get_client()

    for attempt in range(MAX_RETRIES):
        try:
            full_response = ""
            usage_meta: dict[str, Any] = {}

            with client.messages.stream(
                model=model_config.model,
                max_tokens=model_config.max_tokens,
                temperature=model_config.temperature,
                system=system_prompt,
                messages=messages,
            ) as stream:
                for text in stream.text_stream:
                    full_response += text
                    yield text

                final = stream.get_final_message()
                usage_meta = {
                    "input_tokens": final.usage.input_tokens,
                    "output_tokens": final.usage.output_tokens,
                    "model": model_config.model,
                }

            return full_response, usage_meta

        except anthropic.APIError as e:
            error_str = str(e)
            is_billing = "credit balance" in error_str.lower() or "billing" in error_str.lower()

            if is_billing:
                console.print(
                    "\n[bold red]Billing error:[/bold red] Your Anthropic API credits are exhausted.\n"
                    "  Top up at [link=https://console.anthropic.com/settings/billing]console.anthropic.com/settings/billing[/link]\n"
                    "  Then re-run your command — all progress is saved."
                )
                raise

            if attempt < MAX_RETRIES - 1:
                wait = RETRY_BACKOFF_BASE ** (attempt + 1)
                console.print(f"\n[yellow]API error (attempt {attempt + 1}): {e}. Retrying in {wait}s...[/yellow]")
                time.sleep(wait)
                continue
            raise
