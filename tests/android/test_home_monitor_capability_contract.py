"""Source contracts only; independent Android compile is still required."""
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[2] / "android/home/app/src/main/java/com/skeleton/home/MainActivity.kt").read_text()


def test_radar_uses_explicit_server_capability_and_tv_type():
    assert 'val monitorAvailable=jobId.isNotBlank()&&isSeries&&monitorSupported' in SOURCE
    assert 'monitorAvailable&&monitorReady' in SOURCE
    assert 'monitorSupported=it.optBoolean("supported",false)' in SOURCE
    assert 'val monitorAvailable=jobId.isNotBlank()&&cat.optInt("tmdb_id")>0' not in SOURCE


def test_lookup_failure_surfaces_server_message():
    lookup = SOURCE.split('suspend fun loadMonitor(){', 1)[1].split('suspend fun applyEndpointSelection', 1)[0]
    assert 'message=it.message?:' in lookup
    assert 'monitorSupported=false' in lookup
    assert 'requestedJobId' in lookup and 'requestedEndpointId' in lookup
    assert 'device_id=${Uri.encode(requestedEndpointId)}' in lookup


def test_mutation_failure_restores_value_and_reports_error():
    mutation = SOURCE.split('VideoToggleControl("radar"', 1)[1].split('VideoToggleControl("autoplay-repeat"', 1)[0]
    assert 'monitor=previous;monitorReady=true;message=it.message?:' in mutation
    assert '.put("device_id",requestedEndpointId)' in mutation
    assert 'api.server!=requestedServer' in mutation
    # HomeApi propagates server error text (including 409); timeout exceptions
    # reach the same UI failure handler without being treated as unsupported.
    assert 'throw IllegalStateException(out.optString("error", "HTTP $code"))' in SOURCE
