"""Offline current-evidence and public receipt regression tests."""
import json
import shutil
from pathlib import Path

import pytest

from core import page_pipeline as pipeline
from core import page_publisher_backends as backends
from test_page_pipeline import _manifest


def snapshot(path):
    return {str(p.relative_to(path)): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in path.rglob('*') if p.is_file()}


def forbid(*args, **kwargs):
    raise AssertionError('unexpected mutation')


@pytest.mark.parametrize('change', ['none', 'removed', 'mutated', 'owner', 'root', 'base', 'stable', 'revision', 'identity', 'corrupt', 'list'])
def test_filesystem_current_evidence(tmp_path, monkeypatch, change):
    root = tmp_path / 'runtime'
    published = tmp_path / 'published'
    manifest, _ = _manifest(tmp_path, 'filesystem_static_v1', root=str(published), base_url='https://example.test')
    first = pipeline.publish_manifest(manifest, root=root)
    assert first['status'] == 'PUBLISHED'
    target = published / 'pages/demo-page'
    state = root / 'state/filesystem_static_v1/demo/demo-page.json'
    manifest['publication_mode'] = 'update_owned'
    if change == 'removed':
        shutil.rmtree(target)
    elif change == 'mutated':
        (target / 'styles.css').write_text('changed')
    elif change == 'owner':
        owner = target / backends._OWNER_FILE
        value = json.loads(owner.read_text())
        value['owner_module'] = 'other'
        owner.write_text(json.dumps(value))
    elif change == 'root':
        manifest['backend_options']['root'] = str(tmp_path / 'new-destination')
    elif change == 'base':
        manifest['backend_options']['base_url'] = 'https://other.test'
    elif change == 'stable':
        manifest['stable_url'] = 'https://other.test/pages/demo-page/'
    elif change in {'revision', 'identity'}:
        value = json.loads(state.read_text())
        value['revision' if change == 'revision' else 'owner_module'] = 'wrong'
        state.write_text(json.dumps(value))
    elif change in {'corrupt', 'list'}:
        state.write_text('{' if change == 'corrupt' else '[]')
    before = snapshot(published), snapshot(root / 'state'), snapshot(root / 'receipts')
    monkeypatch.setattr(backends, '_copy_rendered', forbid)
    monkeypatch.setattr(pipeline, '_run_actions', forbid)
    result = pipeline.publish_manifest(manifest, root=root, command_runner=forbid, url_fetcher=forbid)
    assert result['status'] == ('NO_CHANGE' if change == 'none' else 'BLOCKED')
    assert before == (snapshot(published), snapshot(root / 'state'), snapshot(root / 'receipts'))
    assert not (tmp_path / 'new-destination').exists()


def fake_encrypt(args, cwd, timeout):
    assert args[:2] == ['openssl', 'enc']
    Path(args[args.index('-out') + 1]).write_bytes(b'synthetic cipher')
    return 0, ''


@pytest.mark.parametrize('change', ['none', 'missing', 'wrong', 'unavailable', 'payload', 'repository', 'local', 'key', 'verification'])
def test_encrypted_current_evidence(tmp_path, monkeypatch, change):
    repo = tmp_path / 'repo'
    manifest, _ = _manifest(tmp_path, 'github_pages_encrypted_v1', repository_path=str(repo),
                            base_url='https://example.test', git_mode='none', verification_mode='https',
                            verify_attempts=1, verify_delay_seconds=0)
    root = tmp_path / 'runtime'
    target = repo / 'demo-page'
    mode = ['none']

    def fetch(url, timeout):
        if mode[0] == 'unavailable':
            raise OSError('PRIVATE_MARKER /private/path #k=secret')
        if mode[0] == 'missing':
            return 404, b''
        if mode[0] == 'wrong':
            return 200, b'<title>Old revision</title>'
        return 200, (target / ('payload.json' if 'payload.json' in url else 'index.html')).read_bytes()

    first = pipeline.publish_manifest(manifest, root=root, command_runner=fake_encrypt, url_fetcher=fetch)
    assert first['status'] == 'PUBLISHED'
    state_path = root / 'state/github_pages_encrypted_v1/demo/demo-page.json'
    state = json.loads(state_path.read_text())
    key = state['private']['fragment_key']
    manifest['publication_mode'] = 'update_owned'
    mode[0] = change
    if change == 'payload':
        payload = target / 'payload.json'
        payload.write_text(payload.read_text().replace('synthetic', 'corrupted'))
        # Always mutate bytes, regardless of ciphertext encoding.
        payload.write_text(payload.read_text() + ' ')
    if change == 'repository':
        manifest['backend_options']['repository_path'] = str(tmp_path / 'other')
    if change == 'local':
        manifest['backend_options']['verification_mode'] = 'local'
    if change in {'key', 'verification'}:
        if change == 'key':
            state['private']['fragment_key'] = 'wrong'
        else:
            state['verification'] = 'LOCAL_VERIFIED'
        state_path.write_text(json.dumps(state))
    before = snapshot(repo), snapshot(root / 'state'), snapshot(root / 'receipts')
    monkeypatch.setattr(pipeline, '_run_actions', forbid)
    result = pipeline.publish_manifest(manifest, root=root, command_runner=forbid, url_fetcher=fetch)
    assert result['status'] == ('NO_CHANGE' if change == 'none' else 'BLOCKED')
    assert before == (snapshot(repo), snapshot(root / 'state'), snapshot(root / 'receipts'))
    assert json.loads(state_path.read_text())['private']['fragment_key'] == ('wrong' if change == 'key' else key)
    assert key not in json.dumps(result)
    assert str(tmp_path) not in json.dumps(result)


