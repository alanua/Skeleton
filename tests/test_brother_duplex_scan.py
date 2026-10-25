from pathlib import Path


def test_shell_routes_to_canonical_producer():
    script = Path(__file__).parents[1] / 'scripts' / 'brother-duplex-scan'
    result = subprocess.run(['bash', str(script), '--help'], capture_output=True, text=True)
    assert result.returncode == 0
    assert '--event' in result.stdout
    assert '--state-db' in result.stdout