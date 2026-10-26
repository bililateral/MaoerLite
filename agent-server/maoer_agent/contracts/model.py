"""Internal model input requirements, independent of the provider HTTP protocol."""


class JsonMessages(list):
    """A JSON object is required; schema and evidence validation remain with callers."""
    output_format = 'json_object'
