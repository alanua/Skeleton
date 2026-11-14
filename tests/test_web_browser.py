"""In-process DOM contract fixtures, no browser installation or real network."""
import pytest

from core.operator_attention import (AttentionStatus, OperatorAttentionResolution,
                                     WebActionClass)
from core.web_acquisition import Fetcher, Limits, Response, WebExecutionError
from core.web_browser import BrowserAdapter, PlaywrightDOM
from core.web_session import WebSessionStatus

READ = WebActionClass.READ_ONLY
PRIVATE = WebActionClass.PRIVATE_SESSION_MUTATION


class DOM:
    def __init__(self):
        self.document = ''
        self.values = {}
        self.closed = False

    def load(self, document):
        self.document = document

    def read(self):
        return self.document

    def href(self, selector):
        return '/ad/123?view=detail' if selector == '#detail' else None

    def type(self, selector, value):
        self.values[selector] = value

    select = type

    def screenshot(self):
        return b'synthetic-png'

    def close(self):
        self.closed = True


def adapter():
    f = Fetcher(transport=lambda *args: Response(200, {'content-type': 'text/html'}, b'<p>synthetic</p>'),
                resolver=lambda *args: ('93.184.216.34',))
    return BrowserAdapter(backend_factory=DOM, fetcher=f)


def create(browser, session='sess:one'):
    return browser.create(session_id=session, url='https://fixture.test/ad/123', effect=PRIVATE)


def test_isolation_operations_and_cleanup():
    browser = adapter()
    create(browser)
    create(browser, 'sess:two')
    browser.type('sess:one', '#query', 'private', effect=PRIVATE)
    browser.select('sess:one', '#choice', 'a', effect=PRIVATE)
    assert browser.contexts['sess:two'].values == {}
    assert browser.read('sess:one', effect=READ) == '<p>synthetic</p>'
    evidence = browser.click('sess:one', '#detail', effect=READ)
    assert evidence.final_url.endswith('?view=detail')
    browser.wait('sess:one', 0, effect=READ)
    ref = browser.screenshot('sess:one', effect=READ)
    assert ref.startswith('private:')
    assert ('sess:two', ref) not in browser.artifacts
    context = browser.contexts['sess:one']
    browser.close('sess:one', effect=PRIVATE)
    assert context.closed and not browser.artifacts
    assert browser.status('sess:one', effect=READ).status is WebSessionStatus.CLOSED
    assert not browser.contexts['sess:two'].closed


def test_pause_retains_exact_context_and_resumes():
    browser = adapter()
    state = create(browser)
    context = browser.contexts['sess:one']
    browser.type('sess:one', '#query', 'preserved', effect=PRIVATE)
    request = browser.require_captcha(session_id='sess:one', challenge_id='captcha:one',
                                      created_at='2026-11-14T00:00:00Z')
    with pytest.raises(WebExecutionError, match='SESSION_NOT_READY'):
        browser.read('sess:one', effect=READ)
    for status in (AttentionStatus.DELIVERED, AttentionStatus.OPENED, AttentionStatus.IN_PROGRESS):
        browser.attention.advance(request.attention_id, status, now='2026-11-14T00:00:01Z')
    resolution = OperatorAttentionResolution(request.attention_id, 'completed', 'operator:local',
                                             '2026-11-14T00:00:02Z', request.resume_token)
    resumed = browser.resume_after_attention(binding=state.binding, resolution=resolution)
    assert resumed.binding == state.binding
    assert browser.contexts['sess:one'] is context
    assert context.values['#query'] == 'preserved'
    browser.read('sess:one', effect=READ)


@pytest.mark.parametrize('effect', [WebActionClass.EXTERNAL_MUTATION, WebActionClass.HIGH_IMPACT, 'read_only'])
def test_effect_cannot_authorize_submit(effect):
    browser = adapter()
    create(browser)
    with pytest.raises(WebExecutionError, match='EFFECT_NOT_AUTHORIZED'):
        browser.click('sess:one', '#submit', effect=effect)


