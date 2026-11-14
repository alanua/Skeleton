"""One G1 session owner with an optional, offline Chromium rendering adapter.

Browser networking is disabled. All navigations pass through bounded pinned HTTPS;
scripted interactive rendering requires the separately reviewed runtime slice.
"""
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import os
import stat
import time
import uuid
from urllib.parse import urljoin

from core.operator_attention import WebActionClass
from core.secret_store import SecretReference
from core.web_acquisition import Fetcher, WebExecutionError
from core.web_session import SyntheticWebSessionAdapter, WebSessionStatus, _origin_for_url

PRIVATE_ROOT = Path('/var/lib/skeleton/private/web')


def _private_directory():
    # Deployment provisions the fixed root. Never follow a symlink or accept task paths.
    for path in (PRIVATE_ROOT, *PRIVATE_ROOT.parents):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise WebExecutionError('PRIVATE_ROOT_UNSAFE')
    info = PRIVATE_ROOT.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise WebExecutionError('PRIVATE_ROOT_UNSAFE')
    directory = PRIVATE_ROOT / uuid.uuid4().hex
    directory.mkdir(mode=0o700)
    return directory


class PlaywrightDOM:
    """Optional sync Playwright backend; no evaluate, task flags or native network."""
    def __init__(self):
        self.driver = self.context = None
        try:
            directory = _private_directory()
            from playwright.sync_api import sync_playwright
            self.driver = sync_playwright().start()
            self.context = self.driver.chromium.launch_persistent_context(
                str(directory), headless=True, java_script_enabled=False,
                chromium_sandbox=True, accept_downloads=False, service_workers='block', offline=True,
                timeout=10_000,
            )
            self.context.route('**/*', lambda route: route.abort())
            self.context.set_default_timeout(5_000)
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        except Exception:
            try:
                self.close()
            except Exception:
                pass
            raise WebExecutionError('BROWSER_RUNTIME_UNAVAILABLE') from None

    def load(self, document):
        self.page.set_content(document, wait_until='domcontentloaded', timeout=5_000)

    def read(self):
        # The acquisition byte cap bounds the source; DOM-generated content is disabled.
        return self.page.locator('body').inner_text(timeout=5_000)

    def href(self, selector):
        element = self.page.locator('a').and_(self.page.locator(selector))
        if element.count() != 1:
            raise WebExecutionError('NON_LINK_CLICK_UNSUPPORTED')
        # Restrict click to an explicit link; never dispatch a page click or submit form.
        return element.get_attribute('href', timeout=5_000)

    def type(self, selector, value):
        self.page.locator(selector).fill(value, timeout=5_000)

    def select(self, selector, value):
        self.page.locator(selector).select_option(value, timeout=5_000)

    def screenshot(self):
        return self.page.screenshot(type='png', full_page=False, timeout=5_000)

    def close(self):
        try:
            if self.context is not None:
                self.context.close()
        finally:
            if self.driver is not None:
                self.driver.stop()


