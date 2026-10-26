"""Advance search candidates to body evidence through durable normal tools."""
from maoer_agent.contracts.intent import web_delivery
from maoer_agent.memory.history import last_turn
from maoer_agent.research.evidence import web_evidence
from maoer_agent.core.urls import public_url, known_public_url, public_links
from maoer_agent.workflows.episode_pages import observations


def lookup_step(route, messages, names):
    """Supply an omitted initial lookup when the model proposes to finish.

    This does not constrain later query refinement or additional sources.
    """
    lookup = route.get('web_lookup', {})
    if (not route.get('web') or 'web_search' not in names
            or route.get('page_lookup', {}).get('requested') or len(lookup.get('query', '').strip()) < 2):
        return None
    if any(call['name'] in ('web_search', 'read_web_page') for call, result in observations(last_turn(messages))):
        return None
    return {'name': 'web_search', 'arguments': {'query': lookup['query'], 'delivery': web_delivery(route)}}


def known_urls(messages):
    urls=set()
    for call,result in observations(messages):
        if result.get('status')!='ok':continue
        for item in result.get('items',[]):
            urls.update(url for url in (item.get('source_url'),item.get('original_url')) if url and public_url(url))
            urls.update(public_links(item.get('evidence','')))
    return tuple(sorted(urls))


def evidence_scope(messages, available, allowed, delivery='answer'):
    if delivery=='results':
        allowed=allowed-{'read_web_page'}
        available=[d for d in available if d['function']['name'] in allowed]
    if not web_evidence(last_turn(messages))['network_stopped']:
        return available, allowed
    allowed = allowed - {'web_search', 'read_web_page'}
    return [d for d in available if d['function']['name'] in allowed], allowed


def evidence_step(messages, names, delivery='answer'):
    if delivery=='results' or 'read_web_page' not in names:
        return None
    evidence = web_evidence(last_turn(messages))
    if evidence['body_urls'] or evidence['network_stopped']:
        return None
    candidates = evidence['candidate_urls']
    tried = {known_public_url(url, candidates) or url for url in evidence['attempted_urls']}
    # Only unattempted known candidates remain eligible. The shared tool budget
    # bounds work; failures with uncertain effects/quotas stop network scope.
    url = next((url for url in candidates if url not in tried and public_url(url)), None)
    return {'name': 'read_web_page', 'arguments': {'url': url, 'query': '', 'offset': 0}} if url else None
