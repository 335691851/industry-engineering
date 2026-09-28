"""Exercise built Next.js dispatch -> durable requeue -> authenticated backend stub.

No cloud credentials, no real model calls. Requires apps/platform npm build.
"""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import httpx

ROOT=Path(__file__).resolve().parents[1]

def main():
    calls=[];secret='hobby-integration-test-'+'x'*32
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_POST(self):
            self.rfile.read(int(self.headers.get('content-length',0)))
            assert self.headers.get('Authorization')=='Bearer '+secret
            assert self.headers.get('X-Engineering-Owner')=='aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa'
            calls.append(self.path)
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
            self.wfile.write(json.dumps({'state':'queued' if len(calls)==1 else 'completed'}).encode())
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    # Reserve a free port before launching Next.
    import socket
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    base=f'http://localhost:{port}'
    with tempfile.TemporaryDirectory(prefix='workflow-smoke-') as folder:
        env={**os.environ,'ENGINEERING_SERVICE_TOKEN':secret,
             'ENGINEERING_BACKEND_URL':f'http://127.0.0.1:{server.server_port}',
             'WORKFLOW_LOCAL_BASE_URL':base,'WORKFLOW_TARGET_WORLD':'local',
             'WORKFLOW_LOCAL_DATA_DIR':str(Path(folder)/'workflow')}
        app=ROOT/'apps/platform'
        with (Path(folder)/'next.log').open('wb') as log:
            child=subprocess.Popen([shutil.which('node'),str(app/'node_modules/next/dist/bin/next'),'start','-p',str(port)],
                cwd=app,env=env,stdout=log,stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            try:
                deadline=time.monotonic()+60
                with httpx.Client(timeout=5,trust_env=False) as client:
                    while time.monotonic()<deadline:
                        try:
                            if client.get(base).status_code in (200,307): break
                        except httpx.HTTPError: pass
                        time.sleep(.2)
                    assert client.post(base+'/api/cloud/dispatch',json={}).status_code==401
                    response=client.post(base+'/api/cloud/dispatch',headers={'Authorization':'Bearer '+secret},
                        json={'taskId':'abcdef123456','ownerId':'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa'})
                    assert response.status_code==202,response.text
                    while len(calls)<2 and time.monotonic()<deadline: time.sleep(.2)
                assert len(calls)==2,'Workflow did not resume a queued slice'
                print('PASS: built Next dispatch -> authenticated step -> queued -> resumed step -> completed')
            finally:
                child.terminate();child.wait(timeout=15);server.shutdown();server.server_close()

if __name__=='__main__': main()
