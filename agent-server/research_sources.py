"""Stable, per-answer citations. Only actual tool evidence becomes a source."""
import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime, timezone, timedelta


def current_citation_view(messages):
    """Old evidence remains usable; its answer-local citation IDs do not.

    Change only the model input copy, not stored replies, receipts, tool-call
    identities or provider reasoning. Current-turn annotations stay intact.
    """
    view = deepcopy(messages)
    starts = [i for i, message in enumerate(view) if message['role'] == 'user']
    if len(starts) < 2:
        return view
    numbers = set()
    for message in view[:starts[-1]]:
        if message['role'] == 'user':
            numbers = set()
        elif message['role'] == 'tool':
            result = json.loads(message['content'])
            for item in result.get('items') or []:
                number = item.pop('citation', None)
                if type(number) is int:
                    numbers.add(str(number))
            message['content'] = json.dumps(result, ensure_ascii=False)
        elif message['role'] == 'assistant' and message.get('content'):
            message['content'] = re.sub(r'(?<!\!)\[(\d{1,3})\](?!\()',
                lambda match: '（此前资料）' if match[1] in numbers else match[0], message['content'])
    return view


def annotate(run, result):
    sources = list(run.get('sources', []))
    items = []
    for raw in result.get('items', []):
        item = dict(raw)
        if isinstance(item.get('fetched_at'), (int, float)) and item['fetched_at'] > 0:
            item['fetched_display'] = datetime.fromtimestamp(item['fetched_at'], timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M 北京时间')
        if item.get('evidence') and item.get('source_url'):
            identity = '|'.join(str(item.get(k, '')) for k in ('kind', 'id', 'source_url'))
            key = hashlib.sha256(identity.encode()).hexdigest()[:24]
            old = next((s for s in sources if s['key'] == key), None)
            if old is None and len(sources) < 48:
                old = {**item, 'key': key, 'number': len(sources) + 1}
                sources.append(old)
            elif old:
                previous = old.get('evidence', '')
                incoming = item.get('evidence', '')
                # A fetched body replaces its search teaser; successive RSS/body
                # passages share one citation and remain available in the snapshot.
                upgrade = bool(item.get('document_id')) and not old.get('document_id')
                if upgrade or incoming not in previous:
                    excerpt = incoming if upgrade else (previous + '\n…\n' + incoming)[:12000]
                    old = {**old, **item, 'evidence': excerpt, 'key': key, 'number': old['number']}
                    sources[old['number'] - 1] = old
            if old:
                item['citation'] = old['number']
        items.append(item)
    return {**result, 'items': items} if 'items' in result else result, sources


def citation_context(sources):
    if not sources:
        return '本轮没有取得可引用资料，不编造[数字]引用；旧回复的编号不可沿用。'
    rows = [f"[{s['number']}] {s.get('title', '')[:150]}：{s.get('evidence', '')[:80]}" for s in sources]
    return ('以下是本轮已取得的资料编号。仅在确实支持对应结论的句末使用[数字]，不要自行编号、不要附加重复来源列表。'
            '推测与一般知识不冒充资料结论。网页搜索摘要不等于已读正文；新鲜度、片段与全文范围如实说明。\n' + '\n'.join(rows))


def valid_citations(text, sources):
    allowed = {str(s['number']) for s in sources}
    return all(n in allowed for n in re.findall(r'(?<!\!)\[(\d{1,3})\](?!\()', text or ''))
