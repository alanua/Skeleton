#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile

REPO = Path('/home/agent/agent-dev/repos/Skeleton')
TARGET_SHA = '45c155a14afdf4d34dbb07539cdff02807c3dcdb'
TOKEN_CRED = Path('/etc/skeleton/credstore.encrypted/bitwarden-access-token.cred')
REF_CRED = Path('/etc/skeleton/credstore.encrypted/openrouter-secret-ref.cred')
DROPIN_DIR = Path('/etc/systemd/system/skeleton-runner-poll.service.d')
DROPIN = DROPIN_DIR / '50-bitwarden-credentials.conf'
SERVICE = 'skeleton-runner-poll.service'
TIMER = 'skeleton-runner-poll.timer'
PR_NUMBER = '2814'
REPO_FULL = 'alanua/Skeleton'

class RepairError(RuntimeError):
    pass


def run(argv: list[str], *, input_text: str | None = None, timeout: int = 120, check: bool = True) -> subprocess.CompletedProcess[str]:
    p = subprocess.run(argv, input=input_text, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False)
    if check and p.returncode != 0:
        raise RepairError('command_failed:' + Path(argv[0]).name)
    return p


def sudo(argv: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    return run(['sudo', '-n', *argv], **kwargs)


def ensure_runtime() -> None:
    if not (REPO / '.git').is_dir():
        raise RepairError('runtime_repo_missing')
    sha = run(['git', '-C', str(REPO), 'rev-parse', 'HEAD']).stdout.strip()
    if sha != TARGET_SHA:
        raise RepairError('runtime_head_mismatch')
    dirty = run(['git', '-C', str(REPO), 'status', '--porcelain', '--untracked-files=all']).stdout.strip()
    if dirty:
        raise RepairError('runtime_checkout_dirty')


def credential_files_ready() -> None:
    for p in (TOKEN_CRED, REF_CRED):
        r = sudo(['test', '-s', str(p)], check=False)
        if r.returncode != 0:
            raise RepairError('encrypted_credential_missing')


def install_system_binding() -> None:
    load = sudo(['systemctl', 'show', SERVICE, '-p', 'LoadState', '--value'], check=False).stdout.strip()
    if load in {'', 'not-found', 'masked'}:
        raise RepairError('system_runner_service_not_loaded')
    unit = (
        '[Service]\n'
        f'LoadCredentialEncrypted=bitwarden-access-token:{TOKEN_CRED}\n'
        f'LoadCredentialEncrypted=openrouter-secret-ref:{REF_CRED}\n'
    )
    sudo(['install', '-d', '-m', '0755', str(DROPIN_DIR)])
    sudo(['tee', str(DROPIN)], input_text=unit)
    sudo(['chmod', '0644', str(DROPIN)])
    sudo(['systemctl', 'daemon-reload'])
    cat = sudo(['systemctl', 'cat', SERVICE]).stdout
    if f'LoadCredentialEncrypted=bitwarden-access-token:{TOKEN_CRED}' not in cat:
        raise RepairError('bitwarden_dropin_not_loaded')
    if f'LoadCredentialEncrypted=openrouter-secret-ref:{REF_CRED}' not in cat:
        raise RepairError('openrouter_ref_dropin_not_loaded')


def run_canary() -> tuple[str, str]:
    code = r'''from __future__ import annotations
import os, shutil, subprocess, sys, tempfile
from pathlib import Path
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
        p = subprocess.run([openhands, '--headless', '--json', '--override-with-envs', '-t', task], cwd=td, env=child, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=240, check=False)
    except subprocess.TimeoutExpired:
        print('OPENHANDS_OPENROUTER_CANARY=FAIL_TIMEOUT')
        raise SystemExit(0)
    if p.returncode != 0:
        print('OPENHANDS_OPENROUTER_CANARY=FAIL_RC')
    elif not target.is_file():
        print('OPENHANDS_OPENROUTER_CANARY=FAIL_NO_ARTIFACT')
    elif target.read_text(encoding='utf-8') != 'SKELETON_OPENROUTER_CANARY_OK\n':
        print('OPENHANDS_OPENROUTER_CANARY=FAIL_BAD_ARTIFACT')
    else:
        print('OPENHANDS_OPENROUTER_CANARY=PASS')
'''
    with tempfile.NamedTemporaryFile('w', prefix='pr2814-binding-canary.', suffix='.py', delete=False, encoding='utf-8') as h:
        h.write(code)
        canary = Path(h.name)
    os.chmod(canary, 0o700)
    try:
        cmd = [
            'systemd-run', '--quiet', '--wait', '--pipe', '--collect',
            '--property=User=agent',
            f'--property=WorkingDirectory={REPO}',
            '--property=Environment=HOME=/home/agent',
            '--property=Environment=PATH=/home/agent/.local/bin:/usr/local/bin:/usr/bin:/bin',
            f'--property=LoadCredentialEncrypted=bitwarden-access-token:{TOKEN_CRED}',
            f'--property=LoadCredentialEncrypted=openrouter-secret-ref:{REF_CRED}',
            '/usr/bin/python3', str(canary),
        ]
        p = sudo(cmd, check=False, timeout=300)
    finally:
        canary.unlink(missing_ok=True)
    text = p.stdout + '\n' + p.stderr
    bw = 'PASS' if 'BITWARDEN_CANARY=PASS' in text else 'FAIL'
    oh = 'UNKNOWN'
    for line in text.splitlines():
        if line.startswith('OPENHANDS_OPENROUTER_CANARY='):
            oh = line.split('=', 1)[1].strip()
    return bw, oh


def activate_runner() -> tuple[str, str]:
    timer_load = sudo(['systemctl', 'show', TIMER, '-p', 'LoadState', '--value'], check=False).stdout.strip()
    if timer_load not in {'', 'not-found', 'masked'}:
        sudo(['systemctl', 'restart', TIMER], check=False)
    start = sudo(['systemctl', 'start', SERVICE], check=False, timeout=180)
    service_rc = 'PASS' if start.returncode == 0 else 'FAIL'
    timer_state = sudo(['systemctl', 'is-active', TIMER], check=False).stdout.strip() if timer_load not in {'', 'not-found', 'masked'} else 'NOT_PRESENT'
    return service_rc, timer_state


def publish(fields: dict[str, str]) -> str:
    body = '### PR #2814 runtime binding repair v6\n\n```text\n' + '\n'.join(f'{k}={v}' for k,v in fields.items()) + '\n```\n'
    p = run(['gh','pr','comment',PR_NUMBER,'--repo',REPO_FULL,'--body',body], check=False, timeout=60)
    return p.stdout.strip() if p.returncode == 0 else 'NOT_PUBLISHED'


def main() -> int:
    fields = {
        'TARGET_MAIN': TARGET_SHA,
        'ENCRYPTED_CREDENTIALS': 'NOT_RUN',
        'RUNNER_LANE': 'SYSTEM_SYSTEMD',
        'DROPIN_BINDING': 'NOT_RUN',
        'BITWARDEN_CANARY': 'NOT_RUN',
        'OPENHANDS_OPENROUTER_CANARY': 'NOT_RUN',
        'RUNNER_SERVICE_START': 'NOT_RUN',
        'RUNNER_TIMER': 'NOT_RUN',
    }
    result = 'BLOCKED'
    try:
        ensure_runtime()
        credential_files_ready()
        fields['ENCRYPTED_CREDENTIALS'] = 'PRESENT'
        install_system_binding()
        fields['DROPIN_BINDING'] = 'PASS'
        bw, oh = run_canary()
        fields['BITWARDEN_CANARY'] = bw
        fields['OPENHANDS_OPENROUTER_CANARY'] = oh
        service_rc, timer_state = activate_runner()
        fields['RUNNER_SERVICE_START'] = service_rc
        fields['RUNNER_TIMER'] = timer_state
        if bw == 'PASS' and service_rc == 'PASS' and oh == 'PASS':
            result = 'READY'
        elif bw == 'PASS' and service_rc == 'PASS':
            result = 'BITWARDEN_READY_OPENHANDS_DEGRADED'
        else:
            result = 'BLOCKED'
    except RepairError as exc:
        fields['ERROR_CLASS'] = str(exc).replace(' ','_')[:120]
    except Exception as exc:
        fields['ERROR_CLASS'] = type(exc).__name__
    fields['RESULT'] = result
    url = publish(fields)
    print('RESULT=' + result)
    print('RECEIPT_REF=' + url)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
