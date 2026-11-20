"""Source-only review checks using a synthetic Android snapshot."""
from hashlib import sha256
from pathlib import Path
from subprocess import run
import sys

import pytest

from ops.review.home1458 import patch_movie_atlas as patch


FIXTURE = '''package com.skeleton.home

private fun updater() = "unchanged updater"
private class HomeApi {
    suspend fun json(method: String, path: String, body: JSONObject? = null, expectedServer: String? = null): JSONObject {
        val c = syntheticConnection(method, path, expectedServer)
        val code = c.responseCode
        val input = if (code >= 400) c.errorStream else c.inputStream
        return monitorAwareResponse(code, input)
    }
    suspend fun get(path: String) = json("GET", path)
    suspend fun secrets() = syntheticSecrets()
}
private fun remote() = "unchanged remote"

@Composable
private fun VideoScreen(api: HomeApi, active: Boolean, endpointId: String, endpoints: List<MediaEndpointUi>) {
    val endpoint = endpointById(endpoints, endpointId)
    Column(Modifier.fillMaxSize()) {
        Header(ui("Відео"),api.server!=null,endpoint.endpointId,endpoints,onEndpoint,onMenu,ambientColor=remember(bm){ambientComplement(bm)}.takeIf{bm!=null},captureCharged=captureCharged,captureEnabled=captureEnabled,onCapture={capturePress()},hyperion=hyperion,onHyperion={onHyperion(!hyperion)})
        LazyColumn(Modifier.weight(1f).padding(horizontal=13.dp),contentPadding=PaddingValues(top=4.dp,bottom=18.dp)){
            item {
                EpisodeControls()
                HistoryControls()
                MonitorControls()
            }
        }
    }
}

@Composable private fun SeasonPickerDialog() { SeasonControls() }
private fun profiles() = "unchanged profiles"
'''


def write_fixture(root: Path, source: str = FIXTURE) -> tuple[Path, str]:
    path = root / "app/src/main/java/com/skeleton/home/MainActivity.kt"
    path.parent.mkdir(parents=True)
    original = source.encode("utf-8")
    path.write_bytes(original)
    return path, sha256(original).hexdigest()


def test_entry_and_dialog_preserve_every_unrelated_byte(tmp_path):
    path, digest = write_fixture(tmp_path)
    path.chmod(0o640)
    assert patch.apply(tmp_path, digest) == 1
    source = path.read_text(encoding="utf-8")
    head, tail = source.split("private fun VideoScreen(", 1)
    screen, dialog = tail.split("@Composable\nprivate fun movieAtlasEntry(", 1)
    assert 'method == "GET" && path == MOVIE_ATLAS_CAPABILITIES' in head
    assert "c.instanceFollowRedirects = false" in head
    assert "c.useCaches = false" in head
    assert "atlasCapabilities && code !in 200..299" in head
    assert 'Text("Кіноатлас")' in screen
    assert "onClick = openMovieAtlas" in screen
    assert screen.index("val openMovieAtlas = movieAtlasEntry(") < screen.index("LazyColumn(")
    assert "api, active, endpointId.takeIf { selected -> endpoints.any { it.endpointId == selected } }" in screen
    assert "heightIn(min = 48.dp)" in screen
    assert 'api.json("GET", MOVIE_ATLAS_CAPABILITIES, expectedServer = server)' in dialog
    assert "movieAtlasServer(context) != null && current()" in dialog
    assert "if (current()) controller = movieAtlasControllerUrl(context, capabilities)" in dialog
    assert "request.accepts(pending.value, currentOwner.copy(server = api.server))" in dialog
    assert "remember(api, owner)" in dialog
    assert "onDispose { pending.value = null }" in dialog
    assert "Dialog(onDismissRequest = close" in dialog
    assert 'Text("Назад до відео")' in dialog
    assert '"Кіноатлас недоступний"' in dialog
    assert "settings.allowFileAccess = false" in dialog
    assert "settings.allowContentAccess = false" in dialog
    assert "settings.setSupportMultipleWindows(false)" in dialog
    assert 'request.method != "GET"' in dialog
    assert "MovieAtlasNavigation.EXIT -> { exit(); true }" in dialog
    assert "MovieAtlasNavigation.BLOCK -> true" in dialog
    assert "onRelease = { view ->\n            view.stopLoading()\n            view.destroy()" in dialog
    for forbidden in ("addJavascriptInterface", "SharpIrBridge", "onReceivedSslError", "api.post(", "Intent("):
        assert forbidden not in dialog
    # Strip exactly the three added fragments and compare against the original fixture.
    prefix, remainder = source.split("        val atlasCapabilities =", 1)
    _, remainder = remainder.split("        val code = c.responseCode\n", 1)
    _, remainder = remainder.split("        val input = ", 1)
    source = prefix + "        val code = c.responseCode\n        val input = " + remainder
    prefix, remainder = source.split("        val openMovieAtlas = ", 1)
    _, remainder = remainder.split("        LazyColumn(", 1)
    list_header, remainder = remainder.split("            item {", 1)
    _, remainder = remainder.split("            item {", 1)
    source = prefix + "        LazyColumn(" + list_header + "            item {" + remainder
    prefix, remainder = source.split("@Composable\nprivate fun movieAtlasEntry(", 1)
    _, remainder = remainder.split("@Composable private fun SeasonPickerDialog(", 1)
    assert (prefix + "@Composable private fun SeasonPickerDialog(" + remainder).encode() == FIXTURE.encode()
    assert path.stat().st_mode & 0o777 == 0o640


