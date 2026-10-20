"""Public gateway request validation and response projection."""
from pydantic import Field
from maoer_agent.contracts.chat import ConversationRequest, Message


class ChatRequest(ConversationRequest):
    messages: list[Message] = Field(min_length=1, max_length=40)
    max_tokens: int = Field(default=1024, ge=1, le=2048)


def public_response(data: dict) -> dict:
    """Expose only text, tool calls and usage. Drop reasoning and other modalities."""
    if not isinstance(data, dict) or 'error' in data:
        raise ValueError('Invalid upstream response')
    result = {k: v for k, v in data.items() if k in ('id', 'object', 'created', 'model', 'usage')}
    if 'choices' in data:
        choices = []
        for choice in data['choices']:
            clean = {k: v for k, v in choice.items() if k in ('index', 'finish_reason')}
            for key in ('message', 'delta'):
                if key in choice:
                    clean[key] = {k: v for k, v in choice[key].items() if k in ('role', 'content', 'tool_calls')}
                    if key == 'message' and 'tool_calls' in clean[key]:
                        # Stream assembly fields do not belong in complete
                        # messages replayed to the model.
                        clean[key]['tool_calls'] = [
                            {k: v for k, v in call.items() if k in ('id', 'type', 'function')}
                            for call in clean[key]['tool_calls']
                        ]
            choices.append(clean)
        result['choices'] = choices
    return result
