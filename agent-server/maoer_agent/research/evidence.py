"""Web evidence states derived from actual paired calls and receipts."""
import json


def web_evidence(messages):
    calls, candidates, reads, bodies = {}, {}, {}, []
    blocked = False
    for message in messages:
        for call in message.get('tool_calls') or []:
            calls[call['id']] = call['function']
        if message['role'] != 'tool' or message.get('tool_call_id') not in calls:
            continue
        call = calls[message['tool_call_id']]
        if call['name'] not in ('web_search', 'read_web_page'):
            continue
        result = json.loads(message['content'])
        if result.get('status') in ('error', 'unknown'):
            # Only local pre-execution rejection or a completed empty extract
            # leaves another known source eligible. Never replay uncertainty,
            # timeouts, quota, auth, rate limits or provider failures.
            local_relevance=(call['name']=='web_search' and result.get('code')=='invalid_model_response' and result.get('stage')=='web_relevance')
            blocked |= not local_relevance and result.get('code') not in ('web_url_not_allowed', 'web_extract_failed', 'web_body_empty')
        if call['name'] == 'web_search' and result.get('status') == 'ok':
            for item in result.get('items', []):
                if item.get('source_url'):
                    candidates.setdefault(item['source_url'], item.get('title', ''))
        elif call['name'] == 'read_web_page':
            url = json.loads(call['arguments']).get('url', '')
            reads[url] = result.get('status')
            for item in result.get('items', []) if result.get('status') == 'ok' else []:
                if item.get('document_id') and item.get('evidence'):
                    bodies.append(item['source_url'])
    return {'candidate_urls': list(candidates), 'attempted_urls': list(reads),
            'body_urls': list(dict.fromkeys(bodies)), 'network_stopped': blocked}


def answer_sources(sources, messages, delivery='answer', episode_id=''):
    """Keep search candidates separate from citable factual-answer evidence.

    Explicit search-result delivery retains snippet citations. This projection
    never changes stored sources, receipts or source-drawer availability.
    """
    if delivery=='results':
        return sources
    if episode_id:
        return [s for s in sources if s.get('kind') == 'episode' and s.get('id') == episode_id
                and s.get('document_id') and s.get('evidence')]
    return [s for s in sources if s.get('kind') != 'web' or s.get('document_id')]


def candidate_view(messages, delivery='answer'):
    start = max((i for i, m in enumerate(messages) if m['role'] == 'user'), default=0)
    view = []
    for index, message in enumerate(messages):
        if message['role'] == 'tool':
            result = json.loads(message['content'])
            result.pop('candidate_items',None)  # Raw provider evidence stays in storage, not the selected result view.
            for item in result.get('items', []):
                if index>=start and delivery!='results' and item.get('kind') == 'web' and not item.get('document_id'):
                    item.pop('citation', None)
                    item.pop('evidence', None)
            message = {**message, 'content': json.dumps(result, ensure_ascii=False)}
        view.append(message)
    return view
