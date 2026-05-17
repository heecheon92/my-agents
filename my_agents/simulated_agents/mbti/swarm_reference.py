"""Reference MBTI T/F swarm implementation for comparison and learning.

This file intentionally sits beside `graph.py` instead of replacing it. It demonstrates
LangGraph Swarm's parent-graph handoff model:

- user messages enter the parent swarm graph;
- the swarm routes to the currently active agent;
- each agent can call a handoff tool that updates the parent graph's active agent.

It is a simulation for architecture study, not a real MBTI assessment.
"""

from __future__ import annotations

from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph
from langgraph_swarm import create_handoff_tool, create_swarm

from my_agents.settings import Settings, get_settings

TAGENT_NAME = "tagent"
FAGENT_NAME = "fagent"

TAGENT_PROMPT = """
You are tagent, a simulated MBTI Thinking-style specialist.

Purpose:
- Demonstrate a swarm handoff pattern for learning.
- Give concise, structured, logic-first responses.

Behavior:
- Focus on reasons, tradeoffs, evidence, and next actions.
- If the user's message is mainly emotional, self-deprecating, or asks for comfort,
  call the handoff tool to transfer to fagent.

Boundaries:
- This is a simulation, not psychological advice.
- Do not claim to be a real MBTI evaluator or therapist.
""".strip()

FAGENT_PROMPT = """
You are fagent, a simulated MBTI Feeling-style specialist.

Purpose:
- Demonstrate a swarm handoff pattern for learning.
- Give concise, empathy-first responses.

Behavior:
- Reflect emotion, validate the situation, and then offer a gentle next step.
- If the user's message mainly needs detached analysis, ranking, or tradeoff reasoning,
  call the handoff tool to transfer to tagent.

Boundaries:
- This is a simulation, not psychological advice.
- Do not claim to be a real MBTI evaluator or therapist.
""".strip()


def build_chat_model(settings: Settings | None = None) -> ChatOpenAI:
    """Build the OpenAI chat model lazily so importing this file is credential-free."""
    app_settings = settings or get_settings()
    return ChatOpenAI(
        model=app_settings.openai_model,
        api_key=app_settings.openai_api_key_value(),
        timeout=app_settings.openai_timeout_seconds,
        max_completion_tokens=app_settings.openai_max_output_tokens,
    )


def build_tagent(model: BaseChatModel | None = None) -> CompiledStateGraph:
    """Build the Thinking-style simulated agent with a handoff to fagent."""
    return create_agent(
        model or build_chat_model(),
        tools=[
            create_handoff_tool(
                agent_name=FAGENT_NAME,
                description="Transfer to fagent for empathy-first support.",
            )
        ],
        system_prompt=TAGENT_PROMPT,
        name=TAGENT_NAME,
    )


def build_fagent(model: BaseChatModel | None = None) -> CompiledStateGraph:
    """Build the Feeling-style simulated agent with a handoff to tagent."""
    return create_agent(
        model or build_chat_model(),
        tools=[
            create_handoff_tool(
                agent_name=TAGENT_NAME,
                description="Transfer to tagent for logic-first analysis.",
            )
        ],
        system_prompt=FAGENT_PROMPT,
        name=FAGENT_NAME,
    )


def build_mbti_swarm_reference(
    model: BaseChatModel | None = None,
    *,
    checkpointer: Any | None = None,
) -> CompiledStateGraph:
    """Build and compile the reference MBTI T/F swarm.

    The returned graph is the parent swarm. It owns active-agent routing. The user is
    represented by input messages, not by a separate graph node.
    """
    shared_model = model or build_chat_model()
    workflow = create_swarm(
        [build_tagent(shared_model), build_fagent(shared_model)],
        default_active_agent=TAGENT_NAME,
    )
    return workflow.compile(checkpointer=checkpointer or InMemorySaver())
