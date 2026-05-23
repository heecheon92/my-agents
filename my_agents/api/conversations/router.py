"""Router assembly for conversation API endpoint modules."""

from fastapi import APIRouter, status

from my_agents.api.conversations.endpoints.conversations import (
    create_conversation,
    get_conversation,
    list_conversations,
)
from my_agents.api.conversations.endpoints.events import router as event_routes
from my_agents.api.conversations.endpoints.messages import router as message_routes
from my_agents.api.conversations.endpoints.replay import router as replay_routes
from my_agents.api.conversations.endpoints.runs import router as run_routes
from my_agents.api.conversations.endpoints.stream import router as stream_routes
from my_agents.conversations.schemas import ConversationResponse

conversations_router = APIRouter(prefix="/conversations", tags=["conversations"])
# Root collection routes use add_api_route because FastAPI cannot include a
# sub-router route whose prefix and path are both empty.
conversations_router.add_api_route(
    "",
    create_conversation,
    methods=["POST"],
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
)
conversations_router.add_api_route(
    "",
    list_conversations,
    methods=["GET"],
    response_model=list[ConversationResponse],
)
conversations_router.add_api_route(
    "/{conversation_id}",
    get_conversation,
    methods=["GET"],
    response_model=ConversationResponse,
)
conversations_router.include_router(message_routes)
conversations_router.include_router(stream_routes)
conversations_router.include_router(replay_routes)
conversations_router.include_router(run_routes)
conversations_router.include_router(event_routes)
