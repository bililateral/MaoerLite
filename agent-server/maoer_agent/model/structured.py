"""Parse JSON content from the internal model response without business policy."""
import json


async def json_response(model, messages):
    reply = await model(messages)
    text = reply.get('content', '').strip()
    if text.startswith('```'):
        text = text.split('\n', 1)[1].rsplit('```', 1)[0]
    return json.loads(text)
