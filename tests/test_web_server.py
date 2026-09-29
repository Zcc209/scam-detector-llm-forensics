import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer
import web_server


class ServerTests(unittest.TestCase):
    def test_review_api_persists_without_training_and_rejects_origin(self):
        from review_workflow import write_json
        with tempfile.TemporaryDirectory() as temp, patch.object(web_server, 'ROOT', Path(temp)):
            for path in ('/review', '/api/reviews'):  # internal tool is off unless the server runs with --review
                with self.assertRaises(HTTPError) as error:
                    urlopen(self.url + path)
                self.assertEqual(error.exception.code, 404)
            self.server.review = True
            key = 'a' * 24
            path = Path(temp)/'artifacts'/'review_queue'/(key+'.json')
            write_json(path, {'case_id':key,'status':'pending','label':'Unresolved','texts':{'dom':'fixture'},
                             'synthetic':False,'scope':'content'})
            with urlopen(self.url+'/api/reviews') as response:
                self.assertEqual(json.load(response)[0]['queue_id'], key)
            payload = dict(label='Normal',group_id='site:fixture',split='train',reviewer='test',
                           label_source='fixture evidence',permission='test',notes='fixture only')
            url = self.url+'/api/reviews/'+key
            request = Request(url, data=json.dumps(payload).encode(), headers={'Content-Type':'application/json'})
            with urlopen(request) as response:
                self.assertEqual(json.load(response)['status'], 'reviewed')
            self.assertTrue(path.with_suffix('.history.jsonl').is_file())
            request = Request(url, data=json.dumps(payload).encode(), headers={'Origin':'https://evil.example'})
            with self.assertRaises(HTTPError) as error:
                urlopen(request)
            self.assertEqual(error.exception.code, 403)

    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), web_server.Handler)
        self.server.model = Path('model')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_static_and_unknown_paths(self):
        with urlopen(self.url) as response:
            self.assertIn(b'id="result-card"', response.read())
        for path in ('/../../README.md', '/api/history', '/api/jobs/..%2F..%2Fsecret/image', '/api/jobs/' + 'z' * 32):
            with self.assertRaises(HTTPError) as error:
                urlopen(self.url + path)
            self.assertEqual(error.exception.code, 404)

    def test_model_info_reports_held_out_metrics(self):
        with urlopen(self.url + '/api/model-info') as response:
            info = json.load(response)
        if info:  # present whenever models/fusion_model*.json has been generated
            self.assertTrue(0 <= info['test']['f1'] <= 1)

    def test_localhost_host_is_accepted(self):
        request = Request(f'http://localhost:{self.server.server_port}/')
        with urlopen(request) as response:
            self.assertIn('詐騙帳號偵測系統', response.read().decode('utf-8'))

    def test_invalid_and_cross_origin_upload(self):
        for payload, origin, code in [({}, None, 400), ([], None, 400),
                ({'image': 'invalid'}, None, 400), ({'url': 'https://example.com'}, 'https://evil.example', 403)]:
            headers = {'Content-Type': 'application/json'}
            if origin:
                headers['Origin'] = origin
            request = Request(self.url + '/api/jobs', data=json.dumps(payload).encode(), headers=headers)
            with self.assertRaises(HTTPError) as error:
                urlopen(request)
            self.assertEqual(error.exception.code, code)

    def test_job_submission_and_safe_image_path(self):
        with patch.object(web_server.POOL, 'submit') as submit:
            request = Request(self.url + '/api/jobs', data=b'{"url":"https://example.com"}', headers={'Content-Type': 'application/json'})
            with urlopen(request) as response:
                identifier = json.load(response)['id']
            self.assertTrue(submit.called)
        job = web_server.JOBS[identifier]
        job['report'] = {'browser_capture': {'screenshot_path': str(web_server.ROOT / 'README.md')}}
        with self.assertRaises(HTTPError) as error:
            urlopen(self.url + f'/api/jobs/{identifier}/image')
        self.assertEqual(error.exception.code, 404)
        web_server.JOBS.pop(identifier)

    def test_report_loaded_even_with_abstention_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / 'report.json').write_text('{"status":"incomplete"}', encoding='utf-8')
            web_server.JOBS['test'] = {'state': 'queued', 'directory': directory}
            with patch.object(web_server.subprocess, 'run') as run:
                run.return_value.stderr = 'diagnostic'
                run.return_value.returncode = 2
                web_server.analyze('test', ['--url', 'https://example.com'], Path('model'))
            self.assertEqual(web_server.JOBS.pop('test')['state'], 'done')


class EnvironmentCheckTests(unittest.TestCase):
    def test_refuses_to_start_without_pipeline_packages(self):
        # Started with a system Python instead of the project venv, every analysis failed with "No module named numpy".
        self.assertEqual(web_server.missing_packages(), [])
        with patch('importlib.util.find_spec', return_value=None), patch.object(web_server.sys, 'argv', ['web_server.py']):
            with self.assertRaises(SystemExit) as stop:
                web_server.main()
        self.assertIn('numpy', str(stop.exception.code))
