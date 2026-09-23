"""The local text/function-call protocol; no execution of model tools here."""
import json
from urllib.parse import urlsplit
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

from config import Settings


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class FunctionCall(StrictModel):
    name: str = Field(pattern=r'^[A-Za-z_][A-Za-z0-9_]{0,63}$')
    arguments: str = Field(max_length=16000)


class ToolCall(StrictModel):
    id: str = Field(min_length=1, max_length=256)
    type: Literal['function']
    function: FunctionCall


class Message(StrictModel):
    role: Literal['system', 'user', 'assistant', 'tool']
    content: str | None = Field(default=None, max_length=16000)
    tool_calls: list[ToolCall] | None = Field(default=None, min_length=1, max_length=8)
    tool_call_id: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode='after')
    def check_role(self):
        if self.role != 'assistant' and self.content is None:
            raise ValueError('Message content required')
        if self.tool_calls is not None and self.role != 'assistant':
            raise ValueError('Only assistant messages can contain tool calls')
        if (self.role == 'tool') != (self.tool_call_id is not None):
            raise ValueError('Invalid tool result')
        if self.role == 'assistant' and self.content is None and not self.tool_calls:
            raise ValueError('Empty assistant message')
        return self


class FunctionDefinition(StrictModel):
    name: str = Field(pattern=r'^[A-Za-z_][A-Za-z0-9_]{0,63}$')
    description: str = Field(default='', max_length=2000)
    parameters: dict[str, Any]

    @model_validator(mode='after')
    def check_schema(self):
        if self.parameters.get('type') != 'object':
            raise ValueError('Object parameters required')
        return self


class ToolDefinition(StrictModel):
    type: Literal['function']
    function: FunctionDefinition


class ChatRequest(StrictModel):
    model: str | None = Field(default=None, min_length=1, max_length=128)
    messages: list[Message] = Field(min_length=1, max_length=40)
    stream: bool = False
    max_tokens: int = Field(default=1024, ge=1, le=2048)
    tools: list[ToolDefinition] | None = Field(default=None, min_length=1, max_length=16)
    tool_choice: Literal['auto'] | None = None

    @model_validator(mode='after')
    def check_conversation(self):
        if not any(m.role == 'user' for m in self.messages):
            raise ValueError('User message required')
        if self.messages[-1].role not in ('user', 'tool'):
            raise ValueError('Conversation must end with user or tool results')
        pending, seen = set(), set()
        for message in self.messages:
            if message.role == 'tool':
                if message.tool_call_id not in pending:
                    raise ValueError('Unknown or duplicate tool result')
                pending.remove(message.tool_call_id)
            elif pending:
                raise ValueError('Missing tool results')
            for call in message.tool_calls or []:
                if call.id in seen:
                    raise ValueError('Duplicate tool call ID')
                if not isinstance(json.loads(call.function.arguments), dict):
                    raise ValueError('Tool arguments must be an object')
                seen.add(call.id)
                pending.add(call.id)
        if pending:
            raise ValueError('Missing tool results')
        if self.tool_choice and not self.tools:
            raise ValueError('Tool choice requires tools')
        names = [t.function.name for t in self.tools or []]
        if len(names) != len(set(names)):
            raise ValueError('Duplicate tool definitions')
        return self

    def upstream_body(self, settings: Settings):
        if self.model is not None and self.model != settings.model:
            raise ValueError('Only the configured model is enabled')
        body = {**self.model_dump(exclude_none=True), 'model': settings.model}
        # Official DeepSeek defaults to thinking. Both gateway and Agent must
        # explicitly select non-thinking, including after model alias changes.
        if urlsplit(settings.base_url).hostname == 'api.deepseek.com' or settings.model in (
            'deepseek-flash', 'deepseek-v4.1-flash',
        ):
            body['thinking'] = {'type': 'disabled'}
        return body


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
