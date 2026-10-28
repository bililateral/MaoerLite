"""Internal model input requirements, independent of the provider HTTP protocol."""
from contextvars import ContextVar


model_call_context = ContextVar('model_call_context', default=None)


class JsonMessages(list):
    """A JSON object is required; schema and evidence validation remain with callers."""
    output_format = 'json_object'
