from __future__ import annotations

import hashlib
import hmac
import http.client
import re
import ssl
from dataclasses import dataclass
from html.parser import HTMLParser
from http.cookies import SimpleCookie
from urllib.parse import urlencode, urlsplit


class BrotherWBMError(RuntimeError):
    """A redacted, stable error from the Brother Web Based Management adapter."""


@dataclass(frozen=True, slots=True)
class WBMConfig:
    base_url: str
    tls_mode: str = "ca"
    certificate_sha256: str | None = None
    timeout_seconds: float = 5.0
    max_response_bytes: int = 1_048_576

    def __post_init__(self) -> None:
        parsed = urlsplit(self.base_url)
        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise BrotherWBMError("invalid_https_origin")
        if self.tls_mode not in {"ca", "pin", "encrypted_unverified"}:
            raise BrotherWBMError("invalid_tls_mode")
        if self.tls_mode == "pin":
            if self.certificate_sha256 is None or not re.fullmatch(
                r"[0-9a-fA-F]{64}", self.certificate_sha256
            ):
                raise BrotherWBMError("invalid_certificate_pin")
        elif self.certificate_sha256 is not None:
            raise BrotherWBMError("unexpected_certificate_pin")
        if not 0 < self.timeout_seconds <= 30:
            raise BrotherWBMError("invalid_timeout")
        if not 1 <= self.max_response_bytes <= 8_388_608:
            raise BrotherWBMError("invalid_response_limit")

    @property
    def origin(self) -> tuple[str, int]:
        parsed = urlsplit(self.base_url)
        return parsed.hostname or "", parsed.port or 443


class _PageSignals(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.in_title = False
        self.password_fields: list[str] = []
        self.hidden_fields: dict[str, str] = {}
        self.forms: list[dict[str, str]] = []
        self.current_form: dict[str, str] | None = None
        self.logout = False
        self.login_control = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "title":
            self.in_title = True
        if tag.lower() == "form":
            self.current_form = {
                "method": attrs_map.get("method", "get").lower(),
                "action": attrs_map.get("action", ""),
            }
        if tag.lower() == "input":
            name = attrs_map.get("name", "")
            input_type = attrs_map.get("type", "").lower()
            if input_type == "password" and name:
                self.password_fields.append(name)
            if input_type == "hidden" and name:
                self.hidden_fields[name] = attrs_map.get("value", "")
            control = " ".join(
                attrs_map.get(key, "") for key in ("id", "name", "value", "aria-label")
            ).lower()
            if "logout" in control:
                self.logout = True
            if attrs_map.get("id", "").lower() == "login":
                self.login_control = True
        if tag.lower() in {"a", "button"}:
            control = " ".join(
                attrs_map.get(key, "") for key in ("id", "name", "href", "aria-label")
            ).lower()
            if "logout" in control:
                self.logout = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self.in_title = False
        if tag.lower() == "form" and self.current_form is not None:
            self.forms.append(self.current_form)
            self.current_form = None

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)

    @property
    def title(self) -> str:
        return " ".join(" ".join(self.title_parts).split())


