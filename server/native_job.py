"""Killable native subprocess with tenant-scoped scratch files."""
import json
import sys
from pathlib import Path
from .cloud_context import owner_id,workspace

if __name__=='__main__':
    user,directory,source,result=sys.argv[1:]
    owner_id.set(user);workspace.set(directory)
    from .native_entry import perform
    Path(result).write_text(json.dumps(perform(json.loads(Path(source).read_text(encoding='utf-8')))),encoding='utf-8')
