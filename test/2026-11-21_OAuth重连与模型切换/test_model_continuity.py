from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
import unittest
from unittest.mock import patch

from modules.chat_mode.configuration import configure_conversation
from modules.chat_mode.storage import ChatStorage
from modules.personalization_manager import sanitize_personalization_payload
from utils.api_client.codex import oauth
from utils.api_client.codex.accounts import CodexAccountStore
from utils.responses_reasoning import same_responses_provider


RESPONSES = 'provider/responses'
CHAT = 'provider/chat'


class ModelContinuityTests(unittest.TestCase):
    def test_chat_switches_both_ways_with_history_and_missing_previous_model(self):
        with TemporaryDirectory() as directory:
            storage = ChatStorage(Path(directory))
            conversation_id = storage.create(model_key=RESPONSES)
            storage.begin(conversation_id, 'send-once', message={
                'role': 'user', 'content': 'Previous turn', 'message_id': 'msg_previous',
            })
            profiles = {CHAT: {'api_protocol': 'chat_completions'}}
            with patch('modules.chat_mode.configuration.get_model_profile', side_effect=profiles.__getitem__):
                configure_conversation(storage, conversation_id, {'model_key': CHAT})
                profiles[RESPONSES] = {'api_protocol': 'responses'}
                configure_conversation(storage, conversation_id, {'model_key': RESPONSES})
                configure_conversation(storage, conversation_id, {'model_key': CHAT})
            document = storage.read(conversation_id)
            self.assertEqual(document['metadata']['model_key'], CHAT)
            self.assertEqual(document['messages'][0]['content'], 'Previous turn')

    def test_temporarily_missing_defaults_survive_other_preference_saves(self):
        previous = {'default_model': RESPONSES, 'chat_default_model': RESPONSES}
        with patch('modules.personalization_manager.get_registered_model_keys', return_value=[CHAT]):
            for payload in ({'theme': 'dark'}, {**previous, 'theme': 'dark'}):
                result = sanitize_personalization_payload(payload, fallback=previous)
                self.assertEqual(result['default_model'], RESPONSES)
                self.assertEqual(result['chat_default_model'], RESPONSES)

    def test_encrypted_history_stays_on_its_responses_connection(self):
        profiles = {
            RESPONSES: {'provider_id': 'provider', 'api_protocol': 'responses'},
            CHAT: {'provider_id': 'provider', 'api_protocol': 'chat_completions'},
        }
        with patch('config.model_profiles.get_registered_model_profiles', return_value=profiles):
            self.assertTrue(same_responses_provider(RESPONSES, RESPONSES))
            self.assertFalse(same_responses_provider(RESPONSES, CHAT))


class OAuthContinuityTests(unittest.TestCase):
    def test_reconnect_reuses_original_key_and_rejects_another_identity(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = CodexAccountStore(
                legacy_auth_path=str(root / 'legacy.json'),
                accounts_dir=str(root / 'accounts'),
                index_path=str(root / 'index.json'),
            )
            account = store.upsert_tokens(access_token='old', refresh_token='old-refresh', account_id='a')
            other = store.upsert_tokens(access_token='other', refresh_token='other-refresh', account_id='b')
            old_files = {path: path.read_bytes() for path in (root / 'accounts').glob('*.json')}
            with self.assertRaisesRegex(ValueError, 'reconnect_account_mismatch'):
                store.upsert_tokens(access_token='wrong', refresh_token='wrong-refresh',
                                    account_id='b', reconnect_id=account['id'])
            self.assertEqual({path: path.read_bytes() for path in old_files}, old_files)
            result = store.upsert_tokens(access_token='new', refresh_token='new-refresh',
                                        account_id='a', reconnect_id=account['id'])
            self.assertEqual(result['id'], account['id'])
            self.assertEqual(result['hash'], account['hash'])
            self.assertEqual({record['id'] for record in store.list_accounts()}, {account['id'], other['id']})

    def test_login_waits_for_one_catalog_refresh_in_both_authorization_flows(self):
        for flow_type in (oauth.CodexOAuthFlow, oauth.CodexDeviceFlow):
            with self.subTest(flow=flow_type.__name__):
                entered, release = Event(), Event()
                connection_id = 'openai-codex-abcdef12'
                flow = flow_type(object(), reconnect_id=connection_id)

                def exchange(_store, **kwargs):
                    self.assertEqual(kwargs['reconnect_id'], connection_id)
                    record = kwargs['commit'](lambda: {'id': connection_id, 'hash': 'abcdef'})
                    return None, 'synthetic-account', record

                def refresh(connection):
                    self.assertEqual(connection, connection_id)
                    entered.set()
                    return release.wait(2)

                with patch.object(oauth, '_exchange_code_for_tokens_with_retry', side_effect=exchange), \
                        patch.object(oauth, '_refresh_authorized_models', side_effect=refresh) as refresh_call:
                    if isinstance(flow, oauth.CodexOAuthFlow):
                        flow._code = 'synthetic-code'
                        worker = Thread(target=flow._exchange, daemon=True)
                    else:
                        worker = Thread(target=flow._exchange, args=('synthetic-code', 'verifier'), daemon=True)
                    worker.start()
                    try:
                        self.assertTrue(entered.wait(2))
                        flow.cancel()
                        self.assertEqual(flow.poll()['status'], 'pending')
                        self.assertEqual(flow.poll()['phase'], 'refreshing_models')
                    finally:
                        release.set()
                        worker.join(2)
                    self.assertEqual(flow.poll()['status'], 'completed')
                    self.assertFalse(flow.poll()['models_refresh_failed'])
                    refresh_call.assert_called_once_with(connection_id)