class BrowserAdapter(SyntheticWebSessionAdapter):
    """Reuse G1 attention lifecycle and exact binding; backend injection is host-only.

    External mutations are unsupported. Page text cannot grant operation authority.
    All returned text, URLs, screenshots and state are PRIVATE runtime data.
    """
    provider_id = 'bounded-browser'

    def __init__(self, *, backend_factory=PlaywrightDOM, fetcher=None):
        super().__init__()
        self.factory = backend_factory
        self.fetcher = fetcher or Fetcher()
        self.contexts = {}
        self.artifacts = {}
        self.budgets = {}
        self.secret_references = {}

    def create(self, *, session_id, url, effect, secret_reference=None):
        self._effect(effect, WebActionClass.PRIVATE_SESSION_MUTATION)
        if secret_reference is not None and not isinstance(secret_reference, SecretReference):
            raise WebExecutionError('OPAQUE_SECRET_REFERENCE_REQUIRED')
        if len(self.contexts) >= 4 or len(self._sessions) >= 32:
            raise WebExecutionError('SESSION_LIMIT')
        if session_id in self._sessions:
            raise WebExecutionError('WEB_SESSION_ALREADY_EXISTS')
        # Validate G1 binding and fetch before allocating browser resources.
        state = super().open_session(session_id=session_id, url=url)
        backend = None
        try:
            evidence = self.fetcher.fetch(url)
            if _origin_for_url(evidence.final_url) != state.binding.origin:
                raise WebExecutionError('CROSS_ORIGIN_NAVIGATION')
            backend = self.factory()
            backend.load(evidence.body.decode('utf-8', errors='replace'))
            self.contexts[session_id] = backend
            self.budgets[session_id] = 1
            self.secret_references[session_id] = secret_reference
            self._sessions[session_id] = replace(state, current_url=evidence.final_url)
            return self.state(session_id)
        except Exception as exc:
            self._sessions.pop(session_id, None)
            if backend is not None:
                backend.close()
            self._error(exc)

    def open_session(self, *, session_id, url, effect, secret_reference=None):
        return self.create(session_id=session_id, url=url, effect=effect,
                           secret_reference=secret_reference)

    def require_challenge(self, **kwargs):
        kwargs.setdefault('created_at', datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
        session_id = kwargs.get('session_id')
        state = self.state(session_id)
        if state.status is WebSessionStatus.RESUMED:
            self._sessions[session_id] = replace(state, status=WebSessionStatus.OPEN)
        try:
            return super().require_challenge(**kwargs)
        except Exception:
            self._sessions[session_id] = state
            raise

    def status(self, session_id, *, effect):
        self._effect(effect, WebActionClass.READ_ONLY)
        return self.state(session_id)

    def _ready(self, session_id):
        state = self.state(session_id)
        if state.status not in (WebSessionStatus.OPEN, WebSessionStatus.RESUMED):
            raise WebExecutionError('SESSION_NOT_READY')
        if self.budgets[session_id] >= 64:
            raise WebExecutionError('SESSION_OPERATION_LIMIT')
        self.budgets[session_id] += 1
        return state, self.contexts[session_id]

    @staticmethod
    def _effect(actual, required):
        if actual is not required:
            raise WebExecutionError('EFFECT_NOT_AUTHORIZED')

    @staticmethod
    def _error(exc):
        if isinstance(exc, WebExecutionError):
            raise exc
        raise WebExecutionError('BROWSER_OPERATION_FAILED') from None

    def navigate(self, session_id, url, *, effect):
        self._effect(effect, WebActionClass.READ_ONLY)
        state, backend = self._ready(session_id)
        try:
            if _origin_for_url(url) != state.binding.origin:
                raise WebExecutionError('CROSS_ORIGIN_NAVIGATION')
            evidence = self.fetcher.fetch(url)
            if _origin_for_url(evidence.final_url) != state.binding.origin:
                raise WebExecutionError('CROSS_ORIGIN_NAVIGATION')
            backend.load(evidence.body.decode('utf-8', errors='replace'))
            self._sessions[session_id] = replace(state, current_url=evidence.final_url)
            return evidence
        except Exception as exc:
            self._error(exc)

    def read(self, session_id, *, effect):
        self._effect(effect, WebActionClass.READ_ONLY)
        _, backend = self._ready(session_id)
        try:
            return backend.read()[:self.fetcher.limits.text_chars]
        except Exception as exc:
            self._error(exc)

    def click(self, session_id, selector, *, effect):
        self._effect(effect, WebActionClass.READ_ONLY)
        state, backend = self._ready(session_id)
        self._input(selector, 256)
        try:
            href = backend.href(selector)
            if not href:
                raise WebExecutionError('NON_LINK_CLICK_UNSUPPORTED')
            return self.navigate(session_id, urljoin(state.current_url, href), effect=effect)
        except Exception as exc:
            self._error(exc)

    def _edit(self, session_id, selector, value, effect, operation):
        self._effect(effect, WebActionClass.PRIVATE_SESSION_MUTATION)
        _, backend = self._ready(session_id)
        self._input(selector, 256)
        self._input(value, 4096)
        try:
            getattr(backend, operation)(selector, value)
        except Exception as exc:
            self._error(exc)

    def type(self, session_id, selector, value, *, effect):
        self._edit(session_id, selector, value, effect, 'type')

    def select(self, session_id, selector, value, *, effect):
        self._edit(session_id, selector, value, effect, 'select')

    @staticmethod
    def _input(value, cap):
        if not isinstance(value, str) or len(value) > cap:
            raise WebExecutionError('INVALID_INPUT')

    def wait(self, session_id, seconds, *, effect):
        self._effect(effect, WebActionClass.READ_ONLY)
        self._ready(session_id)
        if type(seconds) not in (int, float) or not 0 <= seconds <= 1:
            raise WebExecutionError('INVALID_WAIT')
        time.sleep(seconds)

    def screenshot(self, session_id, *, effect):
        self._effect(effect, WebActionClass.READ_ONLY)
        _, backend = self._ready(session_id)
        if sum(key[0] == session_id for key in self.artifacts) >= 4:
            raise WebExecutionError('ARTIFACT_LIMIT')
        try:
            data = backend.screenshot()
            if len(data) > 2_000_000:
                raise WebExecutionError('ARTIFACT_TOO_LARGE')
            reference = 'private:' + uuid.uuid4().hex
            self.artifacts[(session_id, reference)] = data
            return reference
        except Exception as exc:
            self._error(exc)

    def close(self, session_id, *, effect):
        self._effect(effect, WebActionClass.PRIVATE_SESSION_MUTATION)
        state = self.state(session_id)
        try:
            backend = self.contexts.pop(session_id, None)
            if backend is not None:
                backend.close()
        except Exception as exc:
            self._error(exc)
        finally:
            self._sessions[session_id] = replace(state, status=WebSessionStatus.CLOSED, pending_challenge=None)
            self.artifacts = {key: value for key, value in self.artifacts.items() if key[0] != session_id}
            self.secret_references.pop(session_id, None)
