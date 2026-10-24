from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAIN = ROOT / 'android/home/app/src/main/java/com/skeleton/home/MainActivity.kt'


def test_cached_tap_opens_before_scheduling_network():
    source = MAIN.read_text()
    tap = source.split('val action=historyCache.open', 1)[1].split('\n                    }', 1)[0]
    assert 'if(action.showDialog)historyOpen=true' in tap
    assert tap.index('historyOpen=true') < tap.index('historyRefresh++')
    assert 'scope.launch' not in tap
    assert 'val historyActionable=api.server!=null\n' in source


def test_refresh_is_bounded_cancellable_and_not_an_infinite_retry():
    source = MAIN.read_text()
    loader = source.split('suspend fun loadHistoryForEndpoint(){', 1)[1].split('suspend fun confirmEndpointSelection', 1)[0]
    assert 'withTimeout(15_000L)' in loader
    assert loader.count('currentCoroutineContext().ensureActive()') == 2
    assert 'historyCache.begin()?:return' in loader
    assert 'historyCache.finish(token)' in loader
    assert 'catch(e:CancellationException){throw e}' in loader
    assert 'while(true){if(loadHistoryForEndpoint())' not in source
    assert 'LaunchedEffect(api.server,endpoint.endpointId,historyRefresh)' in source
    assert 'finally{historyCache.cancel()}' in source


def test_shared_catalog_and_lazy_posters():
    source = MAIN.read_text()
    assert 'savedHistoryByEndpoint' not in source
    assert 'val historyCache=remember(api.server){NativeHistoryCache()}' in source
    assert 'if(inflight!=token)return false' in source
    assert 'if(saved!=null){library=canonicalHistoryItems(saved)' in source
    assert 'watchedHistoryForSavedLibrary(savedBase,it)' in source
    assert 'progressByKey[historyKey(item)]' in source
    dialog = source.split('@Composable private fun HistoryPickerDialog', 1)[1].split('@Composable private fun HistoryPickerRow', 1)[0]
    assert 'LazyColumn' in dialog
    assert 'remoteBitmap' not in dialog
    assert 'filter{!it.optBoolean("hidden",false)}' in dialog


def test_kotlin_behavior_regressions_are_present():
    tests = (ROOT / 'android/home/app/src/test/java/com/skeleton/home/NativeVideoHistoryTest.kt').read_text()
    for name in ('hydratedPickerOpensSynchronouslyAndRefreshesOnlyWhenStale',
                 'initialErrorAndEmptyCacheCanRetryAndCancelledEndpointCannotPublish',
                 'sharedMembershipAndPostersPreserveSeparateDeviceProgress',
                 'successfulSavedReadIsAuthoritativeOverStaleWatchedHistory',
                 'initialTapOpensWhenCatalogArrivesWithoutWaitingForProgress'):
        assert f'fun {name}()' in tests


def test_initial_tap_publishes_dialog_before_watched_progress_read():
    source = MAIN.read_text()
    loader = source.split('suspend fun loadHistoryForEndpoint(){', 1)[1].split('suspend fun confirmEndpointSelection', 1)[0]
    assert loader.index('historyPickerOpensAfterRefresh(historyOpenRequested,') < loader.index('val watched=')
    assert 'historyOpenRequested=!action.showDialog' in source
    assert 'var historyOpenRequested by remember(api.server,endpoint.endpointId)' in source


def test_cached_open_uses_same_visibility_rule_as_initial_refresh():
    source = MAIN.read_text()
    assert 'HistoryPickerOpen(historyPickerOpensAfterRefresh(true,items(endpoint)),needsRefresh(endpoint,now))' in source
    tests = (ROOT / 'android/home/app/src/test/java/com/skeleton/home/NativeVideoHistoryTest.kt').read_text()
    assert 'fun hiddenOnlyCacheDoesNotOpenAndFailedRefreshPreservesProgress()' in tests


def test_cached_history_never_shows_refresh_spinner_or_auto_refetches_on_revisit():
    source = MAIN.read_text()
    assert 'HistoryPickerField(historyLabel,historyActionable,historyLoading&&!historyHydrated,historyError.isNotBlank())' in source
    assert 'val historyActionable=api.server!=null' in source
    assert 'private val automaticAttempted=mutableSetOf<String>()' in source
    assert 'fun claimAutomaticRefresh(endpoint:String):Boolean=automaticAttempted.add(endpoint)' in source
    assert 'val initial=historyCache.claimAutomaticRefresh(endpoint.endpointId)' in source
    assert 'if(initial||historyRefresh>0)' in source
    assert 'LaunchedEffect(api.server,endpoint.endpointId,historyRefresh)' in source
    assert 'while(true){if(loadHistoryForEndpoint())' not in source
