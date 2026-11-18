"""Pure release-policy evaluation shared by the public policy service."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from urllib.parse import urlsplit

OFFICIAL_DOWNLOAD_URL = 'https://astrion.cyjai.com/'
_VERSION = re.compile(r'^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$')


def compare_versions(left: str, right: str) -> int:
    """Compare SemVer including prereleases, without accepting partial versions."""
    a, b = _VERSION.fullmatch(left), _VERSION.fullmatch(right)
    if not a or not b:
        raise ValueError('invalid_version')
    for parsed in (a, b):
        if parsed.group(4) and any(not part or (part.isdigit() and len(part) > 1 and part.startswith('0'))
                                   for part in parsed.group(4).split('.')):
            raise ValueError('invalid_prerelease')
    release_a, release_b = tuple(map(int, a.group(1, 2, 3))), tuple(map(int, b.group(1, 2, 3)))
    if release_a != release_b:
        return (release_a > release_b) - (release_a < release_b)
    pre_a, pre_b = a.group(4), b.group(4)
    if pre_a == pre_b:
        return 0
    if pre_a is None or pre_b is None:
        return 1 if pre_a is None else -1
    parts_a, parts_b = pre_a.split('.'), pre_b.split('.')
    for x, y in zip(parts_a, parts_b):
        if not x or not y:
            raise ValueError('invalid_prerelease')
        if x == y:
            continue
        if x.isdigit() and y.isdigit():
            return (int(x) > int(y)) - (int(x) < int(y))
        if x.isdigit() != y.isdigit():
            return -1 if x.isdigit() else 1
        return (x > y) - (x < y)
    return (len(parts_a) > len(parts_b)) - (len(parts_a) < len(parts_b))


def official_download_url(value: str) -> str:
    url = urlsplit(value)
    if (url.scheme != 'https' or url.hostname != 'astrion.cyjai.com'
            or url.username or url.password or url.port not in (None, 443)):
        raise ValueError('download_url_must_be_official_https')
    return value


def _matches(rule: dict, client: dict) -> bool:
    for field, name in (('platforms', 'platform'), ('architectures', 'arch'),
                        ('versions', 'version'), ('build_ids', 'build_id')):
        if field in rule:
            values = rule[field]
            if not isinstance(values, list) or not values or not all(isinstance(x, str) for x in values):
                raise ValueError('invalid_' + field)
            if client[name] not in values:
                return False
    if 'min_version' in rule and compare_versions(client['version'], rule['min_version']) < 0:
        return False
    if 'max_version_exclusive' in rule and compare_versions(client['version'], rule['max_version_exclusive']) >= 0:
        return False
    return True


def evaluate_policy(document: dict, client: dict, latest_version: str,
                    now: datetime | None = None) -> dict:
    """First matching rule wins; a missing policy file is a service error, not allow."""
    now = now or datetime.now(timezone.utc)
    if not isinstance(document, dict) or document.get('schema') != 1 or not isinstance(document.get('rules'), list):
        raise ValueError('invalid_policy_document')
    if client.get('platform') not in ('win32', 'darwin') or client.get('arch') not in ('x64', 'arm64'):
        raise ValueError('unsupported_client')
    compare_versions(client['version'], client['version'])
    # macOS 0.10.4–0.10.9 的 electron-builder.yml 不会展开 extraMetadata 里的环境变量，
    # 包内 astrionBuildId 落成字面量 ${env.ASTRION_BUILD_ID}；这些已经发出去的客户端
    # 只有放行该字面量才能被策略覆盖，所以字符集额外允许 $ { }。
    if not re.fullmatch(r'[A-Za-z0-9._+${}-]{1,128}', client.get('build_id', '')):
        raise ValueError('invalid_build_id')
    ttl = document.get('ttl_seconds', 86400)
    if isinstance(ttl, bool) or not isinstance(ttl, int) or not 60 <= ttl <= 604800:
        raise ValueError('invalid_policy_ttl')
    chosen = None
    for rule in document['rules']:
        if not isinstance(rule, dict):
            raise ValueError('invalid_rule')
        if rule.get('enabled', True) is False:
            continue
        if _matches(rule, client):
            if rule.get('valid_until'):
                until = datetime.fromisoformat(str(rule['valid_until']).replace('Z', '+00:00'))
                if until.tzinfo is None:
                    raise ValueError('rule_expiry_needs_timezone')
                if until <= now:
                    continue
            chosen = rule
            break
    rule = chosen or {}
    required = rule.get('required', False)
    delivery = rule.get('delivery', 'auto')
    if not isinstance(required, bool) or delivery not in ('auto', 'manual'):
        raise ValueError('invalid_policy_action')
    target = str(rule.get('target_version') or latest_version or client['version'])
    newer = compare_versions(target, client['version']) > 0
    if required:
        if delivery == 'auto' and not newer:
            raise ValueError('required_auto_update_needs_newer_target_version')
        if not latest_version or compare_versions(latest_version, target) < 0:
            raise ValueError('required_target_not_published')
    identifier = str(rule.get('id') or ('default' if chosen is None else ''))
    if not identifier or len(identifier) > 128:
        raise ValueError('invalid_rule_id')
    expires = now + timedelta(seconds=ttl)
    if rule.get('valid_until'):
        until = datetime.fromisoformat(str(rule['valid_until']).replace('Z', '+00:00'))
        if until.tzinfo is None or until <= now:
            raise ValueError('expired_rule')
        expires = min(expires, until)
    return {
        'schema': 1, 'client': client, 'id': identifier,
        'required': required, 'delivery': delivery,
        'latest_version': target,
        'reason': str(rule.get('reason') or '')[:2000],
        'download_url': official_download_url(str(rule.get('download_url') or OFFICIAL_DOWNLOAD_URL)),
        'issued_at': now.isoformat(), 'expires_at': expires.isoformat(),
    }
