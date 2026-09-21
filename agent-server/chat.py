"""A local text client. Reads the gateway token, never the provider API key."""
import argparse
import json
import sys
import time
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]
ERROR_MESSAGES = {
    'upstream_rate_limit': '模型服务繁忙或调用受限，请稍后重试。',
    'upstream_account_rate_limit': '上游账户请求速率受限。',
    'upstream_overloaded': '上游模型当前繁忙。',
    'upstream_account_arrears': '上游账户状态受限（欠费），请核查控制台；不会自动重试或切换模型。',
    'upstream_quota_exhausted': '上游使用额度已达上限，请在控制台核查额度与重置时间。',
    'upstream_account_restricted': '上游账户或套餐权限受限，请核查控制台。',
    'upstream_rate_limit_unknown': '上游返回 429，但原因无法确认；已停止自动重试。',
    'local_rate_limit': '发送较频繁，请约一分钟后重试。',
    'local_busy': '上一条请求还在处理中，请稍后重试。',
    'upstream_timeout': '模型响应超时，请稍后重试。',
    'upstream_auth_failed': '模型认证失败，请检查本地配置。',
    'unauthorized': '本地访问令牌无效，请重新启动客户端。',
    'invalid_request': '消息过长或格式不正确，请缩短内容或清空对话。',
}

RETRY_CODES = {'upstream_account_rate_limit', 'upstream_overloaded', 'local_rate_limit'}
MAX_ATTEMPTS = 3
MAX_WAIT_SECONDS = 120
RETRY_WINDOW_SECONDS = 180


def stream_reply(client, context, sleep=time.sleep, clock=time.monotonic):
    """Retry only explicit temporary refusals, before receiving any SSE body."""
    started, waited = clock(), 0
    for attempt in range(MAX_ATTEMPTS):
        delay = None
        remaining = RETRY_WINDOW_SECONDS - (clock() - started)
        if remaining <= 0:
            print('本条消息已达到重试时限，请稍后再试。')
            return '', False
        with client.stream('POST', '/v1/chat/completions',
                           json={'messages': context, 'stream': True}, timeout=min(100, remaining)) as response:
            if response.status_code != 200:
                data = json.loads(response.read())
                failure = data.get('error', {})
                code = failure.get('code', '')
                print(ERROR_MESSAGES.get(code, '请求未完成，请稍后重试。'))
                delay = failure.get('retry_after')
                can_retry = (response.status_code == 429 and code in RETRY_CODES
                             and failure.get('retryable') is True and type(delay) is int and delay > 0)
                if not can_retry:
                    return '', False
                if (attempt + 1 >= MAX_ATTEMPTS or waited + delay > MAX_WAIT_SECONDS
                        or clock() - started + delay >= RETRY_WINDOW_SECONDS):
                    print(f'已停止自动重试，请至少 {delay} 秒后再试（Ctrl+C 可退出）。')
                    return '', False
                source = failure.get('retry_after_source')
                label = {'upstream': '上游提示', 'upstream_and_local_backoff': '上游提示及本地退避',
                         'local_backoff': '本地退避', 'local_gate': '本地限速'}.get(source, '服务端提示')
                print(f'{label}：{delay} 秒后重试（{attempt + 1}/{MAX_ATTEMPTS - 1}，Ctrl+C 取消）。')
            else:
                reply = ''
                print('助手：', end='', flush=True)
                for line in response.iter_lines():
                    if not line.startswith('data:'):
                        continue
                    data = line[5:].strip()
                    if data == '[DONE]':
                        print()
                        return reply, bool(reply)
                    event = json.loads(data)
                    if 'error' in event:
                        print('\n响应中断，请重试。')
                        return reply, False
                    for choice in event.get('choices', []):
                        text = choice.get('delta', {}).get('content') or ''
                        reply += text
                        print(text, end='', flush=True)
                print('\n响应未完整结束，请重试。')
                return reply, False
        # Release the response/connection before waiting. KeyboardInterrupt is
        # intentionally propagated to main; an interrupted stream is never replayed.
        sleep(delay)
        waited += delay
    return '', False


def main():
    parser = argparse.ArgumentParser(description='Chat with the local Maoer model gateway')
    parser.add_argument('--message', help='Send a single message instead of interactive chat')
    parser.add_argument('--port', type=int, default=8787)
    args = parser.parse_args()
    try:
        token = (ROOT / '.local/agent-server.token').read_text(encoding='utf-8').strip()
    except OSError:
        print('请先启动本地服务端。')
        return 1
    messages = [{'role': 'system', 'content': '你是猫耳播客应用的文字助手。当前是本地模型连通性体验，尚未连接节目目录、收听历史或播放器。可以进行文字交流，但不得声称已经查询目录、操作播放或设置定时。简洁地用中文回答。'}]
    with httpx.Client(base_url=f'http://127.0.0.1:{args.port}', timeout=100, trust_env=False,
                      headers={'Authorization': 'Bearer ' + token}) as client:
        while True:
            try:
                question = args.message if args.message is not None else input('\n你（/exit 退出，/clear 清空）：').strip()
                if question == '/exit':
                    return 0
                if question == '/clear':
                    messages = messages[:1]
                    continue
                if not question:
                    continue
                messages.append({'role': 'user', 'content': question})
                context = messages[:1] + messages[1:][-37:]
                reply, complete = stream_reply(client, context)
                if complete and reply:
                    messages.append({'role': 'assistant', 'content': reply})
                else:
                    messages.pop()
                if args.message is not None:
                    return 0 if complete else 1
            except (KeyboardInterrupt, EOFError):
                return 0
            except (httpx.HTTPError, ValueError):
                print('\n服务暂不可用，请检查服务端是否启动。')
                return 1


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