class BrotherWBMClient:
    """Bounded same-origin HTTPS session for Brother WBM status authentication."""

    model_title = "Brother MFC-J4540DW"
    protected_path = "/general/print_head_cleaning.html"
    login_error_path = "/etc/loginerror.html"
    status_path = "/home/status.html"

    def __init__(self, config: WBMConfig) -> None:
        self.config = config
        self._connection: http.client.HTTPSConnection | None = None
        self._cookies: dict[str, str] = {}

    def prepare_tls(self) -> None:
        """Establish TLS and finish CA/pin checks before resolving any password."""
        self._connect()

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        self._cookies.clear()

    def __enter__(self) -> BrotherWBMClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _context(self) -> ssl.SSLContext:
        if self.config.tls_mode == "ca":
            context = ssl.create_default_context()
        else:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        return context

    def _connect(self) -> http.client.HTTPSConnection:
        if self._connection is not None and self._connection.sock is not None:
            return self._connection
        host, port = self.config.origin
        connection = http.client.HTTPSConnection(
            host,
            port,
            timeout=self.config.timeout_seconds,
            context=self._context(),
        )
        try:
            connection.connect()
            if self.config.tls_mode == "pin":
                assert self.config.certificate_sha256 is not None
                certificate = connection.sock.getpeercert(binary_form=True)  # type: ignore[union-attr]
                actual = hashlib.sha256(certificate).hexdigest()
                if not hmac.compare_digest(
                    actual.lower(), self.config.certificate_sha256.lower()
                ):
                    raise BrotherWBMError("certificate_pin_mismatch")
        except BrotherWBMError:
            connection.close()
            raise
        except (OSError, ssl.SSLError, http.client.HTTPException):
            connection.close()
            raise BrotherWBMError("tls_connection_failed") from None
        self._connection = connection
        return connection

    @staticmethod
    def _validate_path(path: str) -> None:
        if (
            not path.startswith("/")
            or path.startswith("//")
            or "://" in path
            or "\\" in path
            or any(ord(char) < 0x20 for char in path)
        ):
            raise BrotherWBMError("invalid_same_origin_path")

    def _request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        self._validate_path(path)
        connection = self._connect()
        request_headers = {
            "Accept": "text/html,application/xhtml+xml",
            "Connection": "keep-alive",
        }
        if self._cookies:
            request_headers["Cookie"] = "; ".join(
                f"{key}={value}" for key, value in self._cookies.items()
            )
        if headers:
            request_headers.update(headers)
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            data = response.read(self.config.max_response_bytes + 1)
            if len(data) > self.config.max_response_bytes:
                connection.close()
                self._connection = None
                raise BrotherWBMError("response_too_large")
            for raw_cookie in response.headers.get_all("Set-Cookie", []):
                parsed_cookie = SimpleCookie()
                parsed_cookie.load(raw_cookie)
                for key, morsel in parsed_cookie.items():
                    self._cookies[key] = morsel.value
            response_headers = {
                key.lower(): value for key, value in response.getheaders()
            }
            return response.status, response_headers, data
        except BrotherWBMError:
            raise
        except (OSError, ssl.SSLError, http.client.HTTPException, ValueError):
            self.close()
            raise BrotherWBMError("https_request_failed") from None

    @staticmethod
    def _page(data: bytes) -> _PageSignals:
        try:
            text = data.decode("utf-8", errors="replace")
            page = _PageSignals()
            page.feed(text)
            return page
        except Exception:
            raise BrotherWBMError("invalid_html_response") from None

    def _is_authenticated_status(self, status: int, data: bytes) -> bool:
        if status != 200:
            return False
        page = self._page(data)
        has_login_form = bool(page.password_fields) or any(
            form.get("method") == "post" and form.get("action") == self.status_path
            for form in page.forms
        )
        return (
            page.title == self.model_title
            and page.logout
            and not has_login_form
        )

    def read_authenticated_status(self, password: str) -> dict[str, object]:
        """Authenticate only to the status page; never invoke maintenance actions."""
        try:
            status, headers, data = self._request("GET", self.protected_path)
            if self._is_authenticated_status(status, data):
                return {"authenticated": True, "model": self.model_title}
            if status != 302:
                raise BrotherWBMError("unexpected_protected_page_response")
            location = headers.get("location", "")
            target = urlsplit(location)
            if target.scheme or target.netloc or target.path != self.login_error_path:
                raise BrotherWBMError("unexpected_login_redirect")
            status, _headers, login_page_data = self._request(
                "GET", self.login_error_path + (("?" + target.query) if target.query else "")
            )
            if status != 200:
                raise BrotherWBMError("login_page_unavailable")
            page = self._page(login_page_data)
            if page.title != self.model_title or page.password_fields != ["Baf9"]:
                raise BrotherWBMError("unrecognized_login_form")
            if page.hidden_fields.get("loginurl") != self.protected_path:
                raise BrotherWBMError("unrecognized_login_form")
            if not page.login_control:
                raise BrotherWBMError("unrecognized_login_form")
            if not any(
                form.get("method") == "post" and form.get("action") == self.status_path
                for form in page.forms
            ):
                raise BrotherWBMError("unrecognized_login_form")
            form_body = urlencode(
                {"Baf9": password, "loginurl": self.protected_path}
            ).encode("utf-8")
            post_status, post_headers, _post_body = self._request(
                "POST",
                self.status_path,
                body=form_body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if post_status not in {200, 302, 303}:
                raise BrotherWBMError("authentication_failed")
            if post_status in {302, 303}:
                redirect = urlsplit(post_headers.get("location", ""))
                if redirect.scheme or redirect.netloc or redirect.path != self.status_path:
                    raise BrotherWBMError("unexpected_post_redirect")
            status, _headers, status_page = self._request("GET", self.status_path)
            if not self._is_authenticated_status(status, status_page):
                raise BrotherWBMError("authenticated_status_unverified")
            return {"authenticated": True, "model": self.model_title}
        finally:
            self.close()


def read_brother_status_with_registered_credential(
    *,
    config: WBMConfig,
    authority_environment: dict[str, str],
) -> dict[str, object]:
    """Use the fixed Bitwarden broker binding and return only public status metadata."""
    from integrations.credential_runtime import consume_registered_material_credential

    result: dict[str, object] = {}
    with BrotherWBMClient(config) as client:
        client.prepare_tls()

        def consume(password: str) -> None:
            result.update(client.read_authenticated_status(password))

        receipt = consume_registered_material_credential(
            service_id="brother-mfc-j4540dw",
            alias="wbm-admin",
            action_id="read-authenticated-status",
            consumer=consume,
            authority_environment=authority_environment,
        )
    broker_result = receipt.get("result")
    if not isinstance(broker_result, dict) or broker_result.get("status") != "USED":
        raise BrotherWBMError("credential_broker_use_failed")
    return result
