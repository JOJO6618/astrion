"""审核智能体：固定思考、默认推理强度、统一 APIClient 通道与账本计费的离线回归。

不调用真实模型、不读取真实凭证或对话：APIClient 与价格捕获均为替身，账本写入临时目录。
运行：python3 -B -m unittest discover -s test/2026-11-06_审核智能体计费 -p 'test_*.py'
"""
import asyncio
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
_RUNTIME = tempfile.TemporaryDirectory(prefix='review-billing-')
RUNTIME = Path(_RUNTIME.name)
os.environ.update(ASTRION_IGNORE_DOTENV='1', ASTRION_DATA_ROOT=str(RUNTIME),
                  DATA_DIR=str(RUNTIME / 'data'), LOGS_DIR=str(RUNTIME / 'logs'),
                  DEPLOY_CONFIG_DIR=str(RUNTIME / 'deploy'), TERMINAL_SANDBOX_MODE='host',
                  USER_SPACE_DIR=str(RUNTIME / 'users'), API_USER_SPACE_DIR=str(RUNTIME / 'api'))

from modules import review_model_caller as caller  # noqa: E402
from modules.review_model_caller import ReviewBilling, ReviewModelCallError, call_review_model, review_billing_for  # noqa: E402
from modules.conversation_costs import summarize  # noqa: E402

PRICE = {'model_key': 'synthetic/review', 'provider_id': 'synthetic',
         'rates': {'input': 2, 'cache_read': 0.2, 'output': 8}, 'source': 'test', 'fetched_at': None}
USAGE = {'prompt_tokens': 1000, 'completion_tokens': 100, 'prompt_tokens_details': {'cached_tokens': 400}}
CHAT_PROFILE = {
    'name': 'synthetic/review', 'provider_id': 'synthetic', 'api_protocol': 'chat',
    'fast': {'base_url': 'https://example.invalid/v1', 'api_key': 'k', 'model_id': 'm-fast', 'extra_params': {}},
    'thinking': {'base_url': 'https://example.invalid/v1', 'api_key': 'k', 'model_id': 'm',
                 'extra_params': {'thinking': {'type': 'enabled'}}},
    'supports_thinking': True, 'supports_reasoning_effort': True,
}


class FakeClient:
    """记录构造参数与 apply_profile，按脚本吐出 chunk。"""
    instances = []
    script = []
    delay = 0.0

    def __init__(self, thinking_mode=False, web_mode=False):
        self.thinking_mode = thinking_mode
        self.reasoning_effort = None
        self.extra_headers_resolver = None
        self.profile = None
        self.closed = False
        FakeClient.instances.append(self)

    def apply_profile(self, profile):
        self.profile = profile

    async def chat(self, messages, tools=None, stream=True):
        try:
            for chunk in FakeClient.script:
                if FakeClient.delay:
                    await asyncio.sleep(FakeClient.delay)
                yield chunk
        finally:
            self.closed = True


def run(coro):
    return asyncio.run(coro)


