"""Standalone loopback-only analysis UI."""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import subprocess
import sys
import threading
import uuid

ROOT = Path(__file__).resolve().parent
JOBS = {}
LOCK = threading.Lock()
POOL = ThreadPoolExecutor(max_workers=1)


def analyze(identifier, source, model, calibration=None):
    job = JOBS[identifier]
    job['state'] = 'running'
    try:
        if calibration:
            source = [*source, '--calibration-json', str(calibration)]
        result = subprocess.run([sys.executable, str(ROOT / 'run_pipeline.py'), *source,
            '--model-path', str(model), '--output-dir', str(job['directory']),
            '--progress-file', str(job['directory'] / 'progress.json')],
            cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=600)
        (job['directory'] / 'execution.log').write_text(result.stderr, encoding='utf-8')
        if not (job['directory'] / 'report.json').is_file():
            raise RuntimeError('分析程式沒有產生報告，請稍後再試。')
        job['report'] = json.loads((job['directory'] / 'report.json').read_text(encoding='utf-8'))
        from report_explanation import annotate
        annotate(job['report'])
        job['state'] = 'done'
    except subprocess.TimeoutExpired:
        job.update(state='error', error='分析時間超過 10 分鐘，已停止。請稍後再試。')
    except Exception as exc:
        (job['directory'] / 'server_error.log').write_text(f'{type(exc).__name__}: {exc}', encoding='utf-8')
        job.update(state='error', error=str(exc) if isinstance(exc, RuntimeError) else '分析時發生錯誤，請稍後再試。')


def model_info():
    """Held-out test metrics of the deployed fusion model (written by run_experiments.py)."""
    for name in ('fusion_model_social.json', 'fusion_model.json'):
        path = ROOT / 'models' / name
        if path.is_file():
            trained = json.loads(path.read_text(encoding='utf-8')).get('trained_on') or {}
            return {'version': name, 'test': {**(trained.get('test_metrics') or {}), 'auc': trained.get('auc')},
                    'conformal': trained.get('conformal_test'), 'hard_negative_fpr': trained.get('hard_negative_fpr')}
    return {}