def test_no_form_click_or_raw_credentials_or_unbounded_wait():
    browser = adapter()
    create(browser)
    with pytest.raises(WebExecutionError, match='NON_LINK_CLICK_UNSUPPORTED'):
        browser.click('sess:one', '#submit', effect=READ)
    with pytest.raises(WebExecutionError, match='OPAQUE_SECRET_REFERENCE_REQUIRED'):
        browser.create(session_id='sess:bad', url='https://fixture.test', effect=PRIVATE,
                       secret_reference='plaintext')
    with pytest.raises(WebExecutionError, match='INVALID_WAIT'):
        browser.wait('sess:one', 100, effect=READ)
    with pytest.raises(WebExecutionError, match='CROSS_ORIGIN_NAVIGATION'):
        browser.navigate('sess:one', 'https://other.test/ad/123', effect=READ)


def test_artifact_and_session_limits():
    browser = adapter()
    create(browser)
    for _ in range(4):
        browser.screenshot('sess:one', effect=READ)
    with pytest.raises(WebExecutionError, match='ARTIFACT_LIMIT'):
        browser.screenshot('sess:one', effect=READ)
    for name in ('sess:two', 'sess:three', 'sess:four'):
        create(browser, name)
    with pytest.raises(WebExecutionError, match='SESSION_LIMIT'):
        create(browser, 'sess:five')


def test_backend_errors_sanitized_and_create_rolled_back():
    browser = adapter()
    def fail():
        raise RuntimeError('private secret')
    browser.factory = fail
    with pytest.raises(WebExecutionError, match='^BROWSER_OPERATION_FAILED$'):
        create(browser)
    assert not browser._sessions and not browser.contexts


def test_runtime_is_optional_and_never_installed(monkeypatch):
    import core.web_browser as module
    def unavailable():
        raise OSError('private path')
    monkeypatch.setattr(module, '_private_directory', unavailable)
    with pytest.raises(WebExecutionError, match='^BROWSER_RUNTIME_UNAVAILABLE$'):
        PlaywrightDOM()


def test_optional_playwright_configuration_with_in_process_driver(monkeypatch):
    import sys
    import types
    import core.web_browser as module
    calls = {}
    class Page:
        def set_content(self, document, **kwargs):
            calls['document'] = document
        def screenshot(self, **kwargs):
            return b'fixture'
    class Context:
        pages = [Page()]
        def route(self, pattern, handler):
            calls['route'] = (pattern, handler)
        def set_default_timeout(self, timeout):
            calls['timeout'] = timeout
        def close(self):
            calls['context_closed'] = True
    class Chromium:
        def launch_persistent_context(self, directory, **kwargs):
            calls['directory'], calls['options'] = directory, kwargs
            return Context()
    class Driver:
        chromium = Chromium()
        def start(self):
            return self
        def stop(self):
            calls['driver_closed'] = True
    monkeypatch.setattr(module, '_private_directory', lambda: module.PRIVATE_ROOT / 'synthetic')
    monkeypatch.setitem(sys.modules, 'playwright', types.ModuleType('playwright'))
    monkeypatch.setitem(sys.modules, 'playwright.sync_api', types.SimpleNamespace(sync_playwright=Driver))
    backend = PlaywrightDOM()
    assert calls['options']['offline'] is True
    assert calls['options']['java_script_enabled'] is False
    assert calls['options']['chromium_sandbox'] is True
    assert calls['options']['service_workers'] == 'block'
    assert calls['options']['accept_downloads'] is False
    assert calls['directory'].startswith('/var/lib/skeleton/private/web/')
    class Route:
        def abort(self):
            calls['aborted'] = True
    assert calls['route'][0] == '**/*'
    calls['route'][1](Route())
    assert calls['aborted']
    backend.load('<p>in-process fixture</p>')
    assert backend.screenshot() == b'fixture'
    backend.close()
    assert calls['context_closed'] and calls['driver_closed']


def test_pause_after_resume_and_session_operation_limit():
    browser = adapter()
    state = create(browser)
    browser._sessions['sess:one'] = __import__('dataclasses').replace(state, status=WebSessionStatus.RESUMED)
    context = browser.contexts['sess:one']
    browser.require_captcha(session_id='sess:one', challenge_id='captcha:second',
                            created_at='2026-11-14T00:00:00Z')
    assert browser.contexts['sess:one'] is context
    browser._sessions['sess:one'] = state
    browser.budgets['sess:one'] = 64
    with pytest.raises(WebExecutionError, match='SESSION_OPERATION_LIMIT'):
        browser.read('sess:one', effect=READ)
