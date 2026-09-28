"""Stateless engineering checks. Only callable by the trusted Worker gateway.

Build with scripts/package_cloud_compute.py to include the exact local rules.
No model calls, filesystem persistence, background threads, or native CAD here.
"""
import hmac
import json
import math
import os
from http.server import BaseHTTPRequestHandler

from engineering_core.engineering_model import build_model

MAX_BODY = 200_000


def validate_payload(payload):
    if not isinstance(payload, dict) or set(payload) - {'geometry', 'part'}:
        raise ValueError('请求应包含 geometry 和可选 part')
    geometry = payload.get('geometry')
    part = payload.get('part', {})
    if not isinstance(geometry, dict) or not isinstance(part, dict):
        raise ValueError('geometry / part 必须是对象')
    if geometry.get('cad_document'):
        raise ValueError('云端校核不接受本机 CAD 文件路径作为几何校核依据')

    def walk(value, depth=0):
        if depth > 16:
            raise ValueError('工程数据嵌套层数超限')
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError('工程数值必须有限')
        if isinstance(value, (dict, list)):
            if len(value) > 500:
                raise ValueError('单层工程要素数量超限')
            for item in value.values() if isinstance(value, dict) else value:
                walk(item, depth + 1)
    walk(payload)
    # Rules may reject malformed nested data. Return a client error, never a partial report.
    try:
        return build_model(geometry, {'id': str(part.get('id', ''))[:120]})
    except (TypeError, AttributeError, KeyError, ValueError, IndexError) as exc:
        raise ValueError('工程数据结构无效，请检查尺寸、分段、来源及制造参数') from exc


class handler(BaseHTTPRequestHandler):
    def reply(self, status, value):
        body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        secret = os.getenv('ENGINEERING_COMPUTE_TOKEN', '')
        if len(secret) < 32:
            return self.reply(503, {'error': 'compute_not_configured'})
        supplied = self.headers.get('Authorization', '')
        if not hmac.compare_digest(supplied.encode(), f'Bearer {secret}'.encode()):
            return self.reply(401, {'error': 'unauthorized'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            return self.reply(400, {'error': 'invalid_content_length'})
        if not 0 < length <= MAX_BODY:
            return self.reply(413, {'error': 'request_too_large_or_empty'})
        if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
            return self.reply(415, {'error': 'json_required'})
        try:
            payload = json.loads(self.rfile.read(length))
            report = validate_payload(payload)
        except (ValueError, UnicodeDecodeError, RecursionError) as exc:
            return self.reply(422, {'error': 'invalid_engineering_data', 'message': str(exc)[:160]})
        return self.reply(200, report)

    def do_GET(self):
        return self.reply(405, {'error': 'method_not_allowed'})

    def log_message(self, *_args):
        # Do not log customer geometry or Authorization headers.
        pass
