from typing import Annotated, Literal, TypedDict

from dotenv import load_dotenv
from langchain.tools import tool
from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph, add_messages

from my_agents.settings import get_settings

load_dotenv()
settings = get_settings()
llm = ChatOpenAI(model=settings.openai_model)


class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    active_agent: Literal["tagent", "fagent"]


@tool
def route(to_agent: Literal["tagent", "fagent"]):
    """
    Tool to route.

    Args: to_agent (either tagent or fagent)

    tagent: fact, reasoning based agent
    fagent: emotion based agent
    """
    return to_agent


@tool
def find_emotion(situation: str, emotion: str) -> dict:
    """If the user makes emotional, Separate the problem situation from the user’s emotions.
    Args:
        situation: problem situation string
        emotion: user's emotion string
    """
    return {"situation": situation, "emotion": emotion}


def tool_node(state: AgentState):
    last_message = state["messages"][-1]
    tool_calls = getattr(last_message, "tool_calls", [])

    if not tool_calls:
        return {}

    for tool_call in tool_calls:
        tool_name = tool_call["name"]

        if tool_name == "route":
            to_agent = route.invoke(tool_call["args"])
            return {
                "active_agent": to_agent,
                "messages": [
                    ToolMessage(
                        content=f"route tool selected {to_agent}",
                        name="route",
                        tool_call_id=tool_call["id"],
                    )
                ],
            }
        elif tool_name == "find_emotion":
            result = find_emotion.invoke(tool_call["args"])
            return {
                "messages": [
                    ToolMessage(
                        content=(
                            f"find_emotion result: situation: {result['situation']}, "
                            f"emotion: {result['emotion']}"
                        ),
                        name="find_emotion",
                        tool_call_id=tool_call["id"],
                    )
                ]
            }

    return {}


ttools = [{"type": "web_search"}, route]
tagent_with_tools = llm.bind_tools(ttools)


def tagent(state: AgentState):
    system_prompt = """
    You are the Agent in charge of T in MBTI.
    You have to answer rationally and logically.
    You can use search tool to search the web and find the basis.
    If the user makes emotional or self-deprecating statements,
    transfer to Fagent with the routing tool.
    """
    response = tagent_with_tools.invoke([SystemMessage(content=system_prompt), *state["messages"]])
    return {"messages": [response]}


ftool = [route, find_emotion]
fagent_with_tools = llm.bind_tools(ftool)


def fagent(state: AgentState):
    system_prompt = """
    You are the Agent in charge of F in MBTI.
    You have to empathize with the question and answer it emotionally.
    When user input is long, use find_emotion to separate emotions and situations.

    If the user asks a question or seems to need a solution,
    transfer to Tagent with the routing tool.
    Tagent can answer rationally and logically.
    """
    response = fagent_with_tools.invoke([SystemMessage(content=system_prompt), *state["messages"]])
    return {"messages": [response]}


def check_tool_request(state: AgentState):
    last_msg = state["messages"][-1]
    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        return "tool_node"
    return END


def route_active_agent(state: AgentState):
    return state["active_agent"]


builder = StateGraph(AgentState)
builder.add_node("tagent", tagent)
builder.add_node("fagent", fagent)
builder.add_node("tool_node", tool_node)

builder.add_edge(START, "tagent")
builder.add_conditional_edges("tagent", check_tool_request, ["tool_node", END])
builder.add_conditional_edges("fagent", check_tool_request, ["tool_node", END])
builder.add_conditional_edges("tool_node", route_active_agent, ["tagent", "fagent"])

app = builder.compile(checkpointer=InMemorySaver())
config: RunnableConfig = {"configurable": {"thread_id": "1"}}

if __name__ == "__main__":
    SIG_TERM = 0
    while True:
        try:
            user_input = input("🧑‍💻 User: ")
            if user_input.lower() in ["/quit", "/exit", "/q"]:
                print("Goodbye!")
                SIG_TERM = 1
                break

            turn = app.invoke(
                {"messages": [HumanMessage(content=user_input)]},
                config=config,
            )
            messages = turn["messages"]
            last_message = messages[-1]
            content = last_message.content

            if isinstance(content, str):
                print(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        print(block.get("text", ""))
            else:
                print(content)
        except KeyboardInterrupt:
            print("\nGoodbye!")
            break
        except Exception as exc:
            print(f"{type(exc).__name__}: {exc}")
            break
        if SIG_TERM:
            break
