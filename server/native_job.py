"""Killable native subprocess with a machine-readable result envelope."""
import json
import sys
import traceback
from pathlib import Path
from .cloud_context import owner_id,workspace

if __name__=='__main__':
    user,directory,source,result=sys.argv[1:]
    owner_id.set(user);workspace.set(directory)
    try:
        from .native_entry import perform
        value = perform(json.loads(Path(source).read_text(encoding='utf-8')))
        envelope = {'ok': True, 'result': value}
    except Exception as exc:
        envelope = {'ok': False, 'error': {
            'type': type(exc).__name__, 'message': str(exc)[:500],
            'traceback': ''.join(traceback.format_exception(exc))[-6000:],
        }}
    Path(result).write_text(json.dumps(envelope, ensure_ascii=False, default=str), encoding='utf-8')
