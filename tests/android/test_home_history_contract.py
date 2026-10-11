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
    assert "LaunchedEffect(api.server,endpoint.endpointId)" in video_screen
    assert "while(true){if(loadHistoryForEndpoint())break;delay(1500)}" in video_screen


def test_history_picker_distinguishes_loading_error_empty_and_ready_states() -> None:
    text = _source()

    assert "var historyHydrated by remember{mutableStateOf(false)}" in text
    assert "var historyLoading by remember{mutableStateOf(false)}" in text
    assert "var historyError by remember{mutableStateOf(\"\")}" in text
    assert "historyLoading&&!historyHydrated->\"Читаю історію…\"" in text
    assert "historyError.isNotBlank()&&history.isEmpty()->\"Не вдалося завантажити · торкніться ще раз\"" in text
    assert "history.isEmpty()&&historyHydrated->\"Історія порожня\"" in text
    assert "api.server==null->\"Home Edge недоступний\"" in text
    assert "val historyActionable=api.server!=null&&!historyLoading&&(history.isNotEmpty()||historyError.isNotBlank())" in text
    assert "HistoryPickerField(historyLabel,historyActionable,historyLoading,historyError.isNotBlank())" in text
    assert "if(history.isNotEmpty())historyOpen=true else scope.launch{loadHistoryForEndpoint()}" in text


def test_home_edge_tv_history_preserves_saved_library_on_partial_failure() -> None:
    text = _source()
    loader = re.search(
        r"suspend fun loadHistoryForEndpoint\(\):Boolean\{(?P<body>.*?)\n    \}",
        text,
        re.DOTALL,
    ).group("body")

    assert 'val watched=runCatching{api.get(mediaHistoryPath(endpoint.endpointId)).optJSONArray("items")?.objects().orEmpty()}' in loader
    assert 'val saved=if(endpoint.adapterKind=="home_edge_tv")runCatching{api.get(savedVideoHistoryPath()).optJSONArray("items")?.objects().orEmpty()} else null' in loader
    assert 'val ok=if(endpoint.adapterKind=="home_edge_tv")saved?.isSuccess==true||watched.isSuccess else watched.isSuccess' in loader
    assert "history=historyItemsAfterRead(history,saved?.getOrNull(),watched.getOrNull(),true,savedHistory)" in loader
    assert "saved?.getOrNull()?.let{savedHistory=canonicalHistoryItems(it)}" in loader
    assert "historyError=(saved?.exceptionOrNull()?:watched.exceptionOrNull())?.message?:\"Історія недоступна\"" in loader
