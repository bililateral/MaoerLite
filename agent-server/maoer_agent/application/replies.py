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
