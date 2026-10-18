"""RSS-only semantic retrieval; stable identities match the Android parser."""
import asyncio
import html
import json
import re
import time
import httpx
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlsplit
from research_store import digest
from error_messages import error_message

CATALOG = json.loads(Path(__file__).with_name('research_catalog.json').read_text(encoding='utf-8'))
CONTENT = '{http://purl.org/rss/1.0/modules/content/}encoded'


def plain(text, paragraphs=False):
    if paragraphs:
        text = re.sub(r'<(script|style)\b[^>]*>[\s\S]*?</\1\s*>', '', text, flags=re.I)
        def anchor(match):
            url, label = html.unescape(match[2]), match[3]
            return f'{label} ({url})' if urlsplit(url).scheme in ('http', 'https') and label != url else label
        text = re.sub(r'<a\b[^>]*href\s*=\s*(["\'])(.*?)\1[^>]*>([\s\S]*?)</a\s*>', anchor, text, flags=re.I)
        text = re.sub(r'<br\s*/?>', '\n', text, flags=re.I)
        text = re.sub(r'<li\b[^>]*>', '\n• ', text, flags=re.I)
        text = re.sub(r'</?(p|div|section|h[1-6]|blockquote|ul|ol|li)\b[^>]*>', '\n\n', text, flags=re.I)
    text = html.unescape(re.sub(r'<[^>]*>', ' ', text))
    if not paragraphs:
        return re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'[^\S\n]+', ' ', text.replace('\r\n', '\n').replace('\r', '\n'))
    return re.sub(r'\n{3,}', '\n\n', re.sub(r' *\n *', '\n', text)).strip()


def parse_feed(raw, source):
    if len(raw) > 8_000_000 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('rss_unsupported')
    channel = ET.fromstring(raw).find('channel')
    if channel is None: raise ValueError('rss_invalid')
    result = []
    title = plain(channel.findtext('title') or source['title'])
    for item in channel.findall('item'):
        name = plain(item.findtext('title') or '')[:16384]
        enclosure = item.find('enclosure')
        audio = enclosure.get('url', '').strip() if enclosure is not None else ''
        if not name or urlsplit(audio).scheme not in ('http', 'https'): continue
        published = (item.findtext('pubDate') or '').strip()
        identity = (item.findtext('guid') or '').strip() or (f'{name}|{published}' if published else audio)
        description = item.find(CONTENT)
        if description is None or not ''.join(description.itertext()).strip(): description = item.find('description')
        raw_body = (ET.tostring(description, encoding='unicode') if len(description) else description.text or '') if description is not None else ''
        body = plain(raw_body, paragraphs=True)
        link = (item.findtext('link') or '').strip()
        result.append({'kind': 'episode', 'id': source['id'] + ':' + digest(identity), 'podcast_id': source['id'],
                       'title': name[:300], 'podcast_title': title[:150], 'published': published[:150],
                       'source_url': source['feed_url'], 'original_url': link if urlsplit(link).scheme in ('http', 'https') else '',
                       'description': body[:262144], 'description_truncated': len(body) > 262144})
        if len(result) >= 1000: break
    return list({row['id']: row for row in result}.values())


def chunks(row):
    body = row['description']
    # Short overlapping passages fit a Chinese 512-token encoder with title.
    return [(start, body[start:start + 320]) for start in range(0, len(body), 280)] or [(0, row['title'])]


