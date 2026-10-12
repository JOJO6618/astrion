"""Workspace skill preparation, versioned and serialized per destination."""
from __future__ import annotations

from pathlib import Path
import shutil

from config import WORKSPACE_SKILLS_DIRNAME
from modules.i18n import tr
from modules.skills_revision import tree_revision
from utils.logger import setup_logger

logger = setup_logger(__name__)
_VERSIONS: dict[str, tuple] = {}


def sync_workspace_skills(project_path, enabled_skills=None, base_dir=None, private_dir=None):
    # Catalog owns discovery; this module owns only preparation and its revisions.
    from modules.skills_manager import (
        _get_sync_lock, _get_skill_source_map, ensure_agent_skills_dir,
        get_skills_catalog, resolve_enabled_skills,
    )
    root = Path(project_path).expanduser().resolve()
    skills_dir = (root / WORKSPACE_SKILLS_DIRNAME).resolve()
    try:
        skills_dir.relative_to(root)
    except ValueError:
        return {'success': False, 'error': tr('skills.skills_dir_not_in_project')}
    key = str(skills_dir)
    try:
        with _get_sync_lock(skills_dir):
            ensure_agent_skills_dir(base_dir)
            catalog = get_skills_catalog(base_dir, private_dir=private_dir)
            resolved = resolve_enabled_skills(enabled_skills, catalog)
            sources = _get_skill_source_map(base_dir, private_dir=private_dir)
            revision = tuple((item, str(sources[item]), tree_revision(sources[item]))
                             for item in resolved if item in sources)
            result = {'success': True, 'copied': list(resolved),
                      'available': [item.get('id') for item in catalog], 'target': key}
            if skills_dir.is_dir() and _VERSIONS.get(key) == (revision, tree_revision(skills_dir)):
                return {**result, 'cached': True}
            _VERSIONS.pop(key, None)
            if skills_dir.exists():
                shutil.rmtree(skills_dir)
            skills_dir.mkdir(parents=True, exist_ok=True)
            for item in resolved:
                source = sources.get(item)
                if source is not None and source.is_dir():
                    shutil.copytree(source, skills_dir / item, dirs_exist_ok=True)
            current = tuple((item, str(sources[item]), tree_revision(sources[item]))
                            for item in resolved if item in sources)
            if current == revision:
                _VERSIONS[key] = (revision, tree_revision(skills_dir))
            return {**result, 'cached': False}
    except Exception as exc:
        logger.error('同步 skills 失败: %s', exc, exc_info=True)
        return {'success': False, 'error': str(exc), 'target': key}