class Handler(BaseHTTPRequestHandler):
    def parse_request(self):
        if not super().parse_request():
            return False
        if self.headers.get('Host') not in self.local_origins(''):
            self.send_error(403, 'Host rejected')
            return False
        return True

    def local_origins(self, scheme='http://'):
        return {f'{scheme}{host}:{self.server.server_port}' for host in ('127.0.0.1', 'localhost')}

    def send(self, code, data, kind='application/json; charset=utf-8'):
        body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        assets = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'), '/styles.css': ('styles.css', 'text/css')}
        if getattr(self.server, 'review', False):  # internal labeling tool, off unless started with --review
            assets.update({'/review': ('review.html', 'text/html'), '/review.js': ('review.js', 'text/javascript')})
        if self.path in assets:
            name, kind = assets[self.path]
            return self.send(200, (ROOT / 'ui' / name).read_bytes(), kind + '; charset=utf-8')
        parts = self.path.split('/')
        if self.path == '/api/reviews' and getattr(self.server, 'review', False):
            rows = []
            for path in sorted((ROOT/'artifacts'/'review_queue').glob('*.json')):
                try:
                    row = json.loads(path.read_text(encoding='utf-8'))
                    row['queue_id'] = path.stem
                    rows.append(row)
                except (OSError, ValueError):
                    continue
            return self.send(200, rows)
        if self.path == '/api/model-info':
            return self.send(200, model_info())
        if len(parts) in (4, 5, 6) and parts[1:3] == ['api', 'jobs']:
            job = JOBS.get(parts[3])
            if job and len(parts) == 4:
                data = {k: v for k, v in job.items() if k != 'directory'}
                try:
                    data['progress'] = json.loads((job['directory'] / 'progress.json').read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    data['progress'] = None
                return self.send(200, data)
            directory = job['directory'] if job else None
            if directory and len(parts) == 5:
                if parts[4] == 'image':
                    report = job.get('report', {})
                    path = (report.get('browser_capture') or {}).get('screenshot_path')
                    image = Path(path).resolve() if path else directory / 'input.png'
                    if image.is_relative_to(directory) and image.is_file():
                        return self.send(200, image.read_bytes(), 'image/png')
                if parts[4] == 'ela' and (directory / 'ela.png').is_file():
                    return self.send(200, (directory / 'ela.png').read_bytes(), 'image/png')
            if directory and len(parts) == 6 and parts[4] == 'link' and parts[5].isdigit() and int(parts[5]) < 10:
                image = directory / 'links' / str(int(parts[5])) / 'page.png'
                if image.is_file():
                    return self.send(200, image.read_bytes(), 'image/png')
        self.send(404, {'error': 'Not found'})

    def do_POST(self):
        review_id = self.path.removeprefix('/api/reviews/')
        is_review = (getattr(self.server, 'review', False) and self.path.startswith('/api/reviews/') and len(review_id) == 24
                     and all(c in '0123456789abcdef' for c in review_id))
        if self.path != '/api/jobs' and not is_review:
            return self.send(404, {'error': 'Not found'})
        origin = self.headers.get('Origin')
        if origin and origin not in self.local_origins():
            return self.send(403, {'error': '拒絕來自其他網站的請求'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 14 * 1024 * 1024:
                raise ValueError('圖片不得超過 10 MB')
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict):
                raise ValueError('請求格式錯誤')
            if is_review:
                from review_workflow import save_review
                with LOCK:
                    reviewed = save_review(ROOT/'artifacts'/'review_queue'/(review_id+'.json'), payload)
                return self.send(200, {'status': reviewed['status'], 'message': 'Review saved; model unchanged'})
            with LOCK:
                if sum(j['state'] in ('queued', 'running') for j in JOBS.values()) >= 3:
                    return self.send(429, {'error': '目前分析的人數較多，請稍後再試。'})
                identifier = uuid.uuid4().hex
                directory = (ROOT / 'artifacts' / 'web' / identifier).resolve()
                if payload.get('url'):
                    url = payload['url']
                    if not isinstance(url, str) or len(url) > 4096:
                        raise ValueError('網址格式不正確')
                    source = ['--url', url]
                elif payload.get('image'):
                    from PIL import Image
                    try:
                        raw = base64.b64decode(payload['image'], validate=True)
                        with Image.open(io.BytesIO(raw)) as image:
                            image.verify()
                    except Exception:
                        raise ValueError('無法讀取這張圖片，請改用 PNG、JPG 或 WebP 檔') from None
                    if len(raw) > 10 * 1024 * 1024:
                        raise ValueError('圖片不得超過 10 MB')
                    directory.mkdir(parents=True, exist_ok=True)
                    with Image.open(io.BytesIO(raw)) as image:
                        image.convert('RGB').save(directory / 'input.png')
                    source = ['--image', str(directory / 'input.png')]
                else:
                    raise ValueError('請輸入網址或上傳截圖')
                directory.mkdir(parents=True, exist_ok=True)
                if payload.get('explain') is True:
                    source.append('--explain')
                (directory / 'meta.json').write_text(json.dumps({'target': str(payload.get('url') or payload.get('name') or '上傳圖片')[:300]},
                                                                ensure_ascii=False), encoding='utf-8')
                JOBS[identifier] = {'state': 'queued', 'directory': directory}
                POOL.submit(analyze, identifier, source, self.server.model, getattr(self.server, 'calibration', None))
            self.send(202, {'id': identifier})
        except (ValueError, TypeError, OSError) as exc:
            self.send(400, {'error': str(exc) if isinstance(exc, ValueError) else '請求格式錯誤'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--model-path', type=Path, default=ROOT / 'anti_fraud_E3_macbert')
    parser.add_argument('--calibration-json', type=Path, help='Explicitly apply a reviewed calibration candidate')
    parser.add_argument('--review', action='store_true', help='Also serve the internal case-review page at /review')
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.model = args.model_path.resolve()
    server.calibration = args.calibration_json.resolve() if args.calibration_json else None
    server.review = args.review
    print(f'Open http://127.0.0.1:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        POOL.shutdown(wait=True)


if __name__ == '__main__':
    main()
