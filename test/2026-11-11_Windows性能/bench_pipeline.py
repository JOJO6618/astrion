"""Profile real startup/request preparation on disposable desktop-history copies.

No model calls, no writes to live data, no message contents in the report.
"""
import argparse
import cProfile
import contextlib
import io
import json
import os
from pathlib import Path
import pstats
import socket
import sys
import tempfile
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--size', choices=['empty', 'long'], default='long')
    parser.add_argument('--profile', action='store_true')
    args = parser.parse_args()
    sys.path.insert(0, str(args.repo))
    report = []
    def stage(label, fn):
        started = time.perf_counter()
        profiler = cProfile.Profile()
        if args.profile:
            profiler.enable()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = fn()
        if args.profile:
            profiler.disable()
        output = io.StringIO()
        if args.profile:
            pstats.Stats(profiler, stream=output).strip_dirs().sort_stats('cumulative').print_stats(25)
        entry = {'stage':label,'seconds':round(time.perf_counter()-started,4),'profile':output.getvalue()}
        report.append(entry)
        print(json.dumps(entry,ensure_ascii=False),flush=True)
        return result

    # Refuse outbound traffic even if an optional initializer attempts a refresh.
    def deny_network(*_args, **_kwargs):
        raise OSError('Network disabled in isolated benchmark')
    socket.create_connection = deny_network
    socket.socket.connect = deny_network
    with tempfile.TemporaryDirectory(prefix='astrion-pipeline-') as tmp:
        root = Path(tmp)
        project = root/'project'
        project.mkdir()
        data = root/'data'
        os.environ.update({'ASTRION_IGNORE_DOTENV':'1','TERMINAL_SANDBOX_MODE':'host',
                           'ASTRION_DATA_ROOT':str(root),'DATA_DIR':str(data),
                           'DEPLOY_CONFIG_DIR':str(root/'config'),'LOGS_DIR':str(root/'logs'),
                           'WEB_API_SILENT':'1'})
        def imports():
            from core.web_terminal import WebTerminal
            return WebTerminal
        Terminal = stage('imports',imports)
        terminal = stage('terminal_init',lambda:Terminal(str(project),data_dir=str(data)))
        cm = terminal.context_manager
        if args.size == 'long':
            source_dir = Path.home()/'.astrion'/'astrion-desktop'/'host'/'data'/'conversations'
            source = max(source_dir.glob('**/conv_*.json'),key=lambda p:p.stat().st_size)
            payload = json.loads(source.read_text(encoding='utf-8'))
            payload['metadata']['project_path'] = str(project)
            payload['metadata']['project_relative_path'] = None
            payload['metadata']['model_key'] = None
            payload['metadata']['execution_mode'] = 'direct'
            cm.conversation_manager._save_conversation_file(payload['id'],payload)
            cm.conversation_manager._update_index(payload['id'],payload)
            terminal._bound_conversation_id = payload['id']
            cm._service_instance_no_history = False
            stage('load_conversation',lambda:terminal.load_conversation(payload['id'],restore_model=False))
        else:
            stage('create_conversation',terminal.create_new_conversation)
            terminal._bound_conversation_id = cm.current_conversation_id
            cm._service_instance_no_history = False
        stage('add_user',lambda:cm.add_conversation('user','Performance probe'))
        stage('get_context_tokens',cm.get_current_context_tokens)
        context = stage('build_context',terminal.build_context)
        stage('build_messages',lambda:terminal.build_messages(context,'Performance probe'))
        stage('build_messages_second',lambda:terminal.build_messages(context,'Performance probe'))
        stage('define_tools',terminal.define_tools)
        stage('final_save',lambda:cm.auto_save_conversation(force=True))
    out = args.repo/'test'/'2026-11-11_Windows性能'/f'pipeline_{args.size}.json'
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')


if __name__=='__main__':
    main()