class ReviewCallerTests(unittest.TestCase):
    def setUp(self):
        FakeClient.instances = []
        FakeClient.delay = 0.0
        FakeClient.script = [
            {'choices': [{'delta': {'reasoning_content': '想一想'}}]},
            {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'c1', 'function': {'name': 'approve_decision', 'arguments': '{"decision":'}}]}}]},
            {'choices': [{'delta': {'tool_calls': [{'index': 0, 'function': {'arguments': '"approved"}'}}]}, 'finish_reason': 'tool_calls'}]},
            {'choices': [], 'usage': USAGE},
        ]
        self.data_dir = Path(tempfile.mkdtemp(dir=RUNTIME))
        self.committed = []
        self.patches = [
            # 以替身模块替换 utils.api_client：不依赖 httpx 等网络库，也不可能发出真实请求
            patch.dict(sys.modules, {'utils.api_client': types.SimpleNamespace(APIClient=FakeClient)}),
            patch('modules.api_pricing.capture_price', lambda key, **_: dict(PRICE, model_key=key)),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def billing(self, conv='conv_1', key='auto_approval'):
        return ReviewBilling(str(self.data_dir), conv, key, lambda: self.committed.append(conv))

    def ledger(self, conv='conv_1'):
        return json.loads((self.data_dir / 'api_costs' / f'{conv}.json').read_text(encoding='utf-8'))

    def test_always_thinking_default_effort_and_aggregation(self):
        out = run(call_review_model({'profile': CHAT_PROFILE}, [], [], 30, billing=self.billing()))
        client = FakeClient.instances[0]
        self.assertTrue(client.thinking_mode)
        self.assertIsNone(client.reasoning_effort)
        self.assertIs(client.profile, CHAT_PROFILE)
        self.assertTrue(client.closed)
        self.assertEqual(out['reasoning_content'], '想一想')
        self.assertEqual(out['tool_calls'][0]['function'], {'name': 'approve_decision', 'arguments': '{"decision":"approved"}'})

    def test_priced_request_recorded_as_review_actor(self):
        run(call_review_model({'profile': CHAT_PROFILE}, [], [], 30, billing=self.billing()))
        data = self.ledger()
        actor = data['actors']['review_agent:auto_approval']
        self.assertEqual((actor['kind'], actor['name'], actor['requests'], actor['priced_requests']),
                         ('review_agent', 'auto_approval', 1, 1))
        self.assertEqual(Decimal(actor['cost_usd']), Decimal('0.00208'))
        req = next(iter(data['requests'].values()))
        self.assertEqual(req['price']['model_key'], 'synthetic/review')
        self.assertIn('duration_ms', req)
        self.assertEqual(self.committed, ['conv_1'])
        total = summarize(self.data_dir, 'conv_1')
        self.assertEqual(Decimal(total['total_usd']), Decimal('0.00208'))
        self.assertFalse(total['partial'])

    def test_each_round_new_request_and_agents_separate(self):
        run(call_review_model({'profile': CHAT_PROFILE}, [], [], 30, billing=self.billing()))
        run(call_review_model({'profile': CHAT_PROFILE}, [], [], 30, billing=self.billing()))
        run(call_review_model({'profile': CHAT_PROFILE}, [], [], 30, billing=self.billing(key='goal_review')))
        actors = self.ledger()['actors']
        self.assertEqual(actors['review_agent:auto_approval']['requests'], 2)
        self.assertEqual(actors['review_agent:goal_review']['requests'], 1)

    def test_missing_usage_is_unpriced_not_free(self):
        FakeClient.script = FakeClient.script[:-1]
        run(call_review_model({'profile': CHAT_PROFILE}, [], [], 30, billing=self.billing()))
        actor = self.ledger()['actors']['review_agent:auto_approval']
        self.assertEqual((actor['priced_requests'], actor['unpriced_requests']), (0, 1))
        self.assertTrue(summarize(self.data_dir, 'conv_1')['partial'])

    def test_error_before_output_raises_with_details_and_not_billed(self):
        FakeClient.script = [{'error': {'status_code': 429, 'error_type': 'rate_limit', 'error_message': 'slow down'}}]
        with self.assertRaises(ReviewModelCallError) as ctx:
            run(call_review_model({'profile': CHAT_PROFILE}, [], [], 30, billing=self.billing()))
        self.assertEqual(ctx.exception.details['status_code'], 429)
        self.assertFalse((self.data_dir / 'api_costs' / 'conv_1.json').exists())

    def test_timeout_applies_to_every_protocol(self):
        FakeClient.delay = 0.5
        profile = dict(CHAT_PROFILE, api_protocol='responses')
        with self.assertRaises(ReviewModelCallError) as ctx:
            run(call_review_model({'profile': profile}, [], [], 0.05))
        self.assertEqual(ctx.exception.details['error_type'], 'timeout')
        self.assertIn('timed out', str(ctx.exception))

    def test_incomplete_chat_profile_fails_before_request(self):
        profile = dict(CHAT_PROFILE, thinking=None, fast={'base_url': '', 'api_key': 'k', 'model_id': 'm'})
        with self.assertRaises(ReviewModelCallError) as ctx:
            run(call_review_model({'profile': profile}, [], [], 30))
        self.assertIn('profile incomplete', str(ctx.exception))
        self.assertEqual(FakeClient.instances, [])

    def test_responses_profile_without_static_key_allowed(self):
        profile = {'name': 'openai-codex/gpt', 'api_protocol': 'responses', 'responses_auth': 'codex_oauth',
                   'fast': {'model_id': 'gpt'}, 'thinking': None}
        out = run(call_review_model({'profile': profile}, [], [], 30))
        self.assertEqual(out['tool_calls'][0]['id'], 'c1')

    def test_empty_stream_is_error(self):
        FakeClient.script = []
        with self.assertRaises(ReviewModelCallError):
            run(call_review_model({'profile': CHAT_PROFILE}, [], [], 30, billing=self.billing()))


class BillingIdentityTests(unittest.TestCase):
    def terminal(self, current='conv_1'):
        calls = []

        class Ctx:
            data_dir = str(RUNTIME / 'ws')
            current_conversation_id = current

            def safe_broadcast_token_update(self_inner):
                calls.append(True)

        class Term:
            context_manager = Ctx()
        return Term(), calls

    def test_missing_or_temporary_conversation_not_billed(self):
        term, _ = self.terminal()
        for cid in (None, '', 'temp_123'):
            self.assertIsNone(review_billing_for(term, cid, 'auto_approval'))

    def test_refresh_only_for_displayed_conversation(self):
        term, calls = self.terminal(current='other')
        billing = review_billing_for(term, 'conv_1', 'goal_review')
        self.assertEqual((billing.conversation_id, billing.agent_key), ('conv_1', 'goal_review'))
        billing.on_committed()
        self.assertEqual(calls, [])
        term, calls = self.terminal(current='conv_1')
        review_billing_for(term, 'conv_1', 'goal_review').on_committed()
        self.assertEqual(calls, [True])


class ConfigTests(unittest.TestCase):
    def test_thinking_setting_removed(self):
        from modules.personalization_manager import DEFAULT_PERSONALIZATION_CONFIG, sanitize_personalization_payload
        for item in DEFAULT_PERSONALIZATION_CONFIG['review_agents'].values():
            self.assertNotIn('thinking', item)
        cleaned = sanitize_personalization_payload({'review_agents': {'auto_approval': {'thinking': True, 'model': 'x'}}})
        self.assertNotIn('thinking', cleaned['review_agents']['auto_approval'])
        self.assertEqual(cleaned['review_agents']['auto_approval']['model'], 'x')

    def test_resolved_config_has_no_thinking_or_extra_params(self):
        from modules.review_agent_config import resolve_review_agent_config
        cfg = resolve_review_agent_config('workflow_review')
        self.assertNotIn('thinking', cfg)
        self.assertNotIn('extra_params', cfg)
        self.assertEqual(cfg['agent_key'], 'workflow_review')

    def test_goal_recovery_still_classifies_timeout_as_retryable(self):
        try:
            from server.goal_runtime import classify_error
        except Exception as exc:  # 依赖缺失时跳过，不伪造通过
            self.skipTest(f'goal_runtime unavailable: {exc}')
        self.assertEqual(classify_error({'error_type': 'timeout', 'message': 'review model request timed out after 5s'}), 'retryable')
        self.assertEqual(classify_error({'message': 'model profile incomplete (url/key/model)'}), 'configuration')


if __name__ == '__main__':
    unittest.main()
