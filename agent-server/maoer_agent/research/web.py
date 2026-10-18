"""Tavily transport, persistent free-credit budget and usage telemetry."""
import asyncio
import logging
import math
import time
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
import httpx
from maoer_agent.core.errors import error_message
from maoer_agent.research.store import digest


class WebResearch:
    def __init__(self, settings, client, store):
        self.settings, self.client, self.store = settings, client, store
        self.web_lock = asyncio.Lock()
        self.usage_task = None

    def web_failure(self, code, note, phase, status=0, retry_after=0):
        # Never log provider bodies, headers, query text or credential-bearing URLs.
        logging.getLogger('maoer.agent').warning('web_failure code=%s phase=%s status=%s', code, phase, status)
        return {'status': 'error', 'code': code, 'phase': phase, 'retry_after': retry_after, 'note': note}

    def http_failure(self, response, phase, cache_key):
        status = response.status_code
        if status == 429:
            delay = 60
            raw = response.headers.get('retry-after', '')
            try:
                delay = max(1, math.ceil(float(raw)))
            except (ValueError, OverflowError):
                try: delay = max(1, math.ceil(parsedate_to_datetime(raw).timestamp() - time.time()))
                except (ValueError, TypeError, OverflowError): pass
            self.store.save_state(cache_key + ':cooldown', {'until': time.time() + delay, 'phase': phase})
            return self.web_failure('web_rate_limited', error_message('web_rate_limited', seconds=delay), phase, status, delay)
        if status in (401, 403):
            return self.web_failure('web_auth_failed', error_message('web_auth_failed'), phase, status)
        if status in (432, 433):
            self.store.save_state(cache_key + ':limit', {'month': datetime.now(timezone.utc).strftime('%Y-%m'), 'status': status})
            return self.web_failure('web_quota_exhausted', error_message('web_quota_exhausted'), phase, status)
        return self.web_failure('web_service_error', error_message('web_service_error', status=status), phase, status)

    def monitor_usage(self, cache_key, headers):
        """Account telemetry is advisory; its availability never gates a search."""
        if self.usage_task and not self.usage_task.done(): return
        now = time.time()
        if self.store.state(cache_key + ':monitor').get('next_check', 0) > now: return
        # Persist before dispatch, so a restart cannot hammer the usage endpoint.
        self.store.save_state(cache_key + ':monitor', {'next_check': now + 600})
        month = datetime.now(timezone.utc).strftime('%Y-%m')
        reserved = self.store.credits(month)
        self.usage_task = asyncio.create_task(self.refresh_usage(cache_key, headers, month, reserved))

    async def refresh_usage(self, cache_key, headers, month, reserved):
        try:
            response = await self.client.get('https://api.tavily.com/usage', headers=headers, timeout=5)
            if not response.is_success:
                logging.getLogger('maoer.agent').info('web_usage_unavailable status=%s', response.status_code)
                return  # In particular, usage 429 must not set the search cooldown.
            data = response.json()
            account = data['account']
            plan = str(account.get('current_plan', '')).lower()
            if not plan or any(type(account.get(k)) not in (int, float) for k in ('plan_usage', 'plan_limit', 'paygo_usage')):
                return
            paid = plan not in ('researcher', 'free') or account.get('paygo_limit') not in (None, 0) or account['paygo_usage'] != 0
            self.store.save_state(cache_key + ':policy', {'paid': paid, 'checked_at': time.time()})
            if paid: return
            remaining = min(1000, account['plan_limit']) - account['plan_usage']
            key_usage = data.get('key', {})
            if type(key_usage.get('limit')) in (int, float) and type(key_usage.get('usage')) in (int, float):
                remaining = min(remaining, key_usage['limit'] - key_usage['usage'])
            # Requests can finish while telemetry is in flight. Subtract every
            # reservation since dispatch, conservatively including overlaps.
            if month != datetime.now(timezone.utc).strftime('%Y-%m'): return
            self.store.save_state(cache_key + ':quota', {'verified_at': time.time(), 'month': month,
                'remaining': remaining, 'reserved': reserved})
            if remaining >= 1: self.store.save_state(cache_key + ':limit', {})
        except (httpx.RequestError, ValueError, TypeError, KeyError):
            logging.getLogger('maoer.agent').info('web_usage_unavailable')
        except Exception as exc:
            logging.getLogger('maoer.agent').warning('web_usage_failure type=%s', type(exc).__name__)

    async def tavily(self, endpoint, payload, request_id):
        if not self.settings.tavily_key:
            return {'status': 'error', 'code': 'web_not_configured', 'note': error_message('web_not_configured')}
        # Only these fixed-cost basic operations are permitted, regardless of caller.
        payload = dict(payload)
        if endpoint == 'search':
            payload.update(search_depth='basic', auto_parameters=False, include_usage=True)
        elif endpoint == 'extract' and len(payload.get('urls', [])) == 1:
            payload['extract_depth'] = 'basic'
        else:
            return {'status': 'error', 'code': 'web_operation_not_allowed', 'note': error_message('web_operation_not_allowed')}
        headers = {'Authorization': 'Bearer ' + self.settings.tavily_key}
        cache_key = 'web:' + digest(self.settings.tavily_key)
        async with self.web_lock:
            try:
                self.monitor_usage(cache_key, headers)
                now = time.time()
                cooldown = self.store.state(cache_key + ':cooldown')
                # Migrate old usage-induced cooldowns without weakening real search limits.
                if cooldown.get('phase') != 'usage' and cooldown.get('until', 0) > now:
                    delay = math.ceil(cooldown['until'] - now)
                    return {'status': 'error', 'code': 'web_rate_limited', 'phase': cooldown.get('phase', endpoint),
                            'retry_after': delay, 'note': error_message('web_rate_limited', seconds=delay)}
                month = datetime.now(timezone.utc).strftime('%Y-%m')
                if self.store.state(cache_key + ':policy').get('paid'):
                    return self.web_failure('web_paid_plan_blocked', error_message('web_paid_plan_blocked'), endpoint)
                if self.store.state(cache_key + ':limit').get('month') == month:
                    return self.web_failure('web_quota_exhausted', error_message('web_quota_exhausted'), endpoint)
                quota = self.store.state(cache_key + ':quota')
                if quota.get('month') == month and quota['remaining'] - max(0, self.store.credits(month) - quota['reserved']) < 1:
                    return self.web_failure('web_quota_exhausted', error_message('web_quota_exhausted'), endpoint)
                # No usage response is required: the configured free account supplies
                # the provider-side hard cap; this independent ledger is conservative.
                if not self.store.reserve(request_id, month, min(950, max(0, self.settings.web_monthly_limit))):
                    code = 'web_request_recorded' if self.store.reserved(request_id) else 'web_budget_exhausted'
                    return self.web_failure(code, error_message(code), endpoint)
                response = await self.client.post('https://api.tavily.com/' + endpoint, headers=headers, json=payload, timeout=35)
                if not response.is_success: return self.http_failure(response, endpoint, cache_key)
                data = response.json()
                if not isinstance(data, dict) or not isinstance(data.get('results'), list):
                    raise ValueError('invalid_web_results')
                fields = ('url', 'title', 'content') if endpoint == 'search' else ('raw_content',)
                if any(not isinstance(row, dict) or any(not isinstance(row.get(key, ''), str) for key in fields)
                       or not isinstance(row.get('published_date') or '', str) for row in data['results']):
                    raise ValueError('invalid_web_result_fields')
                return data
            except httpx.TimeoutException as exc:
                code = ('web_timeout_connect' if isinstance(exc, httpx.ConnectTimeout) else
                        'web_timeout_write' if isinstance(exc, httpx.WriteTimeout) else
                        'web_timeout_pool' if isinstance(exc, httpx.PoolTimeout) else 'web_timeout_read')
                return self.web_failure(code, error_message(code), endpoint)
            except httpx.RequestError:
                return self.web_failure('web_connection_failed', error_message('web_connection_failed'), endpoint)
            except (ValueError, KeyError, TypeError):
                return self.web_failure('web_invalid_response', error_message('web_invalid_response'), endpoint)
            except Exception as exc:
                logging.getLogger('maoer.agent').warning('web_internal_failure type=%s', type(exc).__name__)
                return self.web_failure('web_internal_error', error_message('web_internal_error'), endpoint)

    async def close(self):
        if self.usage_task:
            self.usage_task.cancel()
            await asyncio.gather(self.usage_task, return_exceptions=True)
