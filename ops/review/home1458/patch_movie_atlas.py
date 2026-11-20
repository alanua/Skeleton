"""Apply the Movie Atlas overlay to an isolated Home Android review snapshot."""
import argparse
import hashlib
import os
from pathlib import Path
import stat
import tempfile


MAIN = Path("app/src/main/java/com/skeleton/home/MainActivity.kt")
# Reviewed monitor overlay output. A newer reviewed snapshot needs an explicit
# controller-provided source identity and must still match every edit boundary.
REVIEWED_SHA256 = "fdbfbd108bebeb5bd6a20c4ddd57d658f5ff3152f98db72f6ad4b731a06473a4"

RESPONSE = """        val code = c.responseCode
        val input = if (code >= 400) c.errorStream else c.inputStream
"""
ATLAS_RESPONSE = """        val atlasCapabilities = method == "GET" && path == MOVIE_ATLAS_CAPABILITIES
        if (atlasCapabilities) {
            c.instanceFollowRedirects = false
            c.useCaches = false
            c.setRequestProperty("Cache-Control", "no-cache, no-store")
        }
        val code = c.responseCode
        if (atlasCapabilities && code !in 200..299) {
            c.disconnect()
            throw IllegalStateException("Movie Atlas capabilities unavailable")
        }
        val input = if (code >= 400) c.errorStream else c.inputStream
"""

VIDEO_LIST = """        Header(ui("Відео"),api.server!=null,endpoint.endpointId,endpoints,onEndpoint,onMenu,ambientColor=remember(bm){ambientComplement(bm)}.takeIf{bm!=null},captureCharged=captureCharged,captureEnabled=captureEnabled,onCapture={capturePress()},hyperion=hyperion,onHyperion={onHyperion(!hyperion)})
        LazyColumn(Modifier.weight(1f).padding(horizontal=13.dp),contentPadding=PaddingValues(top=4.dp,bottom=18.dp)){
"""
ATLAS_VIDEO_LIST = VIDEO_LIST.replace(
    "        LazyColumn",
    """        val openMovieAtlas = movieAtlasEntry(
            api, active, endpointId.takeIf { selected -> endpoints.any { it.endpointId == selected } }
        )
        LazyColumn""",
) + """            item {
                OutlinedButton(
                    onClick = openMovieAtlas,
                    modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)
                ) { Text("Кіноатлас") }
            }
"""

ANCHOR = "@Composable private fun SeasonPickerDialog("
UI = '''@Composable
private fun movieAtlasEntry(api: HomeApi, active: Boolean, endpointId: String?): () -> Unit {
    val owner = MovieAtlasContext(api.server, endpointId, active)
    val currentOwner by rememberUpdatedState(owner)
    val pending = remember(api, owner) { mutableStateOf<MovieAtlasRequest?>(null) }
    DisposableEffect(pending) {
        onDispose { pending.value = null }
    }
    val request = pending.value
    if (request != null && owner.active) {
        key(request) {
            MovieAtlasDialog(api, request, {
                request.accepts(pending.value, currentOwner.copy(server = api.server))
            }, {
                if (pending.value === request) pending.value = null
            })
        }
    }
    return {
        if (active && currentOwner == owner && owner.server == api.server) {
            pending.value = MovieAtlasRequest(owner)
        }
    }
}

@Composable
private fun MovieAtlasDialog(
    api: HomeApi,
    request: MovieAtlasRequest,
    isCurrent: () -> Boolean,
    close: () -> Unit
) {
    val current by rememberUpdatedState(isCurrent)
    var checking by remember { mutableStateOf(true) }
    var controller by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(request) {
        val context = request.context
        val server = context.server
        try {
            if (server != null && movieAtlasServer(context) != null && current()) {
                val response = api.json("GET", MOVIE_ATLAS_CAPABILITIES, expectedServer = server)
                val targets = response.optJSONArray("supported_targets")
                val capabilities = mapOf(
                    "schema" to response.opt("schema"),
                    "protocol" to response.opt("protocol"),
                    "supported" to response.opt("supported"),
                    "controller_path" to response.opt("controller_path"),
                    "supported_targets" to targets?.let { array -> List(array.length()) { array.opt(it) } },
                    "presentation_ready" to response.opt("presentation_ready")
                )
                if (current()) controller = movieAtlasControllerUrl(context, capabilities)
            }
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (_: Exception) {
            // The unavailable state also covers missing or unauthorized capability APIs.
        } finally {
            if (current()) checking = false
        }
    }

    Dialog(onDismissRequest = close, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Column(Modifier.fillMaxSize().background(Bg)) {
            TextButton(onClick = close, modifier = Modifier.heightIn(min = 48.dp)) {
                Text("Назад до відео")
            }
            val url = controller
            if (url != null && current()) {
                MovieAtlasWebView(url, { current() }, close) {
                    if (current()) controller = null
                }
            } else {
                Text(
                    if (checking) "Перевіряю доступність Кіноатласу…" else "Кіноатлас недоступний",
                    color = Text,
                    modifier = Modifier.padding(16.dp)
                )
            }
        }
    }
}

@Composable
private fun MovieAtlasWebView(url: String, isCurrent: () -> Boolean, close: () -> Unit, unavailable: () -> Unit) {
    val current by rememberUpdatedState(isCurrent)
    val exit by rememberUpdatedState(close)
    val fail by rememberUpdatedState(unavailable)
    AndroidView(
        modifier = Modifier.fillMaxSize(),
        factory = { context ->
            WebView(context).apply {
                settings.javaScriptEnabled = true
                settings.domStorageEnabled = true
                settings.allowFileAccess = false
                settings.allowContentAccess = false
                settings.allowFileAccessFromFileURLs = false
                settings.allowUniversalAccessFromFileURLs = false
                settings.setSupportMultipleWindows(false)
                settings.javaScriptCanOpenWindowsAutomatically = false
                settings.mixedContentMode = android.webkit.WebSettings.MIXED_CONTENT_NEVER_ALLOW
                android.webkit.CookieManager.getInstance().setAcceptThirdPartyCookies(this, false)
                webViewClient = object : WebViewClient() {
                    override fun shouldOverrideUrlLoading(view: WebView, request: android.webkit.WebResourceRequest): Boolean {
                        if (!current() || !request.isForMainFrame || request.method != "GET") return true
                        return when (movieAtlasNavigation(url, request.url.toString())) {
                            MovieAtlasNavigation.ALLOW -> false
                            MovieAtlasNavigation.EXIT -> { exit(); true }
                            MovieAtlasNavigation.BLOCK -> true
                        }
                    }

                    override fun shouldInterceptRequest(
                        view: WebView, request: android.webkit.WebResourceRequest
                    ): android.webkit.WebResourceResponse? {
                        if (!request.isForMainFrame) return null
                        if (request.method == "GET" &&
                            movieAtlasNavigation(url, request.url.toString()) == MovieAtlasNavigation.ALLOW
                        ) return null
                        return android.webkit.WebResourceResponse(
                            "text/plain", "UTF-8", java.io.ByteArrayInputStream(byteArrayOf())
                        )
                    }

                    override fun onPageStarted(view: WebView, pageUrl: String, favicon: Bitmap?) {
                        if (!current()) {
                            view.stopLoading()
                            return
                        }
                        when (movieAtlasNavigation(url, pageUrl)) {
                            MovieAtlasNavigation.ALLOW -> Unit
                            MovieAtlasNavigation.EXIT -> { view.stopLoading(); exit() }
                            MovieAtlasNavigation.BLOCK -> { view.stopLoading(); fail() }
                        }
                    }

                    override fun onReceivedError(
                        view: WebView, request: android.webkit.WebResourceRequest, error: android.webkit.WebResourceError
                    ) {
                        if (request.isForMainFrame && current()) fail()
                    }

                    override fun onReceivedHttpError(
                        view: WebView, request: android.webkit.WebResourceRequest, response: android.webkit.WebResourceResponse
                    ) {
                        if (request.isForMainFrame && current()) fail()
                    }
                }
                if (current()) loadUrl(url)
            }
        },
        onReset = null,
        onRelease = { view ->
            view.stopLoading()
            view.destroy()
        }
    )
}

'''


