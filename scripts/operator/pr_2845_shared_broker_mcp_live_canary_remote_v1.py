#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

REPO = Path('/home/agent/agent-dev/repos/Skeleton')
TARGET_SHA = '799b189acbbdf41bfeb7031df6becd2f6cd86ca2'
TOKEN_CRED = Path('/etc/skeleton/credstore.encrypted/bitwarden-access-token.cred')
REF_CRED = Path('/etc/skeleton/credstore.encrypted/openrouter-secret-ref.cred')
SERVICE = 'skeleton-runner-poll.service'
TIMER = 'skeleton-runner-poll.timer'
ISSUE_NUMBER = '2834'
REPO_FULL = 'alanua/Skeleton'
CANARY_MODEL = 'openrouter/moonshotai/kimi-k2'


class CanaryError(RuntimeError):
    pass


def run(argv: list[str], *, timeout: int = 120, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        argv,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if check and proc.returncode != 0:
        raise CanaryError('command_failed:' + Path(argv[0]).name)
    return proc


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
    if 'LoadCredentialEncrypted=bitwarden-access-token:' not in unit:
        raise CanaryError('bitwarden_token_binding_missing')
    if 'LoadCredentialEncrypted=openrouter-secret-ref:' not in unit:
        raise CanaryError('openrouter_ref_binding_missing')


def inner_code() -> str:
    return r'''from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, '/home/agent/agent-dev/repos/Skeleton')

from adapters.credential_mcp import CredentialMcpAdapter
from core.credential_broker import InProcessCredentialAdapter
from core.secret_store import SecretResolutionContext
from core.service_credentials import ServiceCredentialBinding, ServiceCredentialCatalog
from integrations.bitwarden_credential_runtime import CredentialRuntimeRegistration, build_bitwarden_credential_runtime
from integrations.bitwarden_secret_store import bitwarden_reference_from_systemd_credential
from integrations.credential_runtime import bind_registered_environment_credential


def emit(key: str, value: str) -> None:
    print(f'{key}={value}', flush=True)


def safe_base(source: dict[str, str]) -> dict[str, str]:
    blocked = {
        'BWS_ACCESS_TOKEN',
        'CREDENTIALS_DIRECTORY',
        'OPENROUTER_API_KEY',
        'LLM_API_KEY',
        'LLM_MODEL',
        'LLM_BASE_URL',
        'MAX_BUDGET_PER_TASK',
        'MAX_ITERATIONS',
        'LLM_NUM_RETRIES',
        'AGENT_FUNCTION_CALLING',
        'SKELETON_OPENROUTER_FALLBACK_API_KEY',
        'SKELETON_OPENROUTER_FALLBACK_MODEL',
    }
    return {k: v for k, v in source.items() if k not in blocked}


def main() -> int:
    fields = {
        'SHARED_BROKER_BIND': 'NOT_RUN',
        'SHARED_BROKER_RECEIPT_REDACTION': 'NOT_RUN',
        'MCP_PROBE': 'NOT_RUN',
        'MCP_FIND': 'NOT_RUN',
        'MCP_USE': 'NOT_RUN',
        'MCP_SPOOF_REJECT': 'NOT_RUN',
        'MCP_OUTPUT_REDACTION': 'NOT_RUN',
        'OPENHANDS_PRESENT': 'NOT_RUN',
        'OPENHANDS_SHARED_BROKER_TOOL_CANARY': 'NOT_RUN',
        'OPENHANDS_OUTPUT_REDACTION': 'NOT_RUN',
    }
    secret_value = ''
    mcp_observed: list[str] = []
    try:
        authority = dict(os.environ)
        child = safe_base(authority)
        receipt = bind_registered_environment_credential(
            service_id='runner-openhands',
            alias='openrouter-api',
            action_id='bind-openrouter-fallback',
            environment=child,
            authority_environment=authority,
        )
        result = receipt.get('result') if isinstance(receipt, dict) else None
        secret_value = child.get('SKELETON_OPENROUTER_FALLBACK_API_KEY', '')
        if isinstance(result, dict) and result.get('status') == 'USED' and secret_value:
            fields['SHARED_BROKER_BIND'] = 'PASS'
        else:
            fields['SHARED_BROKER_BIND'] = 'FAIL'
        receipt_text = json.dumps(receipt, sort_keys=True)
        fields['SHARED_BROKER_RECEIPT_REDACTION'] = (
            'PASS' if secret_value and secret_value not in receipt_text else 'FAIL'
        )

        reference = bitwarden_reference_from_systemd_credential(authority, 'openrouter-secret-ref')
        context = SecretResolutionContext(
            machine_identity='hetzner-agent-runner-1',
            audience='openhands-openrouter',
            task_kind='code_generation',
        )
        binding = ServiceCredentialBinding(
            service_id='runner-openhands',
            alias='openrouter-api',
            reference=reference,
            context=context,
            action_id='mcp-canary-inert-use',
            adapter_id='in_process',
            target_id='mcp-canary-inert-target',
            required=True,
            reload_mode='per_use',
        )

        def inert_consumer(material, candidate_binding) -> None:
            if candidate_binding.service_id != 'runner-openhands':
                raise RuntimeError('mcp_binding_identity_mismatch')
            value = material.inject({}, 'SKELETON_MCP_CANARY').get('SKELETON_MCP_CANARY')
            if not isinstance(value, str) or not value:
                raise RuntimeError('mcp_empty_material')
            mcp_observed.append(value)

        runtime = build_bitwarden_credential_runtime(
            catalog=ServiceCredentialCatalog([binding]),
            registrations=(CredentialRuntimeRegistration('runner-openhands', context),),
            adapters={
                'in_process': InProcessCredentialAdapter(
                    {'mcp-canary-inert-target': inert_consumer}
                )
            },
            authority_environment=authority,
        )
        mcp = CredentialMcpAdapter(runtime.control_for('runner-openhands'))
        probe = mcp.call_tool('credential_probe', {'alias': 'openrouter-api'})
        find = mcp.call_tool('credential_find', {'alias': 'openrouter-api'})
        use = mcp.call_tool(
            'credential_use',
            {'alias': 'openrouter-api', 'action_id': 'mcp-canary-inert-use'},
        )
        spoof = mcp.call_tool(
            'credential_use',
            {
                'service_id': 'other-service',
                'alias': 'openrouter-api',
                'action_id': 'mcp-canary-inert-use',
            },
        )
        fields['MCP_PROBE'] = 'PASS' if probe.get('result', {}).get('status') == 'AVAILABLE' else 'FAIL'
        fields['MCP_FIND'] = 'PASS' if find.get('result', {}).get('status') == 'AVAILABLE' else 'FAIL'
        fields['MCP_USE'] = (
            'PASS'
            if use.get('result', {}).get('status') == 'USED' and bool(mcp_observed)
            else 'FAIL'
        )
        fields['MCP_SPOOF_REJECT'] = 'PASS' if spoof.get('result', {}).get('status') == 'BLOCKED' else 'FAIL'
        mcp_text = json.dumps([probe, find, use, spoof], sort_keys=True)
        fields['MCP_OUTPUT_REDACTION'] = (
            'PASS' if mcp_observed and all(value not in mcp_text for value in mcp_observed) else 'FAIL'
        )

        openhands = shutil.which('openhands', path=authority.get('PATH', ''))
        if not openhands:
            fields['OPENHANDS_PRESENT'] = 'NO'
        else:
            fields['OPENHANDS_PRESENT'] = 'YES'
            oh_env = safe_base(authority)
            oh_env['LLM_API_KEY'] = secret_value
            oh_env['LLM_MODEL'] = 'openrouter/moonshotai/kimi-k2'
            oh_env['MAX_BUDGET_PER_TASK'] = '0.05'
            oh_env['MAX_ITERATIONS'] = '6'
            oh_env['LLM_NUM_RETRIES'] = '1'
            oh_env['AGENT_FUNCTION_CALLING'] = 'true'
            with tempfile.TemporaryDirectory(prefix='skeleton-shared-broker-openhands-canary.') as td:
                subprocess.run(['git', 'init', '-q'], cwd=td, check=True)
                subprocess.run(['git', 'config', 'user.email', 'canary@invalid.local'], cwd=td, check=True)
                subprocess.run(['git', 'config', 'user.name', 'Skeleton Canary'], cwd=td, check=True)
                Path(td, 'README.md').write_text('Synthetic bounded canary.\n', encoding='utf-8')
                subprocess.run(['git', 'add', 'README.md'], cwd=td, check=True)
                subprocess.run(['git', 'commit', '-q', '-m', 'baseline'], cwd=td, check=True)
                target = Path(td, 'CANARY.txt')
                task = (
                    'Create CANARY.txt in the current repository root containing exactly '
                    'SKELETON_SHARED_BROKER_TOOL_OK followed by a newline. '
                    'Do not modify any other file. Finish immediately after the file is created.'
                )
                proc = subprocess.run(
                    [openhands, '--headless', '--json', '--override-with-envs', '-t', task],
                    cwd=td,
                    env=oh_env,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=300,
                    check=False,
                )
                combined = (proc.stdout or '') + '\n' + (proc.stderr or '')
                fields['OPENHANDS_OUTPUT_REDACTION'] = (
                    'PASS' if secret_value and secret_value not in combined else 'FAIL'
                )
                if (
                    proc.returncode == 0
                    and target.is_file()
                    and target.read_text(encoding='utf-8') == 'SKELETON_SHARED_BROKER_TOOL_OK\n'
                ):
                    fields['OPENHANDS_SHARED_BROKER_TOOL_CANARY'] = 'PASS'
                elif proc.returncode != 0:
                    lowered = combined.lower()
                    if any(x in lowered for x in ('insufficient', 'payment', 'credit', 'quota')):
                        fields['OPENHANDS_SHARED_BROKER_TOOL_CANARY'] = 'BLOCKED_BILLING'
                    elif any(x in lowered for x in ('rate limit', '429')):
                        fields['OPENHANDS_SHARED_BROKER_TOOL_CANARY'] = 'BLOCKED_RATE_LIMIT'
                    else:
                        fields['OPENHANDS_SHARED_BROKER_TOOL_CANARY'] = 'FAIL_RC'
                elif not target.is_file():
                    fields['OPENHANDS_SHARED_BROKER_TOOL_CANARY'] = 'FAIL_NO_ARTIFACT'
                else:
                    fields['OPENHANDS_SHARED_BROKER_TOOL_CANARY'] = 'FAIL_BAD_ARTIFACT'
    except subprocess.TimeoutExpired:
        fields['CANARY_ERROR_CLASS'] = 'TimeoutExpired'
        fields['OPENHANDS_SHARED_BROKER_TOOL_CANARY'] = 'FAIL_TIMEOUT'
    except Exception as exc:
        fields['CANARY_ERROR_CLASS'] = type(exc).__name__
    finally:
        secret_value = ''
        mcp_observed.clear()

    required = (
        'SHARED_BROKER_BIND',
        'SHARED_BROKER_RECEIPT_REDACTION',
        'MCP_PROBE',
        'MCP_FIND',
        'MCP_USE',
        'MCP_SPOOF_REJECT',
        'MCP_OUTPUT_REDACTION',
        'OPENHANDS_OUTPUT_REDACTION',
        'OPENHANDS_SHARED_BROKER_TOOL_CANARY',
    )
    result = 'SECRETSTORE_PRODUCTION_CANARY_PASS' if all(fields.get(k) == 'PASS' for k in required) else 'SECRETSTORE_PRODUCTION_CANARY_BLOCKED'
    for key, value in fields.items():
        emit(key, value)
    emit('CANARY_MODEL', 'moonshotai/kimi-k2')
    emit('CONNECTOR_STATUS', 'CONNECTOR_REGISTRATION_REQUIRED')
    emit('RESULT', result)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
'''


def canary() -> dict[str, str]:
    code = inner_code()
    with tempfile.NamedTemporaryFile(
        'w',
        prefix='pr2845-secretstore-production-v1.',
        suffix='.py',
        delete=False,
        encoding='utf-8',
    ) as handle:
        handle.write(code)
        path = Path(handle.name)
    os.chmod(path, 0o700)
    try:
        command = [
            'systemd-run',
            '--quiet',
            '--wait',
            '--pipe',
            '--collect',
            '--property=User=agent',
            f'--property=WorkingDirectory={REPO}',
            '--property=Environment=HOME=/home/agent',
            '--property=Environment=PATH=/home/agent/.local/bin:/usr/local/bin:/usr/bin:/bin',
            f'--property=LoadCredentialEncrypted=bitwarden-access-token:{TOKEN_CRED}',
            f'--property=LoadCredentialEncrypted=openrouter-secret-ref:{REF_CRED}',
            '/usr/bin/python3',
            str(path),
        ]
        proc = sudo(command, check=False, timeout=420)
    finally:
        path.unlink(missing_ok=True)

    allowed = {
        'SHARED_BROKER_BIND',
        'SHARED_BROKER_RECEIPT_REDACTION',
        'MCP_PROBE',
        'MCP_FIND',
        'MCP_USE',
        'MCP_SPOOF_REJECT',
        'MCP_OUTPUT_REDACTION',
        'OPENHANDS_PRESENT',
        'OPENHANDS_SHARED_BROKER_TOOL_CANARY',
        'OPENHANDS_OUTPUT_REDACTION',
        'CANARY_MODEL',
        'CONNECTOR_STATUS',
        'CANARY_ERROR_CLASS',
        'RESULT',
    }
    fields: dict[str, str] = {}
    for line in (proc.stdout + '\n' + proc.stderr).splitlines():
        if '=' not in line:
            continue
        key, value = line.split('=', 1)
        if key in allowed:
            fields[key] = value.strip()[:120]
    if proc.returncode != 0 and 'RESULT' not in fields:
        fields['RESULT'] = 'SECRETSTORE_PRODUCTION_CANARY_BLOCKED'
        fields['CANARY_ERROR_CLASS'] = 'transient_unit_nonzero'
    return fields


def publish(fields: dict[str, str]) -> str:
    ordered = [
        'TARGET_MAIN',
        'RUNNER_BINDING',
        'SHARED_BROKER_BIND',
        'SHARED_BROKER_RECEIPT_REDACTION',
        'MCP_PROBE',
        'MCP_FIND',
        'MCP_USE',
        'MCP_SPOOF_REJECT',
        'MCP_OUTPUT_REDACTION',
        'OPENHANDS_PRESENT',
        'OPENHANDS_SHARED_BROKER_TOOL_CANARY',
        'OPENHANDS_OUTPUT_REDACTION',
        'CANARY_MODEL',
        'RUNNER_TIMER',
        'CONNECTOR_STATUS',
        'CANARY_ERROR_CLASS',
        'RESULT',
    ]
    body = '### PR #2845 shared-broker + MCP production live canary v1\n\n```text\n'
    body += '\n'.join(f'{key}={fields[key]}' for key in ordered if key in fields)
    body += '\n```\n\nNo secret values, provider stdout/stderr, credential contents, or private runtime data are published.'
    proc = run(
        ['gh', 'issue', 'comment', ISSUE_NUMBER, '--repo', REPO_FULL, '--body', body],
        check=False,
        timeout=60,
    )
    return proc.stdout.strip() if proc.returncode == 0 else 'NOT_PUBLISHED'


def main() -> int:
    fields: dict[str, str] = {
        'TARGET_MAIN': TARGET_SHA,
        'RUNNER_BINDING': 'NOT_RUN',
        'RUNNER_TIMER': 'NOT_RUN',
        'RESULT': 'SECRETSTORE_PRODUCTION_CANARY_BLOCKED',
    }
    try:
        preflight()
        fields['RUNNER_BINDING'] = 'PASS'
        fields.update(canary())
        fields['RUNNER_TIMER'] = sudo(['systemctl', 'is-active', TIMER], check=False).stdout.strip() or 'UNKNOWN'
    except CanaryError as exc:
        fields['CANARY_ERROR_CLASS'] = str(exc).replace(' ', '_')[:120]
    except Exception as exc:
        fields['CANARY_ERROR_CLASS'] = type(exc).__name__

    url = publish(fields)
    print('RESULT=' + fields.get('RESULT', 'SECRETSTORE_PRODUCTION_CANARY_BLOCKED'))
    print('RECEIPT_REF=' + url)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
