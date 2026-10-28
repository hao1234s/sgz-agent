"""Prepare a cloud release locally; --apply explicitly uploads and installs it."""
import argparse
from datetime import datetime, timezone
import io
import ipaddress
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def read_env(path):
    path = Path(path)
    if not path.exists():
        return {}
    lines = path.read_text(encoding='utf-8-sig').splitlines()
    # Preserve the user's existing two-line IP/password credential file.
    if len(lines) == 2:
        try:
            ipaddress.ip_address(lines[0].strip())
        except ValueError:
            pass
        else:
            return {'SSH_HOST': lines[0].strip(), 'SSH_PASSWORD': lines[1]}
    values = {}
    for number, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)', line)
        if not match:
            raise ValueError(f'Invalid environment-file format at line {number}; use KEY=VALUE')
        key, value = match.groups()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key in values:
            raise ValueError(f'Duplicate environment-file key at line {number}')
        values[key] = value  # No shell evaluation, interpolation or inline-comment stripping.
    return values


def render_files(settings):
    domain = settings.get('SITE_DOMAIN', '')
    if len(domain) > 253 or not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?', domain):
        raise ValueError('SITE_DOMAIN must be a hostname or IP, without scheme, port or path')
    if '..' in domain or any(len(part) > 63 or part.startswith('-') or part.endswith('-') for part in domain.split('.')):
        raise ValueError('SITE_DOMAIN has an invalid hostname label')
    replacements = {'@DOMAIN@': domain}
    files = {}
    for name in ('nginx.conf', 'sgz-agent.service'):
        text = (HERE / (name + '.template')).read_text(encoding='utf-8')
        for marker, value in replacements.items():
            text = text.replace(marker, value)
        files[name] = text
    files['install-cloud.sh'] = (HERE / 'install-cloud.sh').read_text(encoding='utf-8')
    return files


def create_bundle(dist, destination, settings):
    dist, destination = Path(dist), Path(destination)
    if not (dist / 'index.html').is_file():
        raise ValueError('frontend/dist/index.html is missing; build the frontend first')
    assets = []
    for path in sorted(dist.rglob('*')):
        if path.is_symlink() or path.name == '.env' or path.name.startswith('.env.') or path.suffix in {'.pem', '.key'}:
            raise ValueError('Frontend output contains an environment file, private-key file or symlink')
        if path.is_file():
            assets.append(path)
    files = render_files(settings)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(destination, 'w:gz') as archive:
        for path in assets:
            archive.add(path, arcname='frontend/' + path.relative_to(dist).as_posix(), recursive=False)
        for name, text in files.items():
            content = text.encode('utf-8')
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = 0o755 if name.endswith('.sh') else 0o644
            archive.addfile(info, io.BytesIO(content))
    return destination


def apply_release(bundle, settings):
    host, user = settings.get('SSH_HOST'), settings.get('SSH_USER')
    if not host or not user:
        raise ValueError('SSH_HOST and SSH_USER are required for --apply (or pass --user)')
    port = int(settings.get('SSH_PORT', '22'))
    if not 1 <= port <= 65535:
        raise ValueError('SSH_PORT must be between 1 and 65535')
    api_key = settings.get('DEEPSEEK_API_KEY', '')
    if api_key and not re.fullmatch(r'[A-Za-z0-9_.-]+', api_key):
        raise ValueError('DEEPSEEK_API_KEY contains unsupported characters')
    try:
        import paramiko
    except ImportError:
        raise ValueError('Install agent/deploy/requirements.txt before using --apply') from None
    client = paramiko.SSHClient()
    known_hosts = Path(settings.get('SSH_KNOWN_HOSTS', str(Path.home() / '.ssh/known_hosts'))).expanduser()
    if not known_hosts.is_file():
        raise ValueError('SSH known_hosts is missing; verify this server with OpenSSH first')
    client.load_system_host_keys(str(known_hosts))
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    release_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:8]
    stage = '/tmp/sgz-deploy-' + release_id
    try:
        client.connect(host, port=port, username=user, password=settings.get('SSH_PASSWORD') or None,
                       key_filename=settings.get('SSH_KEY_FILE') or None,
                       timeout=20, banner_timeout=20, auth_timeout=30)
        with client.open_sftp() as sftp:
            sftp.mkdir(stage, mode=0o700)
            sftp.put(str(bundle), stage + '/release.tgz')
            if api_key:
                with sftp.file(stage + '/dsh.env', 'w') as secret:
                    secret.chmod(0o600)
                    secret.write('DEEPSEEK_API_KEY=' + api_key + '\n')
        install = 'bash ' + shlex.quote(stage + '/install-cloud.sh') + ' ' + shlex.quote(settings['SITE_DOMAIN']) + ' ' + shlex.quote(release_id)
        command = ('tar -xzf ' + shlex.quote(stage + '/release.tgz') + ' -C ' + shlex.quote(stage)
                   + ' && if [ "$(id -u)" -eq 0 ]; then ' + install + '; else sudo -n ' + install + '; fi')
        # Combine streams before reading, so verbose npm output cannot deadlock stderr.
        channel = client.get_transport().open_session(timeout=30)
        channel.set_combine_stderr(True)
        channel.exec_command(command)
        deadline = time.monotonic() + 1200
        with channel.makefile('r') as output:
            # Server install output contains status only; the DSH token stays in its journal.
            for line in output:
                print(line.rstrip())
                if time.monotonic() > deadline:
                    raise TimeoutError('Deployment timed out; inspect the server before retrying')
        if channel.recv_exit_status() != 0:
            raise RuntimeError('Remote installer failed; previous configuration was retained or restored')
    finally:
        if client.get_transport() and client.get_transport().is_active():
            with client.open_sftp() as sftp:
                try:
                    sftp.remove(stage + '/dsh.env')
                except OSError:
                    pass
        client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env', type=Path, default=ROOT / '.env', help='Local SSH credentials; never archived')
    parser.add_argument('--config', type=Path, default=HERE / '.env.local', help='Local domain/API key settings')
    parser.add_argument('--domain', help='Public hostname; must match the server TLS certificate')
    parser.add_argument('--user', help='Explicit SSH login username')
    parser.add_argument('--apply', action='store_true', help='Connect to the server and install the prepared release')
    parser.add_argument('--skip-build', action='store_true', help='Package an already verified frontend/dist')
    args = parser.parse_args()
    settings = {**read_env(args.env), **read_env(args.config)}
    if args.domain:
        settings['SITE_DOMAIN'] = args.domain
    if args.user:
        settings['SSH_USER'] = args.user
    render_files(settings)  # Reject bad configuration before building or connecting.
    if not args.skip_build:
        npm = shutil.which('npm.cmd') or shutil.which('npm')
        if not npm:
            raise ValueError('npm is required to build the frontend')
        subprocess.run([npm, 'run', 'build'], cwd=ROOT / 'frontend', check=True)
    bundle = create_bundle(ROOT / 'frontend/dist', ROOT / '.state/deploy/release.tgz', settings)
    print(f'Prepared: {bundle}')
    if args.apply:
        apply_release(bundle, settings)
        print('Cloud services installed. Complete browser authentication and model verification as documented.')
    else:
        print('Prepare only: no SSH connection or server change was made.')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(f'Deployment failed: {error}', file=sys.stderr)
        sys.exit(1)
