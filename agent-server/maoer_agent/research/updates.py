"""Date-ordered RSS metadata queries, independent of embedding relevance."""
import asyncio
import json
import time
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from maoer_agent.research.rss import CATALOG
from maoer_agent.research.store import digest


def publication_time(value):
    try:
        date = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        try:
            date = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except (TypeError, ValueError, AttributeError):
            return None
    if date.tzinfo is None:
        return None  # Do not invent a timezone for an ambiguous publisher timestamp.
    try:
        stamp = date.timestamp()
        return stamp if stamp >= 0 else None
    except (ValueError, OverflowError, OSError):
        return None


def page_view(result, offset, snapshot):
    ordered = result['items']
    page = ordered[offset:offset + 8]
    more = offset + len(page) < len(ordered)
    return {**result, 'items': page, 'query_snapshot': snapshot,
            'returned': len(page), 'offset': offset, 'has_more': more,
            'next_offset': offset + len(page) if more else 0,
            'note': (f"已查询{result['coverage_total']}档节目；" +
                     ('每档取最近一期' if result['mode'] == 'podcasts' else '合并分集') +
                     (f"，限定最近{result['days']}天" if result['days'] else '，未限定天数') +
                     f'，共{len(ordered)}条，当前展示{len(page)}条。' +
                     '每档最多读取1000个可播放RSS条目；日期缺失、无时区、异常或未来日期不参与排序。刷新失败时注明缓存来源及读取时间。')}


async def recent_updates(rss, mode='podcasts', days=0, podcast_ids=None, offset=0, query_snapshot='', *, now=None, snapshot_scope=''):
    now = time.time() if now is None else now
    ids = set(podcast_ids or [])
    sources = [s for s in CATALOG if not ids or s['id'] in ids]
    if ids - {s['id'] for s in sources}:
        return {'status': 'error', 'code': 'rss_podcast_missing', 'items': [], 'note': '部分节目不在当前内置目录。'}
    query = {'mode': mode, 'days': days, 'podcast_ids': sorted(s['id'] for s in sources)}
    # Public query results live in knowledge storage. The opaque conversation
    # scope only locates the latest result for legacy offset-only callers.
    latest_key = 'recent_updates:latest:' + digest(json.dumps([snapshot_scope, query], sort_keys=True)) if snapshot_scope else ''
    snapshot = query_snapshot
    if not snapshot and offset and latest_key:
        snapshot = rss.store.state(latest_key).get('snapshot', '')
    if snapshot:
        saved = rss.store.state('recent_updates:snapshot:' + snapshot)
        if not saved or saved.get('query') != query:
            return {'status': 'error', 'code': 'recent_updates_snapshot_missing', 'items': [],
                    'note': '更新列表快照不存在或查询条件不匹配，请重新查询第一页。'}
        return page_view(saved['result'], offset, snapshot)
    failures, feeds = {}, {}

    async def load(source):
        # Queries verify publication order against a current fetch; unchanged feeds can use HTTP cache semantics upstream.
        fetched, rows = await rss.load(source, force=True, diagnostics=failures)
        feeds[source['id']] = (fetched, rows)

    jobs = [asyncio.create_task(load(s)) for s in sources]
    try:
        done, pending = await asyncio.wait(jobs, timeout=25)
        for job in pending:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
    except BaseException:
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        raise
    unavailable, stale, invalid, future = [], [], 0, 0
    candidates, available, fresh = [], 0, 0
    for source in sources:
        fetched, rows = feeds.get(source['id'], rss.store.feed(source['id']))
        succeeded = source['id'] in feeds and source['id'] not in failures
        if not succeeded:
            stale.append(source['id'])
        if fetched > 0:
            available += 1
        else:
            unavailable.append(source['id'])
        if succeeded:
            fresh += 1
        dated = []
        for row in rows:
            stamp = publication_time(row.get('published', ''))
            if stamp is None:
                invalid += 1
                continue
            if stamp > now:
                future += 1
                continue
            if days and stamp < now - days * 86400:
                continue
            dated.append((stamp, row))
        dated.sort(key=lambda pair: (-pair[0], pair[1]['id']))
        for stamp, row in (dated[:1] if mode == 'podcasts' else dated):
            item = {key: row.get(key, '') for key in ('kind', 'id', 'podcast_id', 'title', 'podcast_title', 'published', 'source_url', 'original_url')}
            display = datetime.fromtimestamp(stamp, timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M 北京时间')
            item.update(published_at=int(stamp), published_display=display, fetched_at=fetched, freshness='refreshed' if succeeded else 'stale_cache',
                        evidence=f"节目：{row.get('podcast_title', source['title'])}\n分集：{row['title']}\nRSS发布日期：{row.get('published', '')}\n发布日期（北京时间）：{display}",
                        evidence_field='RSS分集元数据（发布日期）', evidence_truncated=False)
            candidates.append(item)
    unique = {item['id']: item for item in candidates}
    ordered = sorted(unique.values(), key=lambda item: (-item['published_at'], item['podcast_id'], item['id']))
    result = {'status': 'ok' if available else 'error', 'code': '' if available else 'rss_all_unavailable',
            'items': ordered, 'mode': mode, 'days': days, 'as_of': int(now),
            'as_of_display': datetime.fromtimestamp(now, timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M 北京时间'),
            'date_scope': 'published_display为北京时间发布日期，published保留RSS原始时区；fetched_at为刷新时间',
            'count_scope': '每档最近一期的数量' if mode == 'podcasts' else '符合查询条件的分集数量',
            'date_window_days': days if days else None,
            'scope': '内置全部节目' if not ids else '指定节目', 'coverage_total': len(sources),
            'coverage_available': available, 'coverage_fresh': fresh, 'freshness_complete': fresh == len(sources),
            'stale_or_failed_podcast_ids': stale, 'unavailable_podcast_ids': unavailable,
            'invalid_date_items': invalid, 'future_date_items': future, 'total_matches': len(ordered)}
    saved = {'query': query, 'result': result}
    snapshot = digest(json.dumps(saved, ensure_ascii=False, sort_keys=True))
    rss.store.save_state('recent_updates:snapshot:' + snapshot, saved)
    if latest_key: rss.store.save_state(latest_key, {'snapshot': snapshot})
    return page_view(result, offset, snapshot)