class RssResearch:
    def __init__(self, store, client, model_dir):
        self.store, self.client, self.model_dir = store, client, str(model_dir)
        self.slots = asyncio.Semaphore(3)
        self.embedding_lock = asyncio.Lock()
        self.encoder = None
        self.background = None
        self.closed = False

    def encoder_sync(self):
        if self.encoder is None:
            from fastembed import TextEmbedding
            # Use the pinned artifact directly: Hub cache metadata contains host-specific paths.
            snapshot = Path(self.model_dir) / 'models--Qdrant--bge-small-zh-v1.5' / 'snapshots' / '46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59'
            self.encoder = TextEmbedding('BAAI/bge-small-zh-v1.5', cache_dir=self.model_dir, threads=4,
                                         local_files_only=True, specific_model_path=str(snapshot))
        return self.encoder

    async def load(self, source, force=False, diagnostics=None):
        fetched, rows = self.store.feed(source['id'])
        if not force and rows and time.time() - fetched < 3600: return fetched, rows
        async with self.slots:
            try:
                async with self.client.stream('GET', source['feed_url'], follow_redirects=True,
                                              headers={'Accept': 'application/rss+xml, application/xml, */*',
                                                       'User-Agent': 'MaoerLite/0.3 RSS'}, timeout=15) as response:
                    response.raise_for_status()
                    raw = bytearray()
                    async for block in response.aiter_bytes():
                        raw.extend(block)
                        if len(raw) > 8_000_000: raise ValueError('rss_too_large')
                updated = await asyncio.to_thread(parse_feed, bytes(raw), source)
                updated_at = int(time.time())
                self.store.save_feed(source['id'], updated_at, updated)
                fetched, rows = updated_at, updated
            except Exception as exc:
                # Preserve a real last-successful timestamp; never call it fresh.
                code, status = 'rss_load_failed', None
                if isinstance(exc, httpx.ConnectTimeout): code = 'rss_connect_timeout'
                elif isinstance(exc, httpx.TimeoutException): code = 'rss_read_timeout'
                elif isinstance(exc, httpx.HTTPStatusError):
                    status = exc.response.status_code
                    code = {404: 'rss_not_found', 401: 'rss_access_denied', 403: 'rss_access_denied', 429: 'rss_rate_limited'}.get(status, 'rss_http_error')
                elif isinstance(exc, httpx.RequestError): code = 'rss_connection_failed'
                elif isinstance(exc, sqlite3.Error): code = 'server_storage_error'
                elif isinstance(exc, (ValueError, ET.ParseError)):
                    code = 'rss_too_large' if str(exc) == 'rss_too_large' else 'rss_parse_failed'
                if diagnostics is not None:
                    diagnostics[source['id']] = {'code': code, 'note': error_message(code, status=status), 'cached': bool(rows)}
            return fetched, rows

    def entries(self, rows):
        for row in rows:
            for start, text in chunks(row):
                encoded = row['title'][:80] + '\n' + text
                yield digest('bge-small-zh-v1.5:' + encoded), row, start, text, encoded

    async def index_rows(self, rows):
        entries = list(self.entries(rows))
        known = self.store.vectors([e[0] for e in entries])
        missing = list({e[0]: e[4] for e in entries if e[0] not in known}.items())
        for offset in range(0, len(missing), 32):
            batch = missing[offset:offset + 32]
            async with self.embedding_lock:
                def encode():
                    return [v.astype('float32').tobytes() for v in self.encoder_sync().embed([b[1] for b in batch], batch_size=32)]
                vectors = await asyncio.to_thread(encode)
            self.store.save_vectors([(b[0], v) for b, v in zip(batch, vectors)])
            await asyncio.sleep(0)

    def start(self):
        if self.background is None:
            self.background = asyncio.create_task(self.maintain())

    async def maintain(self):
        while not self.closed:
            for source in CATALOG:
                try:
                    _, rows = await self.load(source)
                    await self.index_rows(rows)
                except Exception:
                    pass  # Query response reports actual vector coverage.
            await asyncio.sleep(3600)

    async def search(self, query, podcast_id='', required_terms=None, excluded_terms=None):
        required_terms, excluded_terms = required_terms or [], excluded_terms or []
        sources = [s for s in CATALOG if not podcast_id or s['id'] == podcast_id]
        if not sources: return {'status': 'error', 'code': 'rss_podcast_missing', 'note': error_message('rss_podcast_missing')}
        failures = {}
        feeds = await asyncio.gather(*(self.load(s, diagnostics=failures) for s in sources))
        if failures and not any(rows for _, rows in feeds):
            return {'status': 'error', 'code': 'rss_all_unavailable', 'note': error_message('rss_all_unavailable'),
                    'failures': failures, 'coverage_available': 0, 'coverage_total': len(sources), 'items': []}
        rows = [r for _, group in feeds for r in group]
        # Literal predicates are evaluated over stored descriptions, never over
        # ranked passages. A truncated document cannot prove an exclusion.
        def literal_check(row):
            body = row['description'].casefold()
            present = [term for term in required_terms if term.casefold() in body]
            excluded = [term for term in excluded_terms if term.casefold() in body]
            complete = not row['description_truncated']
            return {'scope': 'stored_episode_description_literal_only', 'description_complete': complete,
                    'required_terms_found': present, 'excluded_terms_found': excluded,
                    'required_terms': required_terms, 'excluded_terms': excluded_terms,
                    'passes': len(present) == len(required_terms) and not excluded and (not excluded_terms or complete),
                    'audio_checked': False, 'synonyms_checked': False}
        checks = {row['id']: literal_check(row) for row in rows}
        literal_counts = [
            {'literal_term': term,
             'descriptions_containing': sum(term.casefold() in r['description'].casefold() for r in rows),
             'descriptions_complete': sum(not r['description_truncated'] for r in rows)}
            for term in dict.fromkeys(required_terms + excluded_terms)
        ]
        entries = list(self.entries(rows))
        vectors = self.store.vectors([e[0] for e in entries])
        vector = None
        try:
            async with self.embedding_lock:
                vector = await asyncio.to_thread(lambda: next(iter(self.encoder_sync().query_embed(query))))
        except Exception:
            pass
        terms = re.findall(r'[a-z0-9]+|[\u4e00-\u9fff]{2}', query.lower())
        scored = []
        if vector is not None: import numpy as np
        for key, row, start, text, _ in entries:
            if not checks[row['id']]['passes']: continue
            haystack = (row['title'] + ' ' + text).lower()
            lexical = sum(t in haystack for t in terms) / max(1, len(terms))
            semantic = float(np.dot(np.frombuffer(vectors[key], dtype='float32'), vector)) if vector is not None and key in vectors else 0
            if lexical >= 0.25 or semantic >= 0.45:
                scored.append((0.65 * semantic + 0.35 * lexical, row, start, text))
        scored.sort(key=lambda e: e[0], reverse=True)
        items, selected = [], set()
        timestamps = {row['id']: fetched for fetched, group in feeds for row in group}
        matched_episodes = len({row['id'] for _, row, _, _ in scored})
        for score, row, start, text in scored:
            if row['id'] in selected: continue
            selected.add(row['id'])
            # Short descriptions can be read completely within the result budget.
            if len(row['description']) <= 1000:
                start, text = 0, row['description']
            item = self.evidence(row, text, start, timestamps.get(row['id'], 0))
            item['description_checks'] = checks[row['id']]
            items.append(item)
            if len(items) == 6: break
        result = {'status': 'ok', 'items': items, 'failures': failures, 'coverage_available': sum(bool(r) for _, r in feeds),
                'retrieval_scope': 'ranked_relevant_passages_not_exhaustive_findings',
                'description_count': len(rows), 'matched_episodes': matched_episodes,
                'returned': len(items), 'omitted': matched_episodes - len(items),
                'literal_filter': {'required_terms': required_terms, 'excluded_terms': excluded_terms,
                    'operator': 'all_required_and_none_excluded', 'term_counts': literal_counts,
                    'evaluated_descriptions': len(rows), 'eligible_descriptions': sum(c['passes'] for c in checks.values()),
                    'incomplete_descriptions': sum(r['description_truncated'] for r in rows),
                    'scope': 'stored_RSS_descriptions_only_not_audio_or_synonyms',
                    'conclusion_scope': '只证明term_counts中每个精确字面词的出现次数和组合筛选结果；其他词、同义声明、商业性质未检查。没有检查词时没有全文字面核验结论。'},
                'negative_claim_supported': False,
                'coverage_total': len(sources), 'semantic_indexed_passages': len(vectors) if vector is not None else 0,
                'total_passages': len(entries), 'audio_analyzed': False,
                'note': f'RSS可用源 {sum(bool(r) for _, r in feeds)}/{len(sources)}；相关候选 {matched_episodes} 条，返回 {len(items)} 条。索引覆盖只说明检索可用性，不是全文审阅。字面筛选仅证明指定词在已存说明中的包含/排除，不证明音频或同义表述；未经全文检查不将片段未见写成全文没有。'}
        if required_terms or excluded_terms:
            label = sources[0]['title'] if len(sources) == 1 else '所查询节目'
            lines = [f'{label}：已存RSS说明字面检查',
                     f'可用节目源：{result["coverage_available"]}/{len(sources)}；检查说明：{len(rows)}条；其中截断说明：{result["literal_filter"]["incomplete_descriptions"]}条。',
                     '包含条件（全部满足）：' + json.dumps(required_terms, ensure_ascii=False),
                     '排除条件（任一命中即排除）：' + json.dumps(excluded_terms, ensure_ascii=False)]
            lines += [f'精确词{json.dumps(c["literal_term"], ensure_ascii=False)}：{c["descriptions_containing"]}条说明包含。' for c in literal_counts]
            lines += [f'符合组合字面条件：{result["literal_filter"]["eligible_descriptions"]}条；相关性检索命中：{matched_episodes}条；实际展示：{len(items)}条。',
                      '这是程序对本次已存RSS说明的统计，不是某一分集原文；截断部分及不可用来源未检查。只证明上述精确词，不证明同义表述、商业性质或音频内容。']
            evidence = '\n'.join(lines)
            snapshot = json.dumps({'evidence': evidence, 'feeds': [(s['id'], f[0]) for s, f in zip(sources, feeds)]}, ensure_ascii=False)
            result['statistics'] = {'kind': 'rss_statistics', 'id': digest(snapshot),
                'title': label + ' · RSS说明字面统计', 'evidence': evidence,
                'evidence_field': '程序统计（非分集原文）', 'evidence_truncated': False,
                'source_url': '', 'freshness': 'computed'}
        return result

    def evidence(self, row, excerpt, start, fetched):
        return {k: v for k, v in row.items() if k != 'description'} | {
            'evidence': excerpt, 'evidence_field': 'RSS分集说明', 'evidence_truncated': start > 0 or len(excerpt) < len(row['description']) or row['description_truncated'],
            'document_id': self.store.document(row['description']), 'offset': start,
            'fetched_at': fetched, 'freshness': 'recent_cache' if time.time() - fetched < 3600 else 'cached'}

    async def read(self, podcast_id, episode_id, offset=0):
        source = next((s for s in CATALOG if s['id'] == podcast_id), None)
        if not source: return {'status': 'error', 'code': 'rss_podcast_missing', 'note': error_message('rss_podcast_missing')}
        failures = {}
        fetched, rows = await self.load(source, diagnostics=failures)
        if not rows and failures: return {'status': 'error', **failures[podcast_id]}
        row = next((r for r in rows if r['id'] == episode_id), None)
        if not row: return {'status': 'error', 'code': 'rss_episode_missing', 'note': error_message('rss_episode_missing')}
        body = row['description']
        excerpt = body[offset:offset + 4000]
        more = offset + len(excerpt) < len(body)
        return {'status': 'ok', 'items': [self.evidence(row, excerpt, offset, fetched)], 'failures': failures, 'has_more': more,
                'next_offset': offset + len(excerpt), 'total_characters': len(body),
                'note': '分集RSS说明，非音频转写。' + ('源文本超过读取上限，完整性未保证。' if row['description_truncated'] else '')}

    async def close(self):
        self.closed = True
        if self.background:
            self.background.cancel()
            await asyncio.gather(self.background, return_exceptions=True)
