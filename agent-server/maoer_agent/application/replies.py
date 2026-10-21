"""Presentation of fixed page-lookup results, independent of workflow decisions."""
from urllib.parse import quote
from datetime import datetime, timezone, timedelta
from maoer_agent.research.updates import publication_time


def _text(value):
    for character in ('\\', '[', ']', '*', '_', '`'):
        value = value.replace(character, '\\' + character)
    return value.replace('\n', ' ').replace('\r', ' ')


def _link(url, label):
    return '[' + label + '](' + quote(url, safe="/:?#[]@!$&'*+,;=%~.-_") + ')'


def page_reply(step):
    status = step['resolution']['status']
    data = step['presentation']
    subject = f"《{_text(data['title'])}》"
    homepage = data.get('target_kind') == 'podcast_homepage'
    page_name = '节目主页' if homepage else '独立网页'
    if status == 'ambiguous':
        candidates = data['candidates']
        if data['candidate_kind'] == 'podcast':
            return '查到多个节目，请选择：\n' + '\n'.join(f"{n}. {i['title']}" for n, i in enumerate(candidates, 1))
        return '查到多个同名分集，请选择：\n' + '\n'.join(
            f"{n}. {i['title']}（{i.get('podcast_title', '')}，{i.get('published', '日期未知')}）"
            for n, i in enumerate(candidates, 1))
    if status == 'found':
        number = data.get('citation')
        return f"{subject}的{page_name}：{_link(data['url'], '打开节目主页' if homepage else '打开分集网页')}。" + (f'[{number}]' if number else '')
    if status == 'budget_exhausted':
        verb = '核实' if data.get('verification') else '取得'
        return f'本次查询额度已用完，未能{verb}{subject}的{page_name}链接。'
    templates = {
        'local_only': f'现有节目资料中没有找到{subject}的独立网页链接。',
        'unsupported': '当前不支持搜索独立网页链接。',
        'unavailable': f'网页查询未能取得完整结果，无法确认{subject}的独立网页链接。',
        'search_failed': f'网页搜索失败，未能取得{subject}的独立网页链接。',
        'read_failed': f'网页读取失败，未能核实{subject}的独立网页链接。',
        'not_found': f'本次搜索未找到{subject}的独立网页链接。',
    }
    return templates[status].replace('独立网页', page_name)


def selection_reply(step):
    result = step['result']
    if result.get('status') != 'ok':
        return result.get('note') or '分集查询失败。'
    label = {'reverse_source': '原始列表倒数第', 'source': '原始列表第',
             'newest': '按发布时间排序第', 'title_number': '标题期号第'}[step['selector_kind']]
    target = f"《{_text(step['title'])}》{label}{step['number']}"
    items = result.get('items', [])
    if not items:
        return f'未找到{target}的分集。'
    rows = []
    for n, item in enumerate(items, 1):
        duration = item.get('duration_seconds', 0)
        detail = f'，时长{duration}秒' if duration > 0 else ''
        if item.get('published'):
            stamp = publication_time(item['published'])
            published = (datetime.fromtimestamp(stamp, timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M 北京时间')
                         if stamp is not None else item['published'])
            detail += '，发布时间：' + _text(published)
        rows.append(f"{n}. **{_text(item['title'])}**{detail}")
    return target + '的查询结果：\n\n' + '\n'.join(rows) + ('\n\n还有后续结果可继续查询。' if result.get('has_more') else '')


def query_reply(step):
    if step['kind'] == 'library':
        return library_reply(step)
    if step['kind'] == 'unsupported':
        labels = {'add_favorite': '添加收藏', 'remove_favorite': '取消收藏',
                  'mark_completed': '标记听完', 'download': '下载分集', 'delete_download': '删除下载'}
        return '当前助手不支持' + '、'.join(labels[a] for a in step['actions']) + '。'
    if step['kind'] == 'introduction':
        item = step['item']
        title = '**' + _text(item['title']) + '**'
        if item.get('podcast_title'):
            title += ' · ' + _text(item['podcast_title'])
        if item.get('duration_seconds', 0) > 0:
            title += f"（{item['duration_seconds']}秒）"
        number = item.get('citation')
        return title + '\n\n' + _text(item['evidence']) + (f'[{number}]' if number else '')
    return selection_reply(step)


def library_reply(step):
    pages = step['pages']
    items = [i for page in pages for i in page['items']]
    name = {'continue': '继续收听', 'favorites': '收藏', 'history': '收听历史', 'downloads': '下载记录'}[pages[0]['tab']]
    if not items:
        return name + '没有匹配记录。' if pages[0]['total'] == 0 else f'{name}共{pages[0]["total"]}条，本次查询页没有记录。'
    head = f'{name}共{pages[0]["total"]}条，本次展示{len(items)}条。'
    episodes = list({i['id']: i for i in items if i['kind'] == 'episode'}.values())
    if episodes and all(type(i.get('favorite')) is bool for i in episodes):
        head += f'\n这些分集中，已收藏{sum(i["favorite"] for i in episodes)}集。'
    def position(value):
        return f'{value // 60000}:{value // 1000 % 60:02}' if type(value) is int and value >= 0 else '未知'
    rows = []
    for page in pages:
        rows.append(f'\n第{page["offset"] + 1}–{page["offset"] + len(page["items"])}条：')
        for n, item in enumerate(page['items'], page['offset'] + 1):
            rows.append(library_item(n, item, position))
    return head + '\n' + '\n'.join(rows)


def library_item(n, item, position):
    label = _text(item['title'])
    if item['kind'] == 'episode':
        label = _text(item.get('podcast_title', '')) + ' · ' + label
        if 'position_ms' in item:
            label += '，听到 ' + position(item['position_ms']) + ' / ' + position(item.get('duration_ms'))
        if item.get('favorite') is True:
            label += '，已收藏'
        if item.get('completed') is True:
            label += '，已听完'
        if 'download_status' in item:
            states = {'complete': '下载完成', 'failed': '下载失败', 'downloading': '下载中',
                      'queued': '等待下载', 'waiting': '下载等待中'}
            label += '，' + states.get(item['download_status'], _text(item['download_status']))
            if type(item.get('bytes')) is int:
                label += f'，{item["bytes"]}字节'
            if type(item.get('offline_available')) is bool:
                label += '，可离线收听' if item['offline_available'] else '，不可离线收听'
    elif item.get('category'):
        label += ' · ' + _text(item['category'])
    return f'{n}. {label}'
