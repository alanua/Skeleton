#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

REPO = Path('/home/agent/agent-dev/repos/Skeleton')
TARGET_SHA = '45c155a14afdf4d34dbb07539cdff02807c3dcdb'
TOKEN_CRED = Path('/etc/skeleton/credstore.encrypted/bitwarden-access-token.cred')
REF_CRED = Path('/etc/skeleton/credstore.encrypted/openrouter-secret-ref.cred')
SERVICE = 'skeleton-runner-poll.service'
TIMER = 'skeleton-runner-poll.timer'
PR_NUMBER = '2814'
REPO_FULL = 'alanua/Skeleton'
MODEL = 'openrouter/z-ai/glm-4.5-air:free'


class CanaryError(RuntimeError):
    pass


def run(argv: list[str], *, timeout: int = 120, check: bool = True, env: dict[str, str] | None = None, cwd: str | None = None) -> subprocess.CompletedProcess[str]:
    p = subprocess.run(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False, env=env, cwd=cwd)
    if check and p.returncode != 0:
        raise CanaryError('command_failed:' + Path(argv[0]).name)
    return p


def sudo(argv: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    return run(['sudo', '-n', *argv], **kwargs)


def preflight() -> None:
    if not (REPO / '.git').is_dir():
        raise CanaryError('runtime_repo_missing')
    if run(['git', '-C', str(REPO), 'rev-parse', 'HEAD']).stdout.strip() != TARGET_SHA:
        raise CanaryError('runtime_head_mismatch')
    if run(['git', '-C', str(REPO), 'status', '--porcelain', '--untracked-files=all']).stdout.strip():
        raise CanaryError('runtime_checkout_dirty')
    for path in (TOKEN_CRED, REF_CRED):
        if sudo(['test', '-s', str(path)], check=False).returncode != 0:
            raise CanaryError('encrypted_credential_missing')
    unit = sudo(['systemctl', 'cat', SERVICE], check=False).stdout
    if 'LoadCredentialEncrypted=bitwarden-access-token:' not in unit or 'LoadCredentialEncrypted=openrouter-secret-ref:' not in unit:
        raise CanaryError('runner_binding_missing')


def make_inner() -> str:
    return r'''from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
sys.path.insert(0, '/home/agent/agent-dev/repos/Skeleton')
from core.secret_store import SecretAccessPolicy, SecretResolutionContext, SecretStoreGate
from integrations.bitwarden_secret_store import BwsCliSecretsManagerStore, bitwarden_reference_from_systemd_credential

def emit(k, v):
    print(f'{k}={v}', flush=True)

env = dict(os.environ)
ref = bitwarden_reference_from_systemd_credential(env, 'openrouter-secret-ref')
store = BwsCliSecretsManagerStore.from_systemd_credentials(env)
ctx = SecretResolutionContext(machine_identity='hetzner-agent-runner-1', audience='openhands-openrouter', task_kind='code_generation')
policy = SecretAccessPolicy(
    allowed_machine_identities=frozenset({'hetzner-agent-runner-1'}),
    allowed_audiences=frozenset({'openhands-openrouter'}),
    allowed_task_kinds=frozenset({'code_generation'}),
)
material = SecretStoreGate(stores={'bitwarden': store}, policies={(ref.provider, ref.reference_id): policy}).resolve(ref, ctx)
emit('BITWARDEN_CANARY', 'PASS')
openhands = shutil.which('openhands', path=env.get('PATH', ''))
if not openhands:
    emit('OPENHANDS_PRESENT', 'NO')
    raise SystemExit(0)
emit('OPENHANDS_PRESENT', 'YES')
child = {k:v for k,v in env.items() if k not in {
    'BWS_ACCESS_TOKEN','CREDENTIALS_DIRECTORY','OPENROUTER_API_KEY','LLM_API_KEY','LLM_MODEL','LLM_BASE_URL',
    'MAX_BUDGET_PER_TASK','MAX_ITERATIONS','LLM_NUM_RETRIES'
}}
child = material.inject(child, 'LLM_API_KEY')
child['LLM_MODEL'] = 'openrouter/z-ai/glm-4.5-air:free'
child['MAX_BUDGET_PER_TASK'] = '0.50'
child['MAX_ITERATIONS'] = '10'
child['LLM_NUM_RETRIES'] = '1'

with tempfile.TemporaryDirectory(prefix='skeleton-openrouter-git-canary.') as td:
    subprocess.run(['git','init','-q'], cwd=td, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['git','config','user.email','canary@invalid.local'], cwd=td, check=True)
    subprocess.run(['git','config','user.name','Skeleton Canary'], cwd=td, check=True)
    Path(td, 'README.md').write_text('Synthetic Skeleton OpenHands canary workspace.\n', encoding='utf-8')
    subprocess.run(['git','add','README.md'], cwd=td, check=True)
    subprocess.run(['git','commit','-q','-m','canary baseline'], cwd=td, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    emit('SYNTHETIC_GIT_WORKSPACE', 'PASS')
    target = Path(td, 'CANARY.txt')
    task = 'Create CANARY.txt in the current repository root containing exactly SKELETON_OPENROUTER_CANARY_OK followed by a newline. Do not modify any other file. Finish after creating the file.'
    try:
        p = subprocess.run(
            [openhands, '--headless', '--json', '--override-with-envs', '-t', task],
            cwd=td, env=child, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=300, check=False,
        )
    except subprocess.TimeoutExpired:
        emit('OPENHANDS_TOOL_CANARY', 'FAIL_TIMEOUT')
        raise SystemExit(0)
    emit('OPENHANDS_TOOL_RC', str(p.returncode))
    if p.returncode == 0 and target.is_file() and target.read_text(encoding='utf-8') == 'SKELETON_OPENROUTER_CANARY_OK\n':
        emit('OPENHANDS_TOOL_CANARY', 'PASS')
        raise SystemExit(0)
    if p.returncode != 0:
        emit('OPENHANDS_TOOL_CANARY', 'FAIL_RC')
    elif not target.is_file():
        emit('OPENHANDS_TOOL_CANARY', 'FAIL_NO_ARTIFACT')
    else:
        emit('OPENHANDS_TOOL_CANARY', 'FAIL_BAD_ARTIFACT')

    # Distinguish provider/LLM failure from tool-execution failure without exposing model output.
    response_token = 'SKELETON_OPENROUTER_RESPONSE_OK_2814'
    response_task = f'Respond with exactly {response_token} and nothing else. Do not use tools.'
    try:
        r = subprocess.run(
            [openhands, '--headless', '--json', '--override-with-envs', '-t', response_task],
            cwd=td, env=child, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=180, check=False,
        )
    except subprocess.TimeoutExpired:
        emit('OPENHANDS_RESPONSE_CANARY', 'FAIL_TIMEOUT')
        raise SystemExit(0)
    emit('OPENHANDS_RESPONSE_RC', str(r.returncode))
    combined = (r.stdout or '') + '\n' + (r.stderr or '')
    emit('OPENHANDS_RESPONSE_CANARY', 'PASS' if r.returncode == 0 and response_token in combined else 'FAIL')
'''


def canary() -> dict[str, str]:
    inner = make_inner()
    with tempfile.NamedTemporaryFile('w', prefix='pr2814-openhands-git-v7.', suffix='.py', delete=False, encoding='utf-8') as handle:
        handle.write(inner)
        path = Path(handle.name)
    os.chmod(path, 0o700)
    try:
        cmd = [
            'systemd-run', '--quiet', '--wait', '--pipe', '--collect',
            '--property=User=agent',
            f'--property=WorkingDirectory={REPO}',
            '--property=Environment=HOME=/home/agent',
            '--property=Environment=PATH=/home/agent/.local/bin:/usr/local/bin:/usr/bin:/bin',
            f'--property=LoadCredentialEncrypted=bitwarden-access-token:{TOKEN_CRED}',
            f'--property=LoadCredentialEncrypted=openrouter-secret-ref:{REF_CRED}',
            '/usr/bin/python3', str(path),
        ]
        p = sudo(cmd, check=False, timeout=540)
    finally:
        path.unlink(missing_ok=True)
    fields: dict[str, str] = {}
    for line in (p.stdout + '\n' + p.stderr).splitlines():
        if '=' not in line:
            continue
        k, v = line.split('=', 1)
        if k in {
            'BITWARDEN_CANARY','OPENHANDS_PRESENT','SYNTHETIC_GIT_WORKSPACE','OPENHANDS_TOOL_RC',
            'OPENHANDS_TOOL_CANARY','OPENHANDS_RESPONSE_RC','OPENHANDS_RESPONSE_CANARY'
        }:
            fields[k] = v.strip()[:80]
    return fields


def publish(fields: dict[str, str]) -> str:
    body = '### PR #2814 OpenHands synthetic Git canary v7\n\n```text\n' + '\n'.join(f'{k}={v}' for k,v in fields.items()) + '\n```\n'
    p = run(['gh','pr','comment',PR_NUMBER,'--repo',REPO_FULL,'--body',body], check=False, timeout=60)
    return p.stdout.strip() if p.returncode == 0 else 'NOT_PUBLISHED'


def main() -> int:
    fields = {
        'TARGET_MAIN': TARGET_SHA,
        'RUNNER_BINDING': 'NOT_RUN',
        'BITWARDEN_CANARY': 'NOT_RUN',
        'OPENHANDS_TOOL_CANARY': 'NOT_RUN',
        'OPENHANDS_RESPONSE_CANARY': 'NOT_RUN',
        'RUNNER_TIMER': 'NOT_RUN',
    }
    result = 'BLOCKED'
    try:
        preflight()
        fields['RUNNER_BINDING'] = 'PASS'
        fields.update(canary())
        fields['RUNNER_TIMER'] = sudo(['systemctl','is-active',TIMER], check=False).stdout.strip() or 'UNKNOWN'
        if fields.get('BITWARDEN_CANARY') == 'PASS' and fields.get('OPENHANDS_TOOL_CANARY') == 'PASS' and fields['RUNNER_TIMER'] == 'active':
            result = 'READY'
        elif fields.get('BITWARDEN_CANARY') == 'PASS':
            result = 'BITWARDEN_READY_OPENHANDS_DEGRADED'
    except CanaryError as exc:
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
