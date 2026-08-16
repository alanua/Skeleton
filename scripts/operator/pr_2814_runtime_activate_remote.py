#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap

REPO = Path('/home/agent/agent-dev/repos/Skeleton')
TARGET_SHA = '45c155a14afdf4d34dbb07539cdff02807c3dcdb'
REPO_FULL = 'alanua/Skeleton'
PR_NUMBER = '2814'
ENC_DIR = Path('/etc/skeleton/credstore.encrypted')
TOKEN_CRED = ENC_DIR / 'bitwarden-access-token.cred'
REF_CRED = ENC_DIR / 'openrouter-secret-ref.cred'
RUNTIME_DIR = Path('/run/skeleton-runner-credentials')
MATERIALIZER = Path('/etc/systemd/system/skeleton-runner-secret-materialize.service')
OPENROUTER_MODEL = 'openrouter/z-ai/glm-4.5-air:free'

class ActivationError(RuntimeError):
    pass


def run(argv: list[str], *, input_text: str | None = None, env: dict[str, str] | None = None,
        timeout: int = 120, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        argv,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        timeout=timeout,
        check=False,
    )
    if check and result.returncode != 0:
        raise ActivationError('command_failed:' + Path(argv[0]).name)
    return result


def sudo(argv: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    return run(['sudo', '-n', *argv], **kwargs)


def systemctl_user(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(['systemctl', '--user', *args], check=check)


def systemctl_system(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return sudo(['systemctl', *args], check=check)


def unit_loaded(user: bool, unit: str) -> bool:
    cmd = ['systemctl', '--user'] if user else ['sudo', '-n', 'systemctl']
    result = run([*cmd, 'show', unit, '-p', 'LoadState', '--value'], check=False)
    return result.returncode == 0 and result.stdout.strip() not in {'', 'not-found', 'masked'}


def unit_enabled_or_active(user: bool, unit: str) -> bool:
    cmd = ['systemctl', '--user'] if user else ['sudo', '-n', 'systemctl']
    active = run([*cmd, 'is-active', unit], check=False).stdout.strip() in {'active', 'activating'}
    enabled = run([*cmd, 'is-enabled', unit], check=False).stdout.strip() in {'enabled', 'enabled-runtime', 'static'}
    return active or enabled


def safe_git_sync() -> None:
    if not (REPO / '.git').is_dir():
        raise ActivationError('runtime_repo_missing')
    origin = run(['git', '-C', str(REPO), 'remote', 'get-url', 'origin']).stdout.strip()
    if origin not in {'https://github.com/alanua/Skeleton', 'https://github.com/alanua/Skeleton.git'}:
        raise ActivationError('runtime_origin_mismatch')
    run(['git', '-C', str(REPO), 'fetch', '--quiet', 'origin', 'main'])
    origin_sha = run(['git', '-C', str(REPO), 'rev-parse', 'origin/main']).stdout.strip()
    if origin_sha != TARGET_SHA:
        raise ActivationError('origin_main_moved')
    dirty = run(['git', '-C', str(REPO), 'status', '--porcelain', '--untracked-files=all']).stdout.strip()
    if dirty:
        raise ActivationError('runtime_checkout_dirty')
    branch = run(['git', '-C', str(REPO), 'rev-parse', '--abbrev-ref', 'HEAD']).stdout.strip()
    if branch != 'main':
        run(['git', '-C', str(REPO), 'checkout', 'main'])
    run(['git', '-C', str(REPO), 'merge', '--ff-only', 'origin/main'])
    if run(['git', '-C', str(REPO), 'rev-parse', 'HEAD']).stdout.strip() != TARGET_SHA:
        raise ActivationError('runtime_sync_failed')


def bws_env(token: str) -> dict[str, str]:
    return {'BWS_ACCESS_TOKEN': token, 'HOME': '/home/agent', 'PATH': '/usr/local/bin:/usr/bin:/bin'}


def validate_bws(token: str) -> str:
    path = shutil.which('bws', path='/usr/local/bin:/usr/bin:/bin')
    if not path:
        raise ActivationError('bws_missing')
    version = run([path, '--version']).stdout.strip()
    if not version.startswith('bws 2.1.0'):
        raise ActivationError('bws_version_unexpected')
    probe = run([path, 'project', 'list', '--output', 'json'], env=bws_env(token), check=False, timeout=60)
    if probe.returncode != 0:
        raise ActivationError('bitwarden_auth_failed')
    try:
        value = json.loads(probe.stdout)
    except json.JSONDecodeError as exc:
        raise ActivationError('bitwarden_project_list_invalid') from exc
    if not isinstance(value, list):
        raise ActivationError('bitwarden_project_list_contract')
    return version


def ensure_openrouter_secret(token: str, payload: dict) -> tuple[str, str]:
    bws = shutil.which('bws', path='/usr/local/bin:/usr/bin:/bin') or '/usr/local/bin/bws'
    env = bws_env(token)
    ref = str(payload.get('secret_ref') or '').strip()
    if ref:
        got = run([bws, 'secret', 'get', ref, '--output', 'json'], env=env, check=False, timeout=60)
        if got.returncode != 0:
            raise ActivationError('bitwarden_secret_ref_unavailable')
        try:
            obj = json.loads(got.stdout)
        except json.JSONDecodeError as exc:
            raise ActivationError('bitwarden_secret_ref_invalid') from exc
        if not isinstance(obj, dict) or str(obj.get('id') or '') != ref or not isinstance(obj.get('value'), str) or not obj.get('value'):
            raise ActivationError('bitwarden_secret_ref_contract')
        return ref, 'EXISTING'

    api_key = str(payload.get('openrouter_key') or '').strip()
    if not api_key:
        raise ActivationError('openrouter_key_or_secret_ref_required')
    project_id = str(payload.get('project_id') or '').strip()
    if project_id:
        project = run([bws, 'project', 'get', project_id, '--output', 'json'], env=env, check=False, timeout=60)
        if project.returncode != 0:
            raise ActivationError('bitwarden_project_unavailable')
    else:
        created = run([bws, 'project', 'create', 'Skeleton Runtime', '--output', 'json'], env=env, check=False, timeout=60)
        if created.returncode != 0:
            raise ActivationError('bitwarden_project_create_failed')
        try:
            project_obj = json.loads(created.stdout)
            project_id = str(project_obj.get('id') or '')
        except (json.JSONDecodeError, AttributeError) as exc:
            raise ActivationError('bitwarden_project_create_invalid') from exc
        if not project_id:
            raise ActivationError('bitwarden_project_id_missing')

    listed = run([bws, 'secret', 'list', project_id, '--output', 'json'], env=env, check=False, timeout=60)
    if listed.returncode != 0:
        raise ActivationError('bitwarden_secret_list_failed')
    try:
        rows = json.loads(listed.stdout)
    except json.JSONDecodeError as exc:
        raise ActivationError('bitwarden_secret_list_invalid') from exc
    matches = [row for row in rows if isinstance(row, dict) and row.get('key') == 'SKELETON_OPENROUTER_API_KEY'] if isinstance(rows, list) else []
    if len(matches) > 1:
        raise ActivationError('multiple_openrouter_secrets')
    if matches:
        ref = str(matches[0].get('id') or '')
        edited = run([bws, 'secret', 'edit', ref, '--value', api_key, '--output', 'json'], env=env, check=False, timeout=60)
        if edited.returncode != 0:
            raise ActivationError('bitwarden_secret_update_failed')
        mode = 'UPDATED'
    else:
        created = run([bws, 'secret', 'create', 'SKELETON_OPENROUTER_API_KEY', api_key, project_id, '--note', 'Skeleton OpenHands/OpenRouter runtime', '--output', 'json'], env=env, check=False, timeout=60)
        if created.returncode != 0:
            raise ActivationError('bitwarden_secret_create_failed')
        try:
            obj = json.loads(created.stdout)
            ref = str(obj.get('id') or '')
        except (json.JSONDecodeError, AttributeError) as exc:
            raise ActivationError('bitwarden_secret_create_invalid') from exc
        if not ref:
            raise ActivationError('bitwarden_secret_id_missing')
        mode = 'CREATED'
    return ref, mode


def encrypt_credential(name: str, value: str, destination: Path) -> None:
    sudo(['install', '-d', '-m', '0700', str(ENC_DIR)])
    tmp = destination.with_suffix(destination.suffix + '.new')
    sudo(['rm', '-f', str(tmp)], check=False)
    result = sudo(['systemd-creds', 'encrypt', f'--name={name}', '-', str(tmp)], input_text=value, check=False, timeout=60)
    if result.returncode != 0:
        raise ActivationError('systemd_credential_encrypt_failed:' + name)
    sudo(['chmod', '0600', str(tmp)])
    sudo(['mv', '-f', str(tmp), str(destination)])


def install_materializer() -> None:
    unit = textwrap.dedent(f'''\
        [Unit]
        Description=Materialize Skeleton Runner credentials into volatile runtime storage
        After=local-fs.target

        [Service]
        Type=oneshot
        RemainAfterExit=yes
        User=agent
        RuntimeDirectory=skeleton-runner-credentials
        RuntimeDirectoryMode=0700
        LoadCredentialEncrypted=bitwarden-access-token:{TOKEN_CRED}
        LoadCredentialEncrypted=openrouter-secret-ref:{REF_CRED}
        ExecStart=/usr/bin/install -m 0400 %d/bitwarden-access-token {RUNTIME_DIR}/bitwarden-access-token
        ExecStart=/usr/bin/install -m 0400 %d/openrouter-secret-ref {RUNTIME_DIR}/openrouter-secret-ref

        [Install]
        WantedBy=multi-user.target
    ''')
    sudo(['tee', str(MATERIALIZER)], input_text=unit)
    sudo(['chmod', '0644', str(MATERIALIZER)])
    systemctl_system('daemon-reload')
    systemctl_system('enable', '--now', MATERIALIZER.name)
    if not (RUNTIME_DIR / 'bitwarden-access-token').is_file() or not (RUNTIME_DIR / 'openrouter-secret-ref').is_file():
        raise ActivationError('volatile_credentials_not_materialized')


def bind_runner_credentials() -> str:
    user_loaded = unit_loaded(True, 'skeleton-runner-poll.service')
    system_loaded = unit_loaded(False, 'skeleton-runner-poll.service')
    user_live = user_loaded and unit_enabled_or_active(True, 'skeleton-runner-poll.timer')
    system_live = system_loaded and unit_enabled_or_active(False, 'skeleton-runner-poll.timer')
    if user_live and system_live:
        raise ActivationError('duplicate_runner_lanes')
    if user_live or (user_loaded and not system_live):
        install_materializer()
        dropin_dir = Path.home() / '.config/systemd/user/skeleton-runner-poll.service.d'
        dropin_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        dropin = dropin_dir / '50-bitwarden-credentials.conf'
        dropin.write_text(textwrap.dedent(f'''\
            [Service]
            LoadCredential=bitwarden-access-token:{RUNTIME_DIR}/bitwarden-access-token
            LoadCredential=openrouter-secret-ref:{RUNTIME_DIR}/openrouter-secret-ref
        '''), encoding='utf-8')
        os.chmod(dropin, stat.S_IRUSR | stat.S_IWUSR)
        systemctl_user('daemon-reload')
        shown = systemctl_user('show', 'skeleton-runner-poll.service', '-p', 'LoadCredential', '--value').stdout
        if 'bitwarden-access-token' not in shown or 'openrouter-secret-ref' not in shown:
            raise ActivationError('user_runner_credential_binding_failed')
        return 'USER_SYSTEMD'
    if system_live or system_loaded:
        dropin_dir = Path('/etc/systemd/system/skeleton-runner-poll.service.d')
        sudo(['install', '-d', '-m', '0755', str(dropin_dir)])
        dropin = textwrap.dedent(f'''\
            [Service]
            LoadCredentialEncrypted=bitwarden-access-token:{TOKEN_CRED}
            LoadCredentialEncrypted=openrouter-secret-ref:{REF_CRED}
        ''')
        sudo(['tee', str(dropin_dir / '50-bitwarden-credentials.conf')], input_text=dropin)
        sudo(['chmod', '0644', str(dropin_dir / '50-bitwarden-credentials.conf')])
        systemctl_system('daemon-reload')
        shown = systemctl_system('show', 'skeleton-runner-poll.service', '-p', 'LoadCredentialEncrypted', '--value').stdout
        if 'bitwarden-access-token' not in shown or 'openrouter-secret-ref' not in shown:
            raise ActivationError('system_runner_credential_binding_failed')
        return 'SYSTEM_SYSTEMD'
    raise ActivationError('runner_service_not_found')


def run_canary() -> tuple[str, str]:
    canary_code = r'''
from __future__ import annotations
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
sys.path.insert(0, '/home/agent/agent-dev/repos/Skeleton')
from core.secret_store import SecretAccessPolicy, SecretResolutionContext, SecretStoreGate
from integrations.bitwarden_secret_store import BwsCliSecretsManagerStore, bitwarden_reference_from_systemd_credential

env = dict(os.environ)
ref = bitwarden_reference_from_systemd_credential(env, 'openrouter-secret-ref')
store = BwsCliSecretsManagerStore.from_systemd_credentials(env)
ctx = SecretResolutionContext(machine_identity='hetzner-agent-runner-1', audience='openhands-openrouter', task_kind='code_generation')
policy = SecretAccessPolicy(allowed_machine_identities=frozenset({'hetzner-agent-runner-1'}), allowed_audiences=frozenset({'openhands-openrouter'}), allowed_task_kinds=frozenset({'code_generation'}))
material = SecretStoreGate(stores={'bitwarden': store}, policies={(ref.provider, ref.reference_id): policy}).resolve(ref, ctx)
print('BITWARDEN_CANARY=PASS')
openhands = shutil.which('openhands', path=env.get('PATH',''))
if not openhands:
    print('OPENHANDS_OPENROUTER_CANARY=BLOCKED_OPENHANDS_MISSING')
    raise SystemExit(0)
child = {k:v for k,v in env.items() if k not in {'BWS_ACCESS_TOKEN','CREDENTIALS_DIRECTORY','OPENROUTER_API_KEY','LLM_API_KEY','LLM_MODEL','LLM_BASE_URL','MAX_BUDGET_PER_TASK','MAX_ITERATIONS','LLM_NUM_RETRIES'}}
child = material.inject(child, 'LLM_API_KEY')
child['LLM_MODEL'] = 'openrouter/z-ai/glm-4.5-air:free'
child['MAX_BUDGET_PER_TASK'] = '0.50'
child['MAX_ITERATIONS'] = '8'
child['LLM_NUM_RETRIES'] = '1'
with tempfile.TemporaryDirectory(prefix='skeleton-openrouter-canary.') as td:
    target = Path(td) / 'CANARY.txt'
    task = 'Create CANARY.txt in the current working directory containing exactly SKELETON_OPENROUTER_CANARY_OK followed by a newline. Do not modify any other file.'
    try:
        result = subprocess.run([openhands, '--headless', '--json', '--override-with-envs', '-t', task], cwd=td, env=child, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=240, check=False)
    except subprocess.TimeoutExpired:
        print('OPENHANDS_OPENROUTER_CANARY=FAIL_TIMEOUT')
        raise SystemExit(0)
    if result.returncode != 0:
        print('OPENHANDS_OPENROUTER_CANARY=FAIL_RC')
    elif not target.is_file():
        print('OPENHANDS_OPENROUTER_CANARY=FAIL_NO_ARTIFACT')
    elif target.read_text(encoding='utf-8') != 'SKELETON_OPENROUTER_CANARY_OK\n':
        print('OPENHANDS_OPENROUTER_CANARY=FAIL_BAD_ARTIFACT')
    else:
        print('OPENHANDS_OPENROUTER_CANARY=PASS')
'''
    with tempfile.NamedTemporaryFile('w', prefix='skeleton-pr2814-canary.', suffix='.py', delete=False, encoding='utf-8') as handle:
        handle.write(canary_code)
        canary_path = Path(handle.name)
    os.chmod(canary_path, 0o700)
    try:
        cmd = [
            'systemd-run', '--quiet', '--wait', '--pipe', '--collect',
            '--property=User=agent',
            f'--property=WorkingDirectory={REPO}',
            '--property=Environment=PATH=/home/agent/.local/bin:/usr/local/bin:/usr/bin:/bin',
            f'--property=LoadCredentialEncrypted=bitwarden-access-token:{TOKEN_CRED}',
            f'--property=LoadCredentialEncrypted=openrouter-secret-ref:{REF_CRED}',
            '/usr/bin/python3', str(canary_path),
        ]
        result = sudo(cmd, check=False, timeout=300)
    finally:
        canary_path.unlink(missing_ok=True)
    text = result.stdout + '\n' + result.stderr
    bw = 'PASS' if 'BITWARDEN_CANARY=PASS' in text else 'FAIL'
    oh = 'UNKNOWN'
    for line in text.splitlines():
        if line.startswith('OPENHANDS_OPENROUTER_CANARY='):
            oh = line.split('=', 1)[1].strip()
    return bw, oh


def publish_receipt(fields: dict[str, str]) -> str:
    lines = ['### PR #2814 runtime activation receipt', '', '```text']
    lines.extend(f'{key}={value}' for key, value in fields.items())
    lines.append('```')
    body = '\n'.join(lines) + '\n'
    result = run(['gh', 'pr', 'comment', PR_NUMBER, '--repo', REPO_FULL, '--body', body], check=False, timeout=60)
    return result.stdout.strip() if result.returncode == 0 else 'NOT_PUBLISHED'


def main() -> int:
    uid = os.getuid()
    os.environ.setdefault('XDG_RUNTIME_DIR', f'/run/user/{uid}')
    os.environ.setdefault('DBUS_SESSION_BUS_ADDRESS', f'unix:path=/run/user/{uid}/bus')
    raw = sys.stdin.buffer.read()
    try:
        payload = json.loads(raw.decode('utf-8'))
    except Exception:
        print('RESULT=BLOCKED:invalid_private_payload')
        return 0
    if not isinstance(payload, dict):
        print('RESULT=BLOCKED:invalid_private_payload')
        return 0
    token = str(payload.get('bws_token') or '').strip()
    if not token:
        print('RESULT=BLOCKED:bitwarden_machine_token_required')
        return 0

    fields: dict[str, str] = {
        'TARGET_MAIN': TARGET_SHA,
        'RUNTIME_SYNC': 'NOT_RUN',
        'BWS': 'NOT_RUN',
        'OPENROUTER_SECRET': 'NOT_RUN',
        'CREDENTIAL_STORAGE': 'NOT_RUN',
        'RUNNER_BINDING': 'NOT_RUN',
        'BITWARDEN_CANARY': 'NOT_RUN',
        'OPENHANDS_OPENROUTER_CANARY': 'NOT_RUN',
    }
    result_value = 'BLOCKED'
    try:
        safe_git_sync()
        fields['RUNTIME_SYNC'] = 'PASS'
        fields['BWS'] = validate_bws(token)
        ref, secret_mode = ensure_openrouter_secret(token, payload)
        fields['OPENROUTER_SECRET'] = secret_mode
        encrypt_credential('bitwarden-access-token', token, TOKEN_CRED)
        encrypt_credential('openrouter-secret-ref', ref, REF_CRED)
        fields['CREDENTIAL_STORAGE'] = 'SYSTEMD_ENCRYPTED'
        lane = bind_runner_credentials()
        fields['RUNNER_BINDING'] = lane
        bw_canary, oh_canary = run_canary()
        fields['BITWARDEN_CANARY'] = bw_canary
        fields['OPENHANDS_OPENROUTER_CANARY'] = oh_canary
        if bw_canary == 'PASS' and oh_canary == 'PASS':
            result_value = 'READY'
        elif bw_canary == 'PASS':
            result_value = 'BITWARDEN_READY_OPENHANDS_DEGRADED'
        else:
            result_value = 'BLOCKED'
    except ActivationError as exc:
        fields['ERROR_CLASS'] = str(exc).replace(' ', '_')[:120]
        result_value = 'BLOCKED'
    except Exception as exc:
        fields['ERROR_CLASS'] = type(exc).__name__
        result_value = 'BLOCKED'
    finally:
        token = ''
        payload.clear()
        fields['RESULT'] = result_value
        url = publish_receipt(fields)
        print('RESULT=' + result_value)
        print('RECEIPT_REF=' + url)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
