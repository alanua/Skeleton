import json
from scripts.brother_scan_session_assembler import main


def test_explicit_start_cli_is_public_safe(tmp_path, capsys):
    event = tmp_path / 'event.json'
    event.write_text(json.dumps(dict(session_id='synthetic', scanner_id='scanner', profile_id='profile', event_id='start', kind='start', sheet_count=1)))
    assert main(['--state-db', str(tmp_path / 'state.sqlite'), '--output-directory', str(tmp_path / 'output'), '--event', str(event)]) == 0
    assert json.loads(capsys.readouterr().out) == {'status': 'OPEN', 'reason': None}
    assert not (tmp_path / 'output').exists()