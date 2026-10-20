"""Presentation of fixed page-lookup results, independent of workflow decisions."""
from urllib.parse import quote


def _text(value):
    for character in ('\\', '[', ']', '*', '_', '`'):
        value = value.replace(character, '\\' + character)
    return value.replace('\n', ' ').replace('\r', ' ')


def _link(url):
    return '[打开分集网页](' + quote(url, safe="/:?#[]@!$&'*+,;=%~.-_") + ')'


def page_reply(step):
    status = step['resolution']['status']
    data = step['presentation']
    subject = f"《{_text(data['title'])}》"
    if status == 'ambiguous':
        candidates = data['candidates']
        if data['candidate_kind'] == 'podcast':
            return '查到多个节目，请选择：\n' + '\n'.join(f"{n}. {i['title']}" for n, i in enumerate(candidates, 1))
        return '查到多个同名分集，请选择：\n' + '\n'.join(
            f"{n}. {i['title']}（{i.get('podcast_title', '')}，{i.get('published', '日期未知')}）"
            for n, i in enumerate(candidates, 1))
    if status == 'found':
        number = data.get('citation')
        return f"{subject}的独立网页：{_link(data['url'])}。" + (f'[{number}]' if number else '')
    if status == 'budget_exhausted':
        verb = '核实' if data.get('verification') else '取得'
        return f'本次查询额度已用完，未能{verb}{subject}的独立网页链接。'
    templates = {
        'local_only': f'现有节目资料中没有找到{subject}的独立网页链接。',
        'unsupported': '当前不支持搜索独立网页链接。',
        'unavailable': f'网页查询未能取得完整结果，无法确认{subject}的独立网页链接。',
        'search_failed': f'网页搜索失败，未能取得{subject}的独立网页链接。',
        'read_failed': f'网页读取失败，未能核实{subject}的独立网页链接。',
        'not_found': f'本次搜索未找到{subject}的独立网页链接。',
    }
    return templates[status]
