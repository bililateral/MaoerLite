"""Server-side read-only tools with a hard, persistent free-credit budget."""
from maoer_agent.core.errors import error_message
from datetime import datetime, timezone
from ipaddress import ip_address
from urllib.parse import urlsplit
from maoer_agent.research.store import ResearchStore, digest
from maoer_agent.research.rss import RssResearch

from maoer_agent.research.web import WebResearch


def public_url(value):
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password:
            return False
        host = parsed.hostname.lower()
        if '.' not in host or host.endswith(('.local', '.localhost', '.internal')): return False
        try: return ip_address(host).is_global
        except ValueError: return True
    except ValueError:
        return False


class ResearchTools:
    def __init__(self, settings, client, path):
        self.store = ResearchStore(path)
        self.rss = RssResearch(self.store, client, path.parent / 'embedding-model')
        self.web = WebResearch(settings, client, self.store)

    async def perform(self, call, run):
        name, args = call['name'], call['arguments']
        if name == 'list_recent_updates':
            from maoer_agent.research.updates import recent_updates
            return await recent_updates(self.rss, **args)
        if name == 'search_rss': return await self.rss.search(**args)
        if name == 'read_episode_description': return await self.rss.read(**args)
        request_id = run['id'] + ':' + call['id']
        if name == 'web_search':
            result = await self.web.tavily('search', {'query': args['query'], 'search_depth': 'basic', 'max_results': 5,
                                                'include_answer': False, 'include_raw_content': False,
                                                'auto_parameters': False}, request_id)
            if result.get('status') == 'error': return result
            items = []
            for row in result.get('results', [])[:5]:
                url = row.get('url', '')
                if not public_url(url): continue
                items.append({'kind': 'web', 'id': digest(url), 'title': row.get('title', '')[:300],
                              'source_url': url, 'original_url': url, 'published': row.get('published_date') or '',
                              'evidence': row.get('content', '')[:1400], 'evidence_field': '网页搜索摘要（未读正文）',
                              'evidence_truncated': True, 'freshness': 'refreshed',
                              'fetched_at': int(datetime.now(timezone.utc).timestamp())})
            return {'status': 'ok', 'items': items, 'note': '搜索摘要不是网页正文；核实关键事实时继续读取原网页。'}
        url = args['url']
        # Only follow a result already returned to this run, or an exact URL the user supplied.
        known = {s.get('source_url') for s in run.get('sources', [])}
        question = run.get('input', {}).get('message', '')
        if not public_url(url) or (url not in known and url not in question):
            return {'status': 'error', 'code': 'web_url_not_allowed', 'note': error_message('web_url_not_allowed')}
        saved = next((s for s in run.get('sources', []) if s.get('source_url') == url and s.get('document_id')), None)
        raw = self.store.read(saved['document_id']) if saved else ''
        if not raw:
            result = await self.web.tavily('extract', {'urls': [url], 'extract_depth': 'basic', 'format': 'text',
                                                 'include_images': False}, request_id)
            if result.get('status') == 'error': return result
            rows = result.get('results', [])
            if not rows: return {'status': 'error', 'code': 'web_extract_failed', 'note': error_message('web_extract_failed')}
            raw = rows[0].get('raw_content', '')
        if not raw: return {'status': 'error', 'code': 'web_body_empty', 'note': error_message('web_body_empty')}
        title = next((s['title'] for s in run.get('sources', []) if s.get('source_url') == url), urlsplit(url).hostname)
        text = raw[:200000]
        document_id = self.store.document(text)
        # Retrieve passages around the current question, instead of quoting only the header.
        query = args.get('query', '').strip()
        offset = args.get('offset', 0)
        if query and offset == 0:
            position = text.lower().find(query.lower())
            if position >= 0: offset = max(0, position - 300)
        excerpt = text[offset:offset + 6000]
        return {'status': 'ok', 'items': [{'kind': 'web', 'id': digest(url), 'title': title,
                    'source_url': url, 'original_url': url, 'evidence': excerpt,
                    'evidence_field': '网页正文节选', 'evidence_truncated': len(raw) > len(excerpt),
                    'document_id': document_id, 'description_truncated': len(raw) > 200000 or bool(saved and saved.get('description_truncated')),
                    'offset': offset, 'freshness': 'cached' if saved else 'refreshed',
                    'fetched_at': saved['fetched_at'] if saved else int(datetime.now(timezone.utc).timestamp())}],
                'has_more': offset + len(excerpt) < len(text), 'next_offset': offset + len(excerpt),
                'note': '已读取网页正文；提取结果可能不含动态加载或受限内容。'}

    async def close(self):
        await self.web.close()
        await self.rss.close()
        self.store.close()
