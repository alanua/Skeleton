#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile

REPO = Path('/home/agent/agent-dev/repos/Skeleton')
TARGET_SHA = 'a08a3922ac7e01c32226bb193a6f072c4662a81f'
TOKEN_CRED = Path('/etc/skeleton/credstore.encrypted/bitwarden-access-token.cred')
REF_CRED = Path('/etc/skeleton/credstore.encrypted/openrouter-secret-ref.cred')
SERVICE = 'skeleton-runner-poll.service'
TIMER = 'skeleton-runner-poll.timer'
ISSUE_NUMBER = '2822'
REPO_FULL = 'alanua/Skeleton'


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
import sys
sys.path.insert(0, '/home/agent/agent-dev/repos/Skeleton')

from core.credential_broker import InProcessCredentialAdapter
from core.secret_store import SecretResolutionContext
from core.service_credentials import ServiceCredentialBinding, ServiceCredentialCatalog
from integrations.bitwarden_credential_runtime import (
    CredentialRuntimeRegistration,
    CredentialRuntimeRegistrationError,
    build_bitwarden_credential_runtime,
)
from integrations.bitwarden_secret_store import bitwarden_reference_from_systemd_credential


def emit(key: str, value: str) -> None:
    print(f'{key}={value}', flush=True)


def main() -> int:
    fields = {
        'CREDENTIAL_PROBE': 'NOT_RUN',
        'CREDENTIAL_FIND': 'NOT_RUN',
        'CREDENTIAL_USE': 'NOT_RUN',
        'INERT_TARGET': 'NOT_RUN',
        'BAD_ACTION_REJECT': 'NOT_RUN',
        'CROSS_SERVICE_REJECT': 'NOT_RUN',
        'SPOOF_FIELDS_REJECT': 'NOT_RUN',
        'PUBLIC_OUTPUT_REDACTION': 'NOT_RUN',
    }
    observed_secret: list[str] = []
    try:
        env = dict(os.environ)
        reference = bitwarden_reference_from_systemd_credential(env, 'openrouter-secret-ref')
        context = SecretResolutionContext(
            machine_identity='hetzner-agent-runner-1',
            audience='skeleton-credential-broker-canary',
            task_kind='credential_canary',
        )
        binding = ServiceCredentialBinding(
            service_id='credential-canary',
            alias='openrouter-api',
            reference=reference,
            context=context,
            action_id='verify_inert_use',
            adapter_id='in_process',
            target_id='inert-consumer',
            required=True,
            reload_mode='per_use',
        )

        def inert_consumer(material, candidate_binding) -> None:
            if candidate_binding.service_id != 'credential-canary':
                raise RuntimeError('binding_identity_mismatch')
            value = material.inject({}, 'SKELETON_CANARY_SECRET').get('SKELETON_CANARY_SECRET')
            if not isinstance(value, str) or not value:
                raise RuntimeError('empty_material')
            observed_secret.append(value)
            return None

        runtime = build_bitwarden_credential_runtime(
            catalog=ServiceCredentialCatalog([binding]),
            registrations=[CredentialRuntimeRegistration(service_id='credential-canary', context=context)],
            adapters={
                'in_process': InProcessCredentialAdapter({'inert-consumer': inert_consumer}),
            },
            authority_environment=env,
        )
        control = runtime.control_for('credential-canary')

        probe = control.invoke('credential_probe', {'alias': 'openrouter-api'})
        find = control.invoke('credential_find', {'alias': 'openrouter-api'})
        use = control.invoke(
            'credential_use',
            {'alias': 'openrouter-api', 'action_id': 'verify_inert_use'},
        )
        bad_action = control.invoke(
            'credential_use',
            {'alias': 'openrouter-api', 'action_id': 'arbitrary-shell'},
        )
        spoof = control.invoke(
            'credential_use',
            {
                'service_id': 'other-service',
                'alias': 'openrouter-api',
                'action_id': 'verify_inert_use',
            },
        )

        cross_service_blocked = False
        try:
            runtime.control_for('other-service')
        except CredentialRuntimeRegistrationError:
            cross_service_blocked = True

        fields['CREDENTIAL_PROBE'] = 'PASS' if probe.get('result', {}).get('status') == 'AVAILABLE' else 'FAIL'
        fields['CREDENTIAL_FIND'] = 'PASS' if find.get('result', {}).get('status') == 'AVAILABLE' else 'FAIL'
        fields['CREDENTIAL_USE'] = 'PASS' if use.get('result', {}).get('status') == 'USED' else 'FAIL'
        fields['INERT_TARGET'] = 'PASS' if bool(observed_secret) else 'FAIL'
        fields['BAD_ACTION_REJECT'] = 'PASS' if bad_action.get('result', {}).get('status') == 'BLOCKED' else 'FAIL'
        fields['CROSS_SERVICE_REJECT'] = 'PASS' if cross_service_blocked else 'FAIL'
        fields['SPOOF_FIELDS_REJECT'] = 'PASS' if spoof.get('result', {}).get('status') == 'BLOCKED' else 'FAIL'

        serialized = json.dumps([probe, find, use, bad_action, spoof], sort_keys=True)
        if observed_secret and all(secret not in serialized for secret in observed_secret):
            fields['PUBLIC_OUTPUT_REDACTION'] = 'PASS'
        else:
            fields['PUBLIC_OUTPUT_REDACTION'] = 'FAIL'

        result = 'BITWARDEN_GENERIC_BROKER_PASS' if all(value == 'PASS' for value in fields.values()) else 'BITWARDEN_GENERIC_BROKER_FAIL'
    except Exception as exc:
        fields['CANARY_ERROR_CLASS'] = type(exc).__name__
        result = 'BITWARDEN_GENERIC_BROKER_BLOCKED'
    finally:
        observed_secret.clear()

    for key, value in fields.items():
        emit(key, value)
    emit('RESULT', result)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
'''


def canary() -> dict[str, str]:
    code = inner_code()
    with tempfile.NamedTemporaryFile(
        'w',
        prefix='pr2826-bitwarden-broker-v1.',
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
        proc = sudo(command, check=False, timeout=180)
    finally:
        path.unlink(missing_ok=True)

    allowed = {
        'CREDENTIAL_PROBE',
        'CREDENTIAL_FIND',
        'CREDENTIAL_USE',
        'INERT_TARGET',
        'BAD_ACTION_REJECT',
        'CROSS_SERVICE_REJECT',
        'SPOOF_FIELDS_REJECT',
        'PUBLIC_OUTPUT_REDACTION',
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
        fields['RESULT'] = 'BITWARDEN_GENERIC_BROKER_BLOCKED'
        fields['CANARY_ERROR_CLASS'] = 'transient_unit_nonzero'
    return fields


def publish(fields: dict[str, str]) -> str:
    ordered = [
        'TARGET_MAIN',
        'RUNNER_BINDING',
        'CREDENTIAL_PROBE',
        'CREDENTIAL_FIND',
        'CREDENTIAL_USE',
        'INERT_TARGET',
        'BAD_ACTION_REJECT',
        'CROSS_SERVICE_REJECT',
        'SPOOF_FIELDS_REJECT',
        'PUBLIC_OUTPUT_REDACTION',
        'RUNNER_TIMER',
        'CANARY_ERROR_CLASS',
        'RESULT',
    ]
    body = '### Universal Bitwarden credential broker live canary v1\n\n```text\n'
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
        'RESULT': 'BITWARDEN_GENERIC_BROKER_BLOCKED',
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
    print('RESULT=' + fields.get('RESULT', 'BITWARDEN_GENERIC_BROKER_BLOCKED'))
    print('RECEIPT_REF=' + url)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
