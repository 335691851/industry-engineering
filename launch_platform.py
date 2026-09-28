"""Start the local platform independently of the invoking terminal."""
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parent
URL = 'http://127.0.0.1:8000/'


def ready():
    try:
        with urllib.request.urlopen(URL + 'openapi.json', timeout=2) as response:
            import json
            return json.load(response).get('info', {}).get('title') == '工程解析平台'
    except (OSError, ValueError):
        return False


def main():
    if ready():
        print('Platform already running: ' + URL)
        return 0
    if not (ROOT / 'dist' / 'index.html').is_file():
        print('Frontend build missing. Run: npm run build')
        return 1
    options = {'creationflags': subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP |
               subprocess.CREATE_BREAKAWAY_FROM_JOB} if sys.platform == 'win32' else {'start_new_session': True}
    with (ROOT / 'server.log').open('ab') as out, (ROOT / 'server-error.log').open('ab') as err:
        try:
            process = subprocess.Popen(
                [sys.executable, '-m', 'uvicorn', 'server.main:app', '--host', '127.0.0.1', '--port', '8000'],
                cwd=ROOT, stdin=subprocess.DEVNULL, stdout=out, stderr=err, close_fds=True, **options)
        except OSError as exc:
            print('Unable to start platform: ' + str(exc))
            return 1
    for _ in range(40):
        if process.poll() is not None:
            break
        if ready():
            print('Platform running: ' + URL)
            return 0
        time.sleep(.5)
    print('Startup failed. Check server-error.log in the project folder.')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