def _replace_once(source: str, before: str, after: str) -> str:
    if source.count(before) != 1:
        raise ValueError("missing or ambiguous source boundary: " + before.splitlines()[0])
    return source.replace(before, after, 1)


def _transform(source: str, *, reverse: bool = False) -> str:
    for start, end, before, after in (
        ("private class HomeApi {", "    suspend fun get(path: String)", RESPONSE, ATLAS_RESPONSE),
        ("private fun VideoScreen(", ANCHOR, VIDEO_LIST, ATLAS_VIDEO_LIST),
    ):
        if source.count(start) != 1 or source.count(end) != 1:
            raise ValueError("missing or ambiguous declaration boundary")
        left = source.index(start)
        right = source.index(end)
        if right <= left:
            raise ValueError("misordered declaration boundary")
        block = source[left:right]
        if start == "private class HomeApi {" and block.count("expectedServer: String? = null") != 1:
            raise ValueError("missing or ambiguous HomeApi expected-server boundary")
        old, new = (after, before) if reverse else (before, after)
        source = source[:left] + _replace_once(block, old, new) + source[right:]
    before, after = (UI + ANCHOR, ANCHOR) if reverse else (ANCHOR, UI + ANCHOR)
    return _replace_once(source, before, after)


def apply(root: Path, expected_sha256: str = REVIEWED_SHA256) -> int:
    path = root / MAIN
    if path.resolve() != root.resolve() / MAIN or not path.is_file():
        raise ValueError("expected a regular source file inside the isolated root")
    original = path.read_bytes()
    source = original.decode("utf-8")
    if hashlib.sha256(original).hexdigest() != expected_sha256:
        restored = _transform(source, reverse=True)
        if hashlib.sha256(restored.encode("utf-8")).hexdigest() != expected_sha256:
            raise ValueError("source identity mismatch")
        if _transform(restored) != source:
            raise ValueError("patched source differs from expected result")
        return 0

    if "private fun movieAtlasEntry(" in source or "val atlasCapabilities =" in source:
        raise ValueError("unexpected existing Movie Atlas edits")
    result = _transform(source).encode("utf-8")
    output = tempfile.NamedTemporaryFile(dir=path.parent, delete=False)
    temporary = Path(output.name)
    try:
        with output:
            output.write(result)
        temporary.chmod(stat.S_IMODE(path.stat().st_mode))
        if path.read_bytes() != original:
            raise ValueError("source changed before replacement")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("isolated_source_root", type=Path)
    parser.add_argument("--expected-sha256", default=REVIEWED_SHA256)
    args = parser.parse_args()
    try:
        changed = apply(args.isolated_source_root, args.expected_sha256)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Movie Atlas overlay rejected: {error}\n")
    print(f"changed_files={changed}")
