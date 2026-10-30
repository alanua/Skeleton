from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MAIN_ACTIVITY = ROOT / "android/home/app/src/main/java/com/skeleton/home/MainActivity.kt"


def _source() -> str:
    return MAIN_ACTIVITY.read_text(encoding="utf-8")


def test_history_hydration_is_separate_from_selected_job_loading() -> None:
    text = _source()
    video_screen = text.split("private fun VideoScreen", 1)[1]
    load_all = re.search(
        r"suspend fun loadAll\(\):Boolean\{(?P<body>.*?)\n    \}",
        video_screen,
        re.DOTALL,
    ).group("body")

    assert 'runCatching{endpointVideoSelection(api,endpoint.endpointId)}' in load_all
    assert 'api.get(endpointPath(endpoint.endpointId,"/player"))' in load_all
    assert "loadHistoryForEndpoint" not in load_all
    assert "return sel.isSuccess" in load_all
    assert "LaunchedEffect(api.server,endpoint.endpointId,active,historyRevision)" in video_screen
    assert "if(api.server!=null&&active)loadHistoryForEndpoint()" in video_screen


def test_history_picker_distinguishes_loading_error_empty_and_ready_states() -> None:
    text = _source()

    assert "val historyByEndpoint=remember{mutableStateMapOf<String,List<JSONObject>>()}" in text
    assert "val savedHistoryByEndpoint=remember{mutableStateMapOf<String,List<JSONObject>>()}" in text
    assert "val historyHydratedByEndpoint=remember{mutableStateMapOf<String,Boolean>()}" in text
    assert "var historyHydrated by remember(endpoint.endpointId){mutableStateOf(historyHydratedByEndpoint[endpoint.endpointId] == true)}" in text
    assert "var historyLoading by remember(endpoint.endpointId){mutableStateOf(false)}" in text
    assert "var historyError by remember(endpoint.endpointId){mutableStateOf(historyErrorByEndpoint[endpoint.endpointId].orEmpty())}" in text
    assert "var historyOpen by remember(endpoint.endpointId){mutableStateOf(false)}" in text
    assert "var selectedHistoryItem by remember(endpoint.endpointId){mutableStateOf<JSONObject?>(null)}" in text
    assert "historyLoading&&!historyHydrated->\"Читаю історію…\"" in text
    assert "historyError.isNotBlank()&&history.isEmpty()->\"Не вдалося завантажити · торкніться ще раз\"" in text
    assert "history.isEmpty()&&historyHydrated->\"Історія порожня\"" in text
    assert "api.server==null->\"Home Edge недоступний\"" in text
    assert "val historyActionable=api.server!=null&&!historyLoading" in text
    assert "HistoryPickerField(historyLabel,historyActionable,historyLoading,historyError.isNotBlank())" in text
    assert "if(loadHistoryForEndpoint()&&history.isNotEmpty())historyOpen=true" in text


def test_home_edge_tv_history_preserves_saved_library_on_partial_failure() -> None:
    text = _source()
    loader = re.search(
        r"suspend fun loadHistoryForEndpoint\(\):Boolean\{(?P<body>.*?)\n    \}",
        text,
        re.DOTALL,
    ).group("body")

    assert 'val watched=runCatching{api.get(mediaHistoryPath(endpoint.endpointId)).optJSONArray("items")?.objects().orEmpty()}' in loader
    assert 'val saved=runCatching{savedVideoHistoryItemsWithRetry(api)}' in loader
    assert 'setEndpointSavedHistory(catalog)' in loader
    assert 'setEndpointHistory(historyItemsAfterRead(history,catalog,watched.getOrNull(),endpoint.adapterKind=="home_edge_tv"))' in loader
    assert 'return saved.isSuccess' in loader
    assert 'saved.exceptionOrNull()?.message' in loader



def test_home_edge_tv_saved_history_is_retried_before_fallback_error() -> None:
    text = _source()
    retry = re.search(
        r"private suspend fun savedVideoHistoryItemsWithRetry\(api:HomeApi,attempts:Int=2\):List<JSONObject>\{(?P<body>.*?)\n\}",
        text,
        re.DOTALL,
    ).group("body")

    assert "repeat(attempts.coerceAtLeast(1)){i->" in retry
    assert "api.get(savedVideoHistoryPath()).optJSONArray(\"items\")?.objects().orEmpty()" in retry
    assert "result.onSuccess{return it}" in retry
    assert "if(i<attempts-1)delay(650)" in retry
    assert "throw lastError ?: IllegalStateException(\"Історія недоступна\")" in retry


def test_editor_return_and_tab_entry_refresh_without_state_feedback_loop() -> None:
    text = _source()
    assert "HistoryEditorDialog(api,endpointId){historyEditor=false;historyRevision++}" in text
    assert "VideoScreen(api, tab==1&&!historyEditor, historyRevision," in text
    effect = text.split("LaunchedEffect(api.server,endpoint.endpointId,active,historyRevision){", 1)[1].split("\n    }", 1)[0]
    assert effect.count("loadHistoryForEndpoint()") == 1
    assert "if(api.server!=null&&active)" in effect
    assert "while" not in effect
    assert "delay" not in effect


def test_only_canonical_catalog_get_bypasses_http_cache() -> None:
    text = _source()
    policy = text.split('if (method == "GET" && path == savedVideoHistoryPath()) {', 1)[1].split("}", 1)[0]
    assert "c.useCaches = false" in policy
    assert 'c.setRequestProperty("Cache-Control", "no-cache, no-store")' in policy
    assert 'home-poster-cache-v1' in text
    assert 'remember(items){visibleHistoryItems(items)}' in text
    assert 'JSONObject(it.toString()).apply{remove("hidden")}' in text
