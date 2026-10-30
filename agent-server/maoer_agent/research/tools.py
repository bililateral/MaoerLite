"""Server-side read-only tools with a hard, persistent free-credit budget."""
from maoer_agent.core.errors import error_message
from datetime import datetime, timezone
from urllib.parse import urlsplit
from maoer_agent.research.store import digest
from maoer_agent.core.urls import public_url, public_links, known_public_url
from maoer_agent.contracts.execution import ResearchContext

WEB_PAGE_CHARACTERS = 6000



class ResearchTools:
    def __init__(self, store, rss, web, web_relevance=None):
        self.store, self.rss, self.web = store, rss, web
        self.web_relevance=web_relevance

    async def perform(self, call, context: ResearchContext):
        name, args = call['name'], call['arguments']
        if name == 'list_recent_updates':
            from maoer_agent.research.updates import recent_updates
            return await recent_updates(self.rss, **args, snapshot_scope=context.query_scope)
        if name == 'search_rss': return await self.rss.search(**args, relevance_model=context.relevance_model)
        if name == 'read_episode_description': return await self.rss.read(**args)
        request_id = context.request_key
        if name == 'web_search':
            query=args['query']
            as_of=getattr(context,'as_of_date','')
            if args.get('delivery','answer')!='results' and as_of and as_of not in query:
                query += ' as of ' + as_of
            checkpoint = context.checkpoint
            if checkpoint is None:
                result = await self.web.tavily('search', {'query': query, 'search_depth': 'basic', 'max_results': 8,
                                                    'include_answer': False, 'include_raw_content': False,
                                                    'auto_parameters': False}, request_id)
                if result.get('status') == 'error': return result
                # Commit acquired evidence before model processing. A retry can
                # reassess these candidates without repeating the external request.
                checkpoint = {'response': result, 'fetched_at': int(datetime.now(timezone.utc).timestamp())}
                if context.save_checkpoint is not None:
                    context.save_checkpoint(checkpoint)
            result = checkpoint['response']
            items = []
            for row in result.get('results', [])[:8]:
                url = row.get('url', '')
                if not public_url(url): continue
                items.append({'kind': 'web', 'id': digest(url), 'title': row.get('title', '')[:300],
                              'source_url': url, 'original_url': url, 'published': row.get('published_date') or '',
                              'evidence': row.get('content', '')[:1400], 'evidence_field': '网页搜索摘要（未读正文）',
                              'evidence_level': 'search_summary',
                              'evidence_truncated': True, 'freshness': 'refreshed',
                              'fetched_at': checkpoint['fetched_at']})
            candidates={}
            if args.get('delivery')=='results' and self.web_relevance is not None and items:
                original=items
                documents=[{'id':str(i),'title':item['title'],'url':item['source_url'],'passages':[{'id':'search','text':item['evidence']}]} for i,item in enumerate(original)]
                try:
                    selection=await self.web_relevance.select(context.information_request or args['query'],documents,getattr(context,'web_relevance_model',None))
                except ValueError as error:
                    return {'status':'error','code':'invalid_model_response','note':error_message('invalid_model_response'),
                        'stage':'web_relevance','reason':str(error)[:500],'items':[],'candidate_items':original}
                items=[{**original[int(row['id'])],'search_match':{**row['match'],'query':args['query'],'request':context.information_request}} for row in selection.items]
                candidates={'candidate_items':original,'relevance':{'candidate_count':len(original),'selected_count':len(items),'basis':'search_title_url_excerpt','rejected':selection.rejected}}
                if not items and selection.rejected:
                    return {'status':'error','code':'invalid_model_response','stage':'web_relevance','note':error_message('invalid_model_response'),'items':[],**candidates}
            return {'status': 'ok', 'items': items, 'delivery': args.get('delivery', 'answer'), 'query_as_of':as_of, **candidates,
                    'note': '搜索摘要不是网页正文；核实关键事实时继续读取原网页。'}
        url = args['url']
        # Prior real links are locators; only a new read supplies this turn's evidence.
        known = {s.get('source_url') for s in context.sources} | set(getattr(context,'known_urls',()))
        for source in context.sources:
            known.update(public_links(source.get('evidence', '')))
        known.update(public_links(context.question))
        url = known_public_url(url, known)
        if url is None:
            return {'status': 'error', 'code': 'web_url_not_allowed', 'note': error_message('web_url_not_allowed')}
        saved = next((s for s in context.sources if s.get('source_url') == url and s.get('document_id')), None)
        raw = self.store.read(saved['document_id']) if saved else ''
        if not raw:
            result = await self.web.tavily('extract', {'urls': [url], 'extract_depth': 'basic', 'format': 'text',
                                                 'include_images': False}, request_id)
            if result.get('status') == 'error': return result
            rows = result.get('results', [])
            if not rows: return {'status': 'error', 'code': 'web_extract_failed', 'note': error_message('web_extract_failed')}
            raw = rows[0].get('raw_content', '')
        if not raw: return {'status': 'error', 'code': 'web_body_empty', 'note': error_message('web_body_empty')}
        title = next((s['title'] for s in context.sources if s.get('source_url') == url), urlsplit(url).hostname)
        text = raw[:200000]
        document_id = self.store.document(text)
        # Offsets select evidence ranges. A keyword locates text without silently
        # skipping the start, including when it only matches navigation/footer text.
        query = args.get('query', '').strip()
        offset = args.get('offset', 0)
        position = text.lower().find(query.lower()) if query else -1
        excerpt = text[offset:offset + WEB_PAGE_CHARACTERS]
        return {'status': 'ok', 'items': [{'kind': 'web', 'id': digest(url), 'title': title,
                    'source_url': url, 'original_url': url, 'evidence': excerpt,
                    'evidence_field': '网页正文节选', 'evidence_truncated': len(raw) > len(excerpt),
                    'evidence_level': 'page_excerpt',
                    'document_id': document_id, 'description_truncated': len(raw) > 200000 or bool(saved and saved.get('description_truncated')),
                    'offset': offset, 'freshness': 'cached' if saved else 'refreshed',
                    'fetched_at': saved['fetched_at'] if saved else int(datetime.now(timezone.utc).timestamp())}],
                'content_length': len(text), 'query_match_offset': position,
                'has_previous': offset > 0, 'previous_offset': max(0, offset - WEB_PAGE_CHARACTERS),
                'has_more': offset + len(excerpt) < len(text), 'next_offset': offset + len(excerpt),
                'note': '已读取网页正文；提取结果可能不含动态加载或受限内容。'}
