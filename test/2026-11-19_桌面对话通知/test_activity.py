from types import SimpleNamespace
import unittest
from unittest.mock import patch

from flask import Flask

from modules.user_question_manager import UserQuestionManager
from server import state
from server import conversation_tabs
from server.desktop_conversation_activity import conversation_activity, pending_conversation_requests
from server.runtime import runtime_service
from server.tasks import task_manager


def record(task_id, workspace='agents', status='running', task_type='chat', created_at=1):
    return SimpleNamespace(
        task_id=task_id, workspace_id=workspace, conversation_id='conv_shared',
        status=status, task_type=task_type, created_at=created_at,
        principal=SimpleNamespace(conversation_kind='agent'),
    )


class ConversationActivityTests(unittest.TestCase):
    def test_closed_tab_waits_for_background_and_uses_latest_non_compression_task(self):
        tasks = [record('work', status='succeeded'),
                 record('compress', status='succeeded', task_type='compression', created_at=2)]
        with patch.object(task_manager, 'list_tasks', return_value=tasks), patch.object(
            runtime_service, 'list_pending_approvals', return_value={},
        ), patch('modules.host_workspace_manager.load_host_workspace_catalog', return_value={
            'workspaces': [{'workspace_id': 'agents', 'label': 'Agent project'}],
        }):
            def titles(_workspace):
                return {'conv_shared': 'Indexed title'}

            running = conversation_activity('host', lambda *_: True, titles)
            idle = conversation_activity('host', lambda *_: False, titles)
        self.assertEqual(len(running), 1)
        self.assertTrue(running[0]['running'])
        self.assertFalse(idle[0]['running'])
        self.assertEqual(idle[0]['run_id'], 'work')
        self.assertEqual(idle[0]['title'], 'Indexed title')
        self.assertEqual(idle[0]['workspace_label'], 'Agent project')

    def test_request_ids_are_scoped_to_live_task_and_question_batch(self):
        tool = lambda approval_id, **kwargs: {
            'approval_id': approval_id, 'task_id': 'task', 'conversation_id': 'conv_shared', **kwargs,
        }
        rows = {
            'tool': [tool('human', human_approval_required=True, workspace_root='/different/worktree'),
                     tool('automatic', auto_review_required=True),
                     tool('answered', human_approval_required=True, human_decision='approved'),
                     tool('wrong', task_id='stopped', human_approval_required=True)],
            'plan': [tool('plan')],
            'question': [dict(task_id='task', conversation_id='shared', batch_id='batch', question_id=f'q{i}')
                         for i in range(2)],
        }
        with patch.object(runtime_service, 'list_pending_approvals', return_value=rows):
            requests = pending_conversation_requests('host', [
                record('task'), record('other', workspace='other'), record('stopped', status='stopped'),
            ])
        self.assertEqual(requests, {
            ('agent', 'agents', 'conv_shared'): {'approval': {'human', 'plan'}, 'question': {'batch'}},
        })

    def test_real_question_illuminates_only_own_tab_and_answer_clears_it(self):
        questions = UserQuestionManager()
        created = questions.create_question(
            username='host', conversation_id='conv_shared', task_id='task',
            tool_call_id='call', question='Choose a plan',
        )
        tabs = [dict(kind='conv', workspace_id=ws, conversation_id='conv_shared') for ws in ('agents', 'other')]
        with patch.object(state, 'user_question_manager', questions), patch.object(
            task_manager, 'list_tasks', return_value=[record('task')],
        ), patch.object(conversation_tabs, '_active_run_conversation_ids', return_value=set()), patch.object(
            conversation_tabs, '_load_index_titles', return_value={},
        ):
            conversation_tabs._enrich_tabs('host', tabs)
            self.assertEqual([tab['awaiting_user_answer'] for tab in tabs], [True, False])
            self.assertFalse(tabs[0]['awaiting_human_approval'])
            questions.answer(question_id=created['question_id'], username='host', text='Continue')
            conversation_tabs._enrich_tabs('host', tabs)
            self.assertEqual([tab['awaiting_user_answer'] for tab in tabs], [False, False])

    def test_activity_http_route_requires_host_session(self):
        app = Flask(__name__)
        app.secret_key = 'isolated-test-key'
        app.register_blueprint(conversation_tabs.conversation_tabs_bp)
        client = app.test_client()
        url = '/api/conversation-tabs/activity'
        self.assertEqual(client.get(url).status_code, 401)
        with client.session_transaction() as session:
            session.update(username='host', role='admin', login_nonce='test-nonce')
        with patch.dict(state.active_login_nonces, {'host': {'test-nonce'}}), patch(
            'server.host_password.browser_session_valid', return_value=True,
        ), patch.object(conversation_tabs, 'IS_HOST_MODE', True), patch(
            'server.desktop_conversation_activity.conversation_activity', return_value=[{'running': True}],
        ) as query:
            self.assertEqual(client.get(url).json['data'], [])
            query.assert_not_called()
            with client.session_transaction() as session:
                session['host_mode'] = True
            self.assertEqual(client.get(url).json['data'], [{'running': True}])
            self.assertEqual(query.call_args.args[0], 'host')
