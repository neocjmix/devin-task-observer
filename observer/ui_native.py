"""OS actions for an explicitly clicked, validated resource; never shell commands."""
from pathlib import Path
from urllib.parse import urlsplit


def checked_target(action):
    kind, target = action.get('kind'), action.get('target')
    if not isinstance(target, str) or not target or len(target) > 16000 or any(ord(c) < 32 or ord(c) == 127 for c in target):
        raise ValueError('잘못된 리소스 주소입니다.')
    if kind == 'copy':
        return kind, target
    if kind == 'url':
        parsed = urlsplit(target)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or any(c.isspace() for c in target):
            raise ValueError('HTTP 또는 HTTPS 주소만 열 수 있습니다.')
        return kind, target
    if kind == 'file':
        path = Path(target)
        if not path.is_absolute() or not path.is_file():
            raise ValueError('파일을 찾을 수 없습니다. 경로를 복사해 확인하세요.')
        # Do not launch scripts/apps through a document-opening affordance.
        if path.suffix.lower() not in ('.md', '.txt', '.json', '.yaml', '.yml', '.pdf', '.csv'):
            raise ValueError('이 파일 형식은 경로 복사로 이용하세요.')
        return kind, str(path)
    raise ValueError('지원하지 않는 열기 방식입니다.')


def perform(action):
    kind, target = checked_target(action)
    from AppKit import NSWorkspace, NSPasteboard, NSPasteboardTypeString
    from Foundation import NSURL
    if kind == 'copy':
        clipboard = NSPasteboard.generalPasteboard()
        clipboard.clearContents()
        if not clipboard.setString_forType_(target, NSPasteboardTypeString):
            raise ValueError('복사하지 못했습니다. 다시 시도하세요.')
        return {'copied': True}
    url = NSURL.fileURLWithPath_(target) if kind == 'file' else NSURL.URLWithString_(target)
    if url is None or not NSWorkspace.sharedWorkspace().openURL_(url):
        raise ValueError('링크를 열지 못했습니다. 주소를 복사해 이용하세요.')
    return {'opened': True}


def finish(result, performer=None):
    """Run on Cocoa thread and report the actual OS result, not just dispatch."""
    if not result.get('ok') or not isinstance(result.get('result'), dict) or 'native_action' not in result['result']:
        return result
    try:
        value = (performer or perform)(result['result']['native_action'])
        return {**result, 'result': value}
    except Exception as error:
        return {'request_id': result['request_id'], 'ok': False,
                'error': {'code': 'open_failed', 'message': str(error)}}
