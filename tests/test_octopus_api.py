import io
import json
from pathlib import Path
import stat
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from octopus_api import APIError, Client, NoRedirect, OPERATIONS, atomic_json


class OctopusClientTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.client = Client(self.root)

    def tearDown(self):
        self.directory.cleanup()

    def test_business_mutations_are_not_allowed(self):
        for name in ('regenerateSecretKey', 'submitGasSelfReading', 'createPayment', 'authenticate'):
            with self.assertRaises(APIError):
                self.client.read(name)
        self.assertEqual([name for name, query in OPERATIONS.items() if query.startswith('mutation ')],
                         ['authenticate'])

    def test_http_200_graphql_errors_and_partial_data_are_rejected(self):
        response = {'data': {'account': {}}, 'errors': [
            {'message': 'sensitive input must not be echoed', 'extensions': {'errorCode': 'KT-CT-4718'}}]}
        self.client.opener = Mock()
        self.client.opener.open.return_value = io.BytesIO(json.dumps(response).encode())
        with self.assertRaises(APIError) as exc:
            self.client._request('accounts')
        self.assertIn('KT-CT-4718', str(exc.exception))
        self.assertNotIn('sensitive input', str(exc.exception))

    def test_login_stores_session_only_with_private_permissions(self):
        result = {'obtainKrakenToken': {'token': 'session-token', 'refreshToken': 'refresh-token',
                                       'refreshExpiresIn': time.time()+3600}}
        with patch.object(self.client, '_request', return_value=result):
            self.client.authenticate({'email': 'test@example.invalid', 'password': 'test-password'})
        contents = self.client.session_path.read_text()
        self.assertNotIn('test-password', contents)
        self.assertNotIn('test@example.invalid', contents)
        self.assertEqual(stat.S_IMODE(self.client.session_path.stat().st_mode), 0o600)

    def test_refresh_rotates_the_saved_session_without_password(self):
        atomic_json(self.client.session_path, {'token':'old', 'expires_at':0, 'refresh_token':'renew',
                                               'refresh_expires_at':time.time()+3600})
        result = {'obtainKrakenToken': {'token':'new', 'refreshToken':'rotated',
                                       'refreshExpiresIn':time.time()+7200}}
        with patch.object(self.client, '_request', return_value=result) as request:
            session = self.client.session()
        request.assert_called_once_with('authenticate', {'input': {'refreshToken':'renew'}})
        self.assertEqual(session['refresh_token'],'rotated')
        self.assertEqual(json.loads(self.client.session_path.read_text())['token'],'new')

    def test_pdf_request_has_no_api_authorization_and_uses_safe_local_name(self):
        result = {'account': {'bill': {'attachments': {'edges': [{'node': {
            'filename':'../../invoice.pdf','temporaryUrl':'https://billing.s3.amazonaws.com/invoice.pdf?signed=1'
        }}]}}}}
        self.client.opener = Mock()
        self.client.opener.open.return_value = io.BytesIO(b'%PDF-1.4\nexample')
        with patch.object(self.client,'read',return_value=result):
            saved=self.client.download_bill('A-test','../bill',self.root/'files')
        request=self.client.opener.open.call_args.args[0]
        self.assertNotIn('Authorization',request.headers)
        self.assertEqual(Path(saved['path']).parent,(self.root/'files').resolve())
        self.assertEqual(stat.S_IMODE(Path(saved['path']).stat().st_mode),0o600)

    def test_unapproved_document_host_and_redirect_are_rejected(self):
        result = {'account': {'bill': {'attachments': {'edges': [{'node': {
            'filename':'invoice.pdf','temporaryUrl':'https://127.0.0.1/private.pdf'
        }}]}}}}
        with patch.object(self.client,'read',return_value=result):
            with self.assertRaises(APIError):
                self.client.download_bill('A-test','bill',self.root/'files')
        with self.assertRaises(APIError):
            NoRedirect().redirect_request(None,None,302,'',{},'https://example.com')


if __name__ == '__main__':
    unittest.main()