def test_reapplication_is_a_noop_and_requires_the_original_identity(tmp_path):
    path, digest = write_fixture(tmp_path)
    assert patch.apply(tmp_path, digest) == 1
    patched = path.read_bytes()
    inode = path.stat().st_ino
    assert patch.apply(tmp_path, digest) == 0
    assert path.read_bytes() == patched
    assert path.stat().st_ino == inode

    for edited in (patched.replace(b"unchanged profiles", b"changed profiles"),
                   patched.replace(b"MovieAtlasNavigation.BLOCK -> true", b"MovieAtlasNavigation.BLOCK -> false")):
        path.write_bytes(edited)
        with pytest.raises(ValueError):
            patch.apply(tmp_path, digest)
        assert path.read_bytes() == edited


@pytest.mark.parametrize("edit", [
    lambda source: source.replace("        LazyColumn(", "         LazyColumn("),
    lambda source: source.replace("        val code = c.responseCode", "        val code=c.responseCode"),
    lambda source: source.replace("expectedServer: String? = null", "serverHint: String? = null"),
    lambda source: source.replace("    Column(Modifier.fillMaxSize()) {", "    Column(Modifier.fillMaxSize()) {" +
                                 source.split("    Column(Modifier.fillMaxSize()) {", 1)[1].split("            item {", 1)[0]),
    lambda source: source.replace("@Composable private fun SeasonPickerDialog(", "@Composable private fun OtherDialog("),
    lambda source: source + "\nprivate fun movieAtlasEntry() {}\n",
])
def test_even_pinned_drifted_boundaries_fail_without_writing(tmp_path, edit):
    path, digest = write_fixture(tmp_path, edit(FIXTURE))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="boundary|unexpected existing"):
        patch.apply(tmp_path, digest)
    assert path.read_bytes() == before
    assert list(path.parent.iterdir()) == [path]


def test_wrong_identity_and_rename_failure_leave_original_intact(tmp_path, monkeypatch):
    path, digest = write_fixture(tmp_path)
    with pytest.raises(ValueError):
        patch.apply(tmp_path)
    assert path.read_bytes() == FIXTURE.encode()

    def fail_rename(source, destination):
        assert destination == path
        assert source.read_bytes() != path.read_bytes()
        raise OSError("simulated rename failure")

    monkeypatch.setattr(patch.os, "replace", fail_rename)
    with pytest.raises(OSError, match="simulated rename failure"):
        patch.apply(tmp_path, digest)
    assert path.read_bytes() == FIXTURE.encode()
    assert list(path.parent.iterdir()) == [path]


def test_cli_reports_change_then_noop(tmp_path):
    path, digest = write_fixture(tmp_path)
    cmd = [sys.executable, str(Path(patch.__file__).resolve()), str(tmp_path), "--expected-sha256", digest]
    assert run(cmd, capture_output=True, text=True, check=True).stdout == "changed_files=1\n"
    assert run(cmd, capture_output=True, text=True, check=True).stdout == "changed_files=0\n"
    assert path.read_bytes() != FIXTURE.encode()
