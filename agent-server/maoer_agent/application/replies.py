"""Presentation of fixed page-lookup results, independent of workflow decisions."""
from urllib.parse import quote
from datetime import datetime, timezone, timedelta
import re
import json
from maoer_agent.research.updates import publication_time
from maoer_agent.research.sources import citation_numbers,canonical_citations
from maoer_agent.core.urls import public_links
from maoer_agent.core.errors import error_message


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
    page_name = '节目主页' if homepage else '完整文稿网页' if data.get('target_kind') == 'episode_transcript' else '独立网页'
    if status == 'ambiguous':
        candidates = data['candidates']
        if data['candidate_kind'] == 'podcast':
            return '查到多个节目，请选择：\n' + '\n'.join(f"{n}. {i['title']}" for n, i in enumerate(candidates, 1))
        return '查到多个同名分集，请选择：\n' + '\n'.join(
            f"{n}. {i['title']}（{i.get('podcast_title', '')}，{i.get('published', '日期未知')}）"
            for n, i in enumerate(candidates, 1))
    if status == 'found':
        number = data.get('citation')
        if data.get('publisher_transcript_link'):
            return f"{subject}节目方在RSS中标注的完整文稿链接：{_link(data['url'], '打开文稿页面')}。" + (f'[{number}]' if number else '')
        return f"{subject}的{page_name}：{_link(data['url'], '打开' + page_name)}。" + (f'[{number}]' if number else '')
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
             'newest': '按发布时间排序第', 'title_number': '标题期号第', 'displayed_page': '该页第'}[step['selector_kind']]
    target = f"《{_text(step['title'])}》{label}{step['number']}"
    items = result.get('items', [])
    if not items:
        if step['selector_kind'] in ('reverse_source','source','newest') and type(result.get('feed_total')) is int and step['number']>result['feed_total']:
            return f"{target}超出本次RSS列表范围（共{result['feed_total']}条）。"
        return f'未找到{target}的分集。'
    if step.get('titles_only'):
        return '\n'.join(_text(item['title']) for item in items)
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
    if step['kind']=='query_failures':
        rows=['关于“'+_text(f['request'])+'”：'+({'error':'查询失败，本轮无法确认。','unavailable':'查证不可用，本轮无法确认。'}.get(f['status'],'查询结果无法确认。'))
              +error_message(f['code']) for f in step['failures']]
        if step.get('receipts'):
            rows.append(query_reply({'kind':'control_receipts','receipts':step['receipts'],'unsupported':[]}))
        return '\n'.join(rows)
    if step['kind']=='literal_titles':
        return ('\n'.join(f"{n}. **{_text(item['title'])}**[{item['citation']}]" for n,item in enumerate(step['items'],1))
                if step['items'] else '未找到符合字面条件的分集。')
    if step['kind']=='waiting_selection':
        rows=[]
        if step['receipts']:rows.append(query_reply({'kind':'control_receipts','receipts':step['receipts'],'unsupported':[]}))
        rows.append(query_reply({'kind':'episode_page','result':step['result'],'titles_only':False}))
        rows.append(step['question'])
        return '\n\n'.join(rows)
    if step['kind']=='duration_candidates':
        rows=[]
        for n,item in enumerate(step['items'],1):
            seconds=item['duration_seconds']
            rows.append(f"{n}. **{_text(item['title'])}** · {seconds//60}分{seconds%60}秒[{item['citation']}]")
        return '\n'.join(rows)
    if step['kind']=='selection_clarification':
        done=query_reply({'kind':'control_receipts','receipts':step['receipts']}) if step['receipts'] else ''
        return (done+'\n' if done else '')+'你指的是哪个节目或哪份列表中的条目？请提供节目或分集名称。'
    if step['kind']=='acknowledgement':return step.get('text') or '已记下。'
    if step['kind']=='control_receipts':
        rows=[]
        actions={'play_episode':'播放','pause_playback':'暂停','resume_playback':'继续播放',
            'previous_episode':'切换上一集','next_episode':'切换下一集','set_playback_speed':'调整倍速','set_sleep_timer':'设置定时'}
        for receipt in step['receipts']:
            name=receipt['name'];result=receipt['result'];args=receipt['arguments'];action=actions[name]
            status=result.get('status','unknown')
            if result.get('executed') is False:text=action+'未执行。'
            elif status!='ok':
                text=action+('失败。' if status=='error' else '结果无法确认。')
                if result.get('note'):text+=result['note']
            elif name=='set_playback_speed':text=f"已调整为{result['playback_speed']:g}倍速。"
            elif name=='set_sleep_timer':
                minutes=args['minutes']
                text='已取消睡眠定时。' if minutes==0 else '已设置本集结束后停止。' if minutes==-1 else f'已设置{minutes}分钟睡眠定时。'
            elif name in ('play_episode','previous_episode','next_episode'):
                text={'play_episode':'已开始播放','previous_episode':'已切换上一集并播放','next_episode':'已切换下一集并播放'}[name]
                text+=('《'+_text(result['title'])+'》' if result.get('title') else '')+'。'
            else:text='已'+action+'。'
            if result.get('observation_scope')=='execution_before_process_restart':text='进程重启前的执行结果：'+text
            rows.append(text)
        if step.get('unsupported'):rows.append(query_reply({'kind':'unsupported','actions':step['unsupported']}))
        return '\n'.join(rows)
    if step['kind']=='unsupported_setting':
        if step['name']=='set_playback_speed':return f"不支持{step['arguments']['speed']:g}倍速。"
        return f"睡眠定时不支持{step['arguments']['minutes']}分钟。"
    if step['kind'] == 'execution_facts':
        result = step['result']
        operation = {'play_episode': '播放分集', 'pause_playback': '暂停', 'resume_playback': '继续播放',
            'previous_episode': '上一集', 'next_episode': '下一集', 'set_playback_speed': '调整倍速',
            'set_sleep_timer': '设置或取消定时'}[step['name']]
        status = result.get('status', 'unknown')
        facts = {'操作': operation, '结果': {'ok': '已完成', 'error': '失败', 'unknown': '无法确认',
            'accepted': '已接收，效果尚未确认', 'confirmation_required': '待用户确认，未执行'}.get(status, '无法确认')}
        if result.get('executed') is False:
            facts['结果'] = '未执行'
        if result.get('note'):
            facts['说明'] = result['note']
        if result.get('observation_scope') == 'execution_before_process_restart':
            facts['观察阶段'] = '进程重启前的执行结果，当前状态另看最新观察'
        return json.dumps(facts, ensure_ascii=False)
    if step['kind'] == 'playback_facts':
        result = step['result']
        status = result.get('status', 'unknown')
        if status != 'ok':
            return json.dumps({'查询结果': '查询失败' if status == 'error' else '无法确认',
                               '原因': result.get('note', '')}, ensure_ascii=False)
        state = result.get('playback_state')
        if not state and type(result.get('playing')) is bool:
            state = 'playing' if result['playing'] else 'buffering' if result.get('buffering') else 'paused'
        facts = {'播放状态': {'playing': '播放中', 'paused': '暂停', 'buffering': '缓冲中', 'idle': '未选中分集'}.get(state, '无法确认')}
        labels = {'title': '当前选中分集', 'podcast_title': '所属节目',
            'playback_speed': '倍速', 'queue_size': '当前队列总数',
            'queue_position': '当前队列序号（从1开始，0表示未在队列中）',
            'sleep_end_of_episode': '本集结束停止', 'sleep_timer_counts_while_paused': '暂停时定时继续计时',
            'playback_error': '播放错误', 'download_available': '当前节目允许下载',
            'download_available_sources': '允许下载的节目数'}
        facts.update({label: result[key] for key, label in labels.items() if key in result})
        for key, label in (('position_ms', '播放位置'), ('duration_ms', '总时长'), ('sleep_remaining_ms', '定时剩余')):
            if type(result.get(key)) is int and result[key] >= 0:
                seconds = result[key] // 1000
                facts[label] = f'{seconds // 60}分{seconds % 60}秒'
        return json.dumps(facts, ensure_ascii=False)
    if step['kind'] == 'factual_answer':
        rows = []
        for block in step['blocks']:
            text = canonical_citations(block['text'],set(block['sources']))
            # Search-result links identify their own source exactly. Bind those
            # citations at the link, before any later independent task result.
            if step.get('link_sources'):
                for source in step.get('sources', []):
                    number, url = source['number'], source.get('source_url', '')
                    if number not in block['sources'] or number in citation_numbers(text) or not url:
                        continue
                    match = re.search(re.escape(url) + r'(?=\s|$)', text)
                    if match:
                        text = text[:match.end()] + f' [{number}]' + text[match.end():]
            suffix = re.search(r'(?:\[\d{1,3}\])+$', text)
            existing = [int(n) for n in re.findall(r'\[(\d+)\]', suffix[0])] if suffix else []
            cited = set(citation_numbers(text))
            numbers = list(dict.fromkeys(existing + [n for n in block['sources'] if n not in cited]))
            rows.append((text[:suffix.start()] if suffix else text) + ''.join(f'[{number}]' for number in numbers))
        if step.get('include_source_links'):
            cited={n for block in step['blocks'] for n in block['sources']}
            linked=set(public_links('\n\n'.join(rows)))
            for source in step.get('sources', []):
                url=source.get('source_url','')
                if source['number'] in cited and source.get('kind')=='web' and url and url not in linked:
                    rows.append(f"{url} [{source['number']}]")
                    linked.add(url)
        if step.get('question'):rows.append(step['question'])
        return '\n\n'.join(rows)
    if step['kind'] == 'cancel_timer_explanation':
        return ('取消睡眠定时会取消按分钟倒计时或“本集结束”定时，不改变当前播放或暂停状态。'
                '取消后不会因原定时自动停止；播放队列仍按原规则首尾循环。')
    if step['kind'] == 'recovered_selection':
        current = step['current']
        state = '正在播放' if current.get('playing') else '正在加载' if current.get('buffering') else '已暂停'
        return f"已切换到《{_text(current.get('title', ''))}》，当前{state}。"
    if step['kind'] == 'episode_page':
        result = step['result']
        items = result.get('items', [])
        if not items:
            return '本页没有分集。'
        rows = []
        for number, item in enumerate(items, 1):
            row = f"{number}. **{_text(item['title'])}**"
            if not step.get('titles_only'):
                stamp = publication_time(item.get('published', ''))
                if stamp is not None:
                    row += ' · ' + datetime.fromtimestamp(stamp, timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M 北京时间')
                if item.get('duration_seconds', 0) > 0:
                    row += f" · {item['duration_seconds'] // 60}分{item['duration_seconds'] % 60}秒"
            rows.append(row)
        head = '' if step.get('titles_only') else f"第{result.get('offset', 0) + 1}–{result.get('offset', 0) + len(items)}条：\n\n"
        return head + '\n'.join(rows) + ('\n\n还有后续分集。' if result.get('has_more') and not step.get('titles_only') else '')
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
    if step.get('sections'):
        return '\n\n'.join(library_reply(section) for section in step['sections'])
    pages = step['pages']
    items = [i for page in pages for i in page['items']]
    name = step.get('section_name') or {'continue': '继续收听', 'favorites': '收藏', 'history': '收听历史', 'downloads': '下载记录'}[pages[0]['tab']]
    if step.get('completion'):
        name+='中'+('已听完' if step['completion']=='completed' else '未听完')+'的分集'
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