def test_public_projection_and_private_callback(tmp_path, capsys, monkeypatch):
    manifest, _ = _manifest(tmp_path, 'filesystem_static_v1', root=str(tmp_path / 'published'))
    marker = 'PRIVATE_MARKER'
    seen = []

    def action(receipt, config):
        seen.append(dict(receipt))
        return {'private_url': marker, 'status': 'DONE', 'nested': {'path': str(tmp_path)}}

    pipeline.register_downstream_action('private_test', action, replace=True)
    manifest['downstream_actions'] = ['private_test']
    result = pipeline.publish_manifest(manifest, root=tmp_path / 'runtime')
    assert seen[0]['backend_metadata']['target']
    assert result['downstream_actions'] == [{'status': 'DONE'}]
    assert marker not in json.dumps(result)
    assert str(tmp_path) not in json.dumps(result)
    private = next((tmp_path / 'runtime/receipts').rglob('*.json'))
    assert marker in private.read_text()
    poisoned = {**result, 'private_url': marker, 'backend_metadata': {'secret': marker},
                'rollback_ref': str(tmp_path), 'private_receipt_path': str(private),
                'manifest_path': str(tmp_path), 'fragment_key': marker,
                'stable_url': 'https://example.test/page/?token=secret#k=secret'}
    monkeypatch.setattr(pipeline, 'publish_manifest', lambda *args, **kwargs: poisoned)
    assert pipeline.main(['publish', '--manifest', 'unused', '--show-private-url']) == 0
    output = capsys.readouterr().out
    assert marker not in output and str(tmp_path) not in output and 'secret' not in output
    assert json.loads(output)['stable_url'] == 'https://example.test/page/'


@pytest.mark.parametrize('change', ['none', 'route', 'owner', 'dns', 'remote', 'missing'])
def test_tailscale_read_only_current_evidence(tmp_path, monkeypatch, change):
    manifest, _ = _manifest(tmp_path, 'travel_private_v1')
    manifest['owner_module'] = 'travel'
    root = tmp_path / 'runtime'
    target = root / 'sites/travel_private_v1/travel/demo-page/current'
    route = '/travel/demo-page'
    dns = ['host.example.test']
    handlers = {}
    commands = []
    remote_changed = [False]

    def runner(args, cwd, timeout):
        commands.append(args)
        if args == ['tailscale', 'serve', 'status', '--json']:
            return 0, json.dumps({'Web': {f'{dns[0]}:443': {'Handlers': handlers}}})
        if args == ['tailscale', 'status', '--json']:
            return 0, json.dumps({'Self': {'DNSName': dns[0] + '.'}})
        assert args[:2] == ['tailscale', 'serve']
        handlers[route] = {'Path': str(target)}
        return 0, ''

    def fetch(url, timeout):
        if remote_changed[0]:
            return 200, b'<title>Same title, wrong revision</title>'
        name = url.split(route, 1)[1].lstrip('/') or 'index.html'
        return 200, (target / name).read_bytes()

    first = pipeline.publish_manifest(manifest, root=root, command_runner=runner, url_fetcher=fetch)
    assert first['status'] == 'PUBLISHED'
    manifest['publication_mode'] = 'update_owned'
    if change == 'route':
        handlers[route] = {'Path': '/synthetic/other-owner'}
    elif change == 'owner':
        (target / backends._OWNER_FILE).write_text('{}')
    elif change == 'dns':
        dns[0] = 'replacement.example.test'
    elif change == 'remote':
        remote_changed[0] = True
    elif change == 'missing':
        handlers.clear()
    commands.clear()
    before = snapshot(root / 'sites'), snapshot(root / 'state'), snapshot(root / 'receipts')
    monkeypatch.setattr(pipeline, '_run_actions', forbid)
    result = pipeline.publish_manifest(manifest, root=root, command_runner=runner, url_fetcher=fetch)
    assert result['status'] == ('NO_CHANGE' if change == 'none' else 'BLOCKED')
    assert all(cmd in (['tailscale', 'serve', 'status', '--json'], ['tailscale', 'status', '--json']) for cmd in commands)
    assert before == (snapshot(root / 'sites'), snapshot(root / 'state'), snapshot(root / 'receipts'))


def test_encrypted_local_matching_rerun(tmp_path, monkeypatch):
    manifest, _ = _manifest(tmp_path, 'github_pages_encrypted_v1', repository_path=str(tmp_path / 'repo'),
                            verification_mode='local', git_mode='none')
    root = tmp_path / 'runtime'
    assert pipeline.publish_manifest(manifest, root=root, command_runner=fake_encrypt)['status'] == 'PUBLISHED'
    manifest['publication_mode'] = 'update_owned'
    before = snapshot(tmp_path / 'repo'), snapshot(root / 'state'), snapshot(root / 'receipts')
    monkeypatch.setattr(pipeline, '_run_actions', forbid)
    result = pipeline.publish_manifest(manifest, root=root, command_runner=forbid, url_fetcher=forbid)
    assert result['status'] == 'NO_CHANGE'
    assert 'stable_url' not in result  # A file URL would expose the repository path.
    assert before == (snapshot(tmp_path / 'repo'), snapshot(root / 'state'), snapshot(root / 'receipts'))
