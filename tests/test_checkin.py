import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

import requests
import checkin as app


def response(payload=None, status=200, text=None):
    r = requests.Response()
    r.status_code = status
    r._content = (text if text is not None else json.dumps(payload)).encode()
    return r


class CheckinTests(unittest.TestCase):
    def config(self, **env):
        with patch.dict(os.environ, env, clear=True):
            return app.Config()

    def test_exit_matrix(self):
        cfg = self.config(GLADOS_COOKIES='dummy')
        cases = [([], 1), ([0], 1), ([0, 0], 0), ([0, 1], 0), ([1, 1], 0), ([0, -2], 1), ([-2, -2], 1)]
        for codes, expected in cases:
            with self.subTest(codes=codes):
                checker = app.Checker(cfg)
                checker.results = [app.CheckinResult(i, 'glados.cloud', code=app.CheckinStatus(c)) for i, c in enumerate(codes)]
                self.assertEqual(checker.exit_code(), expected)

    def test_business_codes_and_invalid_payloads(self):
        cases = [({'code': 0}, 200, None, app.CheckinStatus.SUCCESS),
                 ({'code': 1}, 200, None, app.CheckinStatus.REPEAT),
                 ({'code': -2, 'message': 'No permission'}, 200, None, app.CheckinStatus.FAILURE),
                 ({}, 200, None, app.CheckinStatus.FAILURE),
                 ({'code': False}, 200, None, app.CheckinStatus.FAILURE),
                 ({'code': '0'}, 200, None, app.CheckinStatus.FAILURE),
                 ([], 200, None, app.CheckinStatus.FAILURE),
                 (None, 200, '<html>login</html>', app.CheckinStatus.FAILURE),
                 ({'code': 0}, 401, None, app.CheckinStatus.FAILURE),
                 ({'code': 0}, 302, None, app.CheckinStatus.FAILURE),
                 ({'code': 0}, 500, None, app.CheckinStatus.FAILURE)]
        for payload, status, text, expected in cases:
            with self.subTest(payload=payload, status=status), app.API('glados.cloud') as api:
                with patch.object(api.session, 'post', return_value=response(payload, status, text)) as post:
                    self.assertEqual(api.checkin('dummy')['code'], expected)
                    self.assertFalse(post.call_args.kwargs['allow_redirects'])
                    self.assertEqual(post.call_args.kwargs['json'], {'token': 'glados.cloud'})

    def test_timeout_and_sensitive_data_not_logged(self):
        with app.API('railgun.info', verbose=True) as api:
            for value in [response({'code': -2, 'message': 'PRIVATE_SENTINEL'}),
                          response(status=403, text='PRIVATE_SENTINEL')]:
                with patch.object(api.session, 'post', return_value=value), self.assertLogs(level='INFO') as logs:
                    self.assertEqual(api.checkin('PRIVATE_SENTINEL')['code'], app.CheckinStatus.FAILURE)
                self.assertNotIn('PRIVATE_SENTINEL', '\n'.join(logs.output))
            with patch.object(api.session, 'post', side_effect=requests.Timeout('PRIVATE_SENTINEL')), self.assertLogs(level='INFO') as logs:
                self.assertEqual(api.checkin('dummy')['code'], app.CheckinStatus.FAILURE)
            self.assertNotIn('PRIVATE_SENTINEL', '\n'.join(logs.output))

    def test_mapping_replaces_legacy_and_limits_destinations(self):
        cfg = self.config(GLADOS_COOKIES='old', GLADOS_COOKIES_BY_DOMAIN=json.dumps({'railgun.info': ['new&cookie']}))
        self.assertEqual(list(cfg.iter_tasks()), [(1, 'railgun.info', 'new&cookie')])
        for raw in ['{bad', '{}', '{"evil.example":["dummy"]}', '{"glados.cloud":[]}', '{"glados.cloud":"dummy"}']:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.config(GLADOS_COOKIES_BY_DOMAIN=raw)

    def test_failures_do_not_stop_other_tasks(self):
        checker = app.Checker(self.config(GLADOS_COOKIES='dummy'))
        with patch.object(checker, '_checkin_on_domain', side_effect=[RuntimeError('private'), app.CheckinResult(1, 'railgun.info', code=app.CheckinStatus.SUCCESS)]):
            checker.checkin_all()
        self.assertEqual(len(checker.results), 2)
        self.assertEqual(checker.exit_code(), 1)

    def test_failure_skips_points_and_exchange(self):
        cfg = self.config(GLADOS_COOKIES='dummy')
        with patch.object(app.API, 'get_status', return_value=('None', -2)), patch.object(app.API, 'checkin', return_value={'status': '签到失败', 'code': app.CheckinStatus.FAILURE}), patch.object(app.API, 'get_points') as points, patch.object(app.API, 'exchange') as exchange:
            app.Checker(cfg)._checkin_on_domain('dummy', 1, 'glados.cloud')
        points.assert_not_called()
        exchange.assert_not_called()

    def test_main_and_push_failure_preserve_status(self):
        for code, expected in [(app.CheckinStatus.FAILURE, 1), (app.CheckinStatus.SUCCESS, 0), (app.CheckinStatus.REPEAT, 0)]:
            with patch.dict(os.environ, {'GLADOS_COOKIES': 'dummy'}, clear=True), patch.object(app.Checker, '_checkin_on_domain', side_effect=lambda c, i, d: app.CheckinResult(i, d, code=code)), patch.object(app.PushService, 'send', side_effect=RuntimeError('private')):
                self.assertEqual(app.main(), expected)
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(app.main(), 1)
        with patch.dict(os.environ, {'GLADOS_COOKIES_BY_DOMAIN': '{bad'}, clear=True):
            self.assertEqual(app.main(), 1)
        with patch.dict(os.environ, {'GLADOS_COOKIES': 'dummy'}, clear=True), patch.object(app.Checker, 'format_results', side_effect=RuntimeError()):
            with patch.object(app.Checker, '_checkin_on_domain', return_value=app.CheckinResult(1, 'glados.cloud', code=app.CheckinStatus.SUCCESS)):
                self.assertEqual(app.main(), 1)

    def test_diagnose_only_gets_status(self):
        cfg = self.config(GLADOS_COOKIES='dummy')
        for payload, expected in [({'code': 0, 'data': {'leftDays': 10}}, 0), ({'code': -2}, 1), ({'code': False}, 1)]:
            with patch.object(app.API, '_make_request', return_value=response(payload)) as request:
                self.assertEqual(app.diagnose(cfg), expected)
                self.assertEqual(request.call_count, 2)
                self.assertTrue(all(c.args[1] == 'GET' and c.args[0].endswith('/api/user/status') for c in request.call_args_list))

    def test_process_exit_business_results(self):
        harness = """
import sys
from unittest.mock import patch
import checkin as app
codes = [int(c) for c in sys.argv[1].split(',')]
results = [app.CheckinResult(i, d, code=app.CheckinStatus(c)) for i, (d, c) in enumerate(zip(app.Config.DOMAINS, codes), 1)]
with patch.object(app.Checker, '_checkin_on_domain', side_effect=results):
    sys.exit(app.main())
"""
        env = {k: v for k, v in os.environ.items() if not k.startswith(('GLADOS_', 'PUSHDEER_'))}
        env['GLADOS_COOKIES'] = 'dummy'
        for codes, expected in [('0,0', 0), ('0,1', 0), ('1,1', 0), ('0,-2', 1), ('-2,-2', 1)]:
            with self.subTest(codes=codes):
                result = subprocess.run([sys.executable, '-c', harness, codes], env=env, capture_output=True)
                self.assertEqual(result.returncode, expected, result.stderr.decode())

    def test_process_exit_missing_cookie(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith(('GLADOS_', 'PUSHDEER_'))}
        result = subprocess.run([sys.executable, 'checkin.py'], env=env, capture_output=True)
        self.assertEqual(result.returncode, 1)


if __name__ == '__main__':
    unittest.main()
