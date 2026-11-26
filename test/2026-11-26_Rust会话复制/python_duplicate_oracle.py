"""Run Python's existing Agent duplication in isolated account storage."""
from __future__ import annotations

from contextlib import redirect_stdout
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main() -> None:
    from modules.i18n import set_locale
    from utils.context_manager.base import ContextManagerBase
    from utils.context_manager.conversation_mixin import ConversationMixin
    from utils.conversation_manager import ConversationManager

    payload = json.load(sys.stdin)
    with redirect_stdout(sys.stderr), TemporaryDirectory(dir=payload['temporary_root']) as temporary:
        data = Path(temporary)
        project = Path(payload['workspace']).resolve()
        prefs = payload['personalization']
        (data / 'personalization.json').write_text(json.dumps(prefs), encoding='utf-8')
        set_locale(prefs['ui_locale'])
        manager = ConversationManager(base_dir=data, project_path=project)
        context = SimpleNamespace(
            conversation_manager=manager, initial_project_path=project,
            workspace_root=Path(sys.modules[ContextManagerBase.__module__].__file__).resolve().parents[1],
        )
        context._resolve_project_path_from_metadata = lambda metadata: (
            ContextManagerBase._resolve_project_path_from_metadata(context, metadata)
        )
        source_file = manager._get_conversation_file_path(payload['id'])
        source_file.write_text(json.dumps(payload['source'], ensure_ascii=False), encoding='utf-8')
        result = ConversationMixin.duplicate_conversation(context, payload['id'])
        assert result['success'], result
        duplicate_id = result['duplicate_conversation_id']
        copied = json.loads(manager._get_conversation_file_path(duplicate_id).read_bytes())
        index = json.loads(manager.index_file.read_bytes())[duplicate_id]
    json.dump({'document': copied, 'index': index}, sys.stdout, ensure_ascii=False)


if __name__ == '__main__':
    main()
