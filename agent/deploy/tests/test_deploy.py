import importlib.util
from contextlib import redirect_stderr
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import MagicMock, patch

MODULE = Path(__file__).resolve().parents[1] / 'deploy.py'
spec = importlib.util.spec_from_file_location('cloud_deploy', MODULE)
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


class DeploymentTests(unittest.TestCase):
    def test_legacy_credentials_are_read_without_rewriting(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '.env'
            original = '192.0.2.10\nexample-password=#literal\n'
            path.write_text(original, encoding='utf-8')
            values = deploy.read_env(path)
            self.assertEqual(values['SSH_HOST'], '192.0.2.10')
            self.assertEqual(values['SSH_PASSWORD'], 'example-password=#literal')
            self.assertNotIn('SSH_USER', values)
            self.assertEqual(path.read_text(encoding='utf-8'), original)

    def test_dotenv_values_remain_literal(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '.env'
            path.write_text('SSH_USER=root\nSSH_PASSWORD="literal$()#=value"\n', encoding='utf-8')
            self.assertEqual(deploy.read_env(path)['SSH_PASSWORD'], 'literal$()#=value')

    def test_bad_env_error_does_not_disclose_line_contents(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '.env'
            path.write_text('secret-with-no-assignment\n', encoding='utf-8')
            with self.assertRaises(ValueError) as failure:
                deploy.read_env(path)
            self.assertNotIn('secret-with-no-assignment', str(failure.exception))

    def test_public_proxy_preserves_dsh_auth_and_prefix(self):
        files = deploy.render_files({'SITE_DOMAIN': 'agent.example.com'})
        nginx = files['nginx.conf']
        self.assertIn('proxy_set_header Host $http_host;', nginx)
        self.assertIn('proxy_pass http://127.0.0.1:3080/;', nginx)
        self.assertIn('proxy_cookie_path / /dsh/;', nginx)
        self.assertIn('proxy_cookie_flags ~ secure;', nginx)
        self.assertIn('proxy_set_header Upgrade $http_upgrade;', nginx)
        self.assertNotIn('$request ', nginx)
        self.assertNotIn('proxy_set_header Origin', nginx)
        service = files['sgz-agent.service']
        self.assertIn('--public-url https://agent.example.com/dsh/', service)
        self.assertIn('--trusted-host agent.example.com', service)
        self.assertIn('--host 127.0.0.1', service)
        self.assertIn('EnvironmentFile=/etc/sgz-agent/dsh.env', service)
        self.assertNotIn('danger-full-access', service)

    def test_rejects_config_injection(self):
        for domain in ['bad; include /tmp/x;', 'example.com\nserver {}', '../etc', 'https://example.com']:
            with self.subTest(domain=domain), self.assertRaises(ValueError):
                deploy.render_files({'SITE_DOMAIN': domain})

    def test_bundle_contains_only_build_and_nonsecret_deployment_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            dist = root / 'dist'
            dist.mkdir()
            (dist / 'index.html').write_text('frontend', encoding='utf-8')
            (root / '.env').write_text('SSH_PASSWORD=do-not-ship', encoding='utf-8')
            archive = deploy.create_bundle(dist, root / 'release.tgz', {'SITE_DOMAIN': 'agent.example.com', 'SSH_PASSWORD': 'do-not-ship'})
            with tarfile.open(archive) as bundle:
                names = set(bundle.getnames())
                self.assertIn('frontend/index.html', names)
                self.assertIn('nginx.conf', names)
                self.assertIn('sgz-agent.service', names)
                self.assertIn('install-cloud.sh', names)
                for member in bundle.getmembers():
                    if member.isfile():
                        self.assertNotIn(b'do-not-ship', bundle.extractfile(member).read())
            (dist / '.env').write_text('unexpected secret', encoding='utf-8')
            with self.assertRaises(ValueError):
                deploy.create_bundle(dist, root / 'rejected.tgz', {'SITE_DOMAIN': 'agent.example.com'})

    def test_disconnected_upload_reports_unconfirmed_secret_cleanup(self):
        client = MagicMock()
        transport = client.get_transport.return_value
        transport.is_active.return_value = True
        channel = transport.open_session.return_value
        def disconnect(_command):
            transport.is_active.return_value = False
            raise RuntimeError('SSH connection lost')
        channel.exec_command.side_effect = disconnect
        paramiko = MagicMock()
        paramiko.SSHClient.return_value = client
        with tempfile.TemporaryDirectory() as folder:
            known_hosts = Path(folder) / 'known_hosts'
            known_hosts.touch()
            settings = {'SSH_HOST': '192.0.2.10', 'SSH_USER': 'root',
                        'SITE_DOMAIN': 'agent.example.com', 'DEEPSEEK_API_KEY': 'sk-test-private',
                        'SSH_KNOWN_HOSTS': str(known_hosts)}
            errors = io.StringIO()
            with patch.dict('sys.modules', {'paramiko': paramiko}), redirect_stderr(errors):
                with self.assertRaisesRegex(RuntimeError, 'SSH connection lost'):
                    deploy.apply_release(Path(folder) / 'release.tgz', settings)
            self.assertIn('temporary credential file', errors.getvalue())
            self.assertNotIn('sk-test-private', errors.getvalue())
            client.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
