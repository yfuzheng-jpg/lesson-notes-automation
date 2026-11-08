"""Official Sign in with ChatGPT OAuth flow for this local Windows app.

The flow uses the documented public-client dynamic registration, loopback PKCE
callback, token endpoint and public Responses API. It never reads ChatGPT's UI
session or uses undocumented ChatGPT backend endpoints.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import http.server
import json
import os
import secrets
import tempfile
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any

import jwt
import requests

AUTH_BASE = "https://auth.openai.com"
API_BASE = "https://api.openai.com/v1"
TOKEN_URL = f"{AUTH_BASE}/api/accounts/oauth/token"
AUTHORIZE_URL = f"{AUTH_BASE}/api/accounts/authorize"
JWKS_URL = f"{AUTH_BASE}/.well-known/jwks.json"
KEYRING_SERVICE = "LessonNotesAutomation"
KEYRING_ACCOUNT = "siwc.credentials.v1"
LOCAL_DATA = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
CREDENTIAL_FILE = LOCAL_DATA / "LessonNotesAutomation" / "siwc-credentials.bin"
REQUIRED_SCOPE = "chatgpt.tokens.use.direct"
ISSUER = AUTH_BASE
_refresh_lock = threading.Lock()
_credentials_lock = threading.RLock()


def _keyring():
    try:
        import keyring
    except ImportError as exc:
        raise RuntimeError("Sign in with ChatGPT 需要 keyring 依赖来安全保存授权令牌。") from exc
    return keyring


def _load_vault() -> dict[str, Any]:
    if CREDENTIAL_FILE.exists():
        try:
            value = _dpapi_transform(CREDENTIAL_FILE.read_bytes(), protect=False).decode("utf-8")
            result = json.loads(value)
        except Exception as exc:
            raise RuntimeError(
                "无法解密本机保存的 ChatGPT 授权。请使用创建授权时的同一个 Windows 用户登录；"
                "若此文件已损坏，请联系开发者清除此应用授权后重新登录。"
            ) from exc
        if not isinstance(result, dict):
            raise RuntimeError("本机 ChatGPT 授权文件格式无效，请重新连接 ChatGPT。")
        if result.get("version") == 2:
            if not isinstance(result.get("connections"), dict):
                raise RuntimeError("本机 ChatGPT 连接列表格式无效。")
            return result
        client_id = result.get("client_id")
        if not client_id:
            raise RuntimeError("本机 ChatGPT 授权缺少客户端 ID。")
        return {"version": 2, "active_client_id": client_id, "connections": {client_id: result}}

    # Migrate credentials created by older releases, which stored them in Credential Manager.
    try:
        value = _keyring().get_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
    except Exception:
        return {"version": 2, "active_client_id": "", "connections": {}}
    if not value:
        return {"version": 2, "active_client_id": "", "connections": {}}
    try:
        result = json.loads(value)
        if not isinstance(result, dict):
            return {"version": 2, "active_client_id": "", "connections": {}}
    except json.JSONDecodeError:
        return {"version": 2, "active_client_id": "", "connections": {}}
    client_id = result.get("client_id")
    if not client_id:
        return {"version": 2, "active_client_id": "", "connections": {}}
    vault = {"version": 2, "active_client_id": client_id, "connections": {client_id: result}}
    _write_vault(vault)
    return vault


def load_credentials() -> dict[str, Any] | None:
    with _credentials_lock:
        vault = _load_vault()
        return vault["connections"].get(vault.get("active_client_id", ""))


def list_connections() -> list[dict[str, str]]:
    """Return picker metadata only, never OAuth tokens."""
    with _credentials_lock:
        vault = _load_vault()
        return [
            {"client_id": client_id, "email": str(record.get("email", "")),
             "label": str(record.get("label") or f"连接 {index}")}
            for index, (client_id, record) in enumerate(vault["connections"].items(), 1)
        ]


def select_connection(client_id: str) -> dict[str, Any]:
    with _credentials_lock:
        vault = _load_vault()
        if client_id not in vault["connections"]:
            raise RuntimeError("此 ChatGPT 连接不存在，请重新添加账号 / 工作区。")
        vault["active_client_id"] = client_id
        _write_vault(vault)
        return vault["connections"][client_id]


def rename_connection(client_id: str, label: str) -> None:
    with _credentials_lock:
        vault = _load_vault()
        vault["connections"][client_id]["label"] = label.strip()[:80]
        _write_vault(vault)


def _dpapi_transform(value: bytes, *, protect: bool) -> bytes:
    """Protect or unprotect bytes for the current Windows user with DPAPI."""
    if os.name != "nt":
        raise RuntimeError("ChatGPT 授权凭据的 DPAPI 存储仅支持 Windows。")
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    input_buffer = ctypes.create_string_buffer(value)
    input_blob = DataBlob(len(value), ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_byte)))
    output_blob = DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    if protect:
        function = crypt32.CryptProtectData
        function.argtypes = [
            ctypes.POINTER(DataBlob), wintypes.LPCWSTR, ctypes.POINTER(DataBlob), ctypes.c_void_p,
            ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob),
        ]
        function.restype = wintypes.BOOL
        ok = function(
            ctypes.byref(input_blob), "LessonNotesAutomation ChatGPT credentials", None,
            None, None, 0x1, ctypes.byref(output_blob),
        )
        description = None
    else:
        function = crypt32.CryptUnprotectData
        function.argtypes = [
            ctypes.POINTER(DataBlob), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(DataBlob),
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob),
        ]
        function.restype = wintypes.BOOL
        description = wintypes.LPWSTR()
        ok = function(
            ctypes.byref(input_blob), ctypes.byref(description), None,
            None, None, 0x1, ctypes.byref(output_blob),
        )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        if output_blob.pbData:
            kernel32.LocalFree(ctypes.cast(output_blob.pbData, ctypes.c_void_p))
        if description and description:
            kernel32.LocalFree(ctypes.cast(description, ctypes.c_void_p))


def _write_vault(vault: dict[str, Any]) -> None:
    # DPAPI keeps long rotating OAuth token sets encrypted for this Windows user.
    protected = _dpapi_transform(json.dumps(vault, ensure_ascii=False).encode("utf-8"), protect=True)
    CREDENTIAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix="siwc-", suffix=".tmp", dir=CREDENTIAL_FILE.parent)
    try:
        with os.fdopen(handle, "wb") as temporary:
            temporary.write(protected)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, CREDENTIAL_FILE)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def store_credentials(credentials: dict[str, Any], *, activate: bool = True) -> None:
    with _credentials_lock:
        vault = _load_vault()
        client_id = str(credentials["client_id"])
        previous = vault["connections"].get(client_id, {})
        if previous.get("subject") and previous["subject"] != credentials.get("subject"):
            raise RuntimeError("此客户端 ID 的 ChatGPT 身份与已保存连接不一致。")
        record = dict(credentials)
        record["label"] = previous.get("label") or f"连接 {len(vault['connections']) + 1}"
        vault["connections"][client_id] = record
        if activate:
            vault["active_client_id"] = client_id
        _write_vault(vault)


def forget_credentials() -> None:
    try:
        CREDENTIAL_FILE.unlink(missing_ok=True)
    except OSError:
        pass
    try:
        _keyring().delete_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
    except Exception:
        pass


def _verified_identity(
    id_token: str,
    client_id: str,
    expected_nonce: str | None = None,
    proxies: dict[str, str] | None = None,
) -> dict[str, Any]:
    key_id = jwt.get_unverified_header(id_token).get("kid")
    jwks_response = requests.get(JWKS_URL, timeout=30, proxies=proxies)
    jwks_response.raise_for_status()
    key_data = next((item for item in jwks_response.json().get("keys", []) if item.get("kid") == key_id), None)
    if not key_data:
        raise RuntimeError("无法在 OpenAI 发布的 JWKS 中找到此登录令牌的签名密钥。")
    signing_key = jwt.PyJWK.from_dict(key_data)
    claims = jwt.decode(
        id_token,
        signing_key.key,
        algorithms=["RS256", "ES256"],
        audience=client_id,
        issuer=ISSUER,
        options={"require": ["exp", "iss", "aud", "sub"]},
    )
    if expected_nonce is not None and claims.get("nonce") != expected_nonce:
        raise RuntimeError("ChatGPT 登录回调中的 nonce 校验失败。")
    return claims


def _token_post(data: dict[str, str], proxies: dict[str, str] | None) -> dict[str, Any]:
    response = requests.post(TOKEN_URL, data=data, timeout=45, proxies=proxies)
    if not response.ok:
        raise RuntimeError(f"ChatGPT OAuth 返回 HTTP {response.status_code}: {response.text[:800]}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("ChatGPT OAuth 返回格式异常。")
    return payload


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    result: dict[str, Any] | None = None
    expected_state: str = ""
    callback_path = "/auth/callback"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != self.callback_path:
            self.send_error(404)
            return
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        values = {key: items[0] for key, items in query.items() if items}
        if not secrets.compare_digest(values.get("state", ""), self.expected_state):
            type(self).result = {"error": "登录 state 校验失败，请关闭此页面并重试。"}
            body = "登录未完成。请回到课堂笔记自动整理应用查看提示。"
            self.send_response(400)
        else:
            type(self).result = values
            body = "ChatGPT 授权回调已收到。请返回课堂笔记自动整理应用。"
            self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, _format: str, *_args: Any) -> None:
        # Never log callback URLs; they contain one-time authorization codes.
        return


def authorize(
    host_id: str, proxies: dict[str, str] | None = None, timeout: int = 300,
    *, new_connection: bool = False, cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Open browser authorization, receive a loopback callback and save credentials."""
    # Issued clients are bound to one account/workspace. Adding another must
    # start dynamic registration without carrying over the active login hints.
    old = None if new_connection else load_credentials()
    issued_client_id = str(old.get("client_id", "")) if old else ""
    client_id = issued_client_id or "dynamic_agent_client"
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)

    handler = type(
        "ChatGPTCallbackHandler",
        (_CallbackHandler,),
        {"result": None, "expected_state": state},
    )
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    server.timeout = 0.25
    redirect_uri = f"http://127.0.0.1:{server.server_port}/auth/callback"

    params = {
        "client_id": client_id,
        "ext_agent_host_id": host_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct",
        "resource": API_BASE,
        "state": state,
        "nonce": nonce,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
    }
    if issued_client_id:
        if old and old.get("id_token"):
            params["id_token_hint"] = str(old["id_token"])
        if old and old.get("email"):
            params["login_hint"] = str(old["email"])
    else:
        params["agent_name_hint"] = "课堂笔记自动整理"
    auth_url = AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)
    try:
        if not webbrowser.open(auth_url, new=1, autoraise=True):
            raise RuntimeError("无法打开系统浏览器。请检查 Windows 默认浏览器设置后重试。")
        deadline = time.monotonic() + timeout
        while handler.result is None and time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("已取消 ChatGPT 登录；原有连接已保留。")
            server.handle_request()
        callback = handler.result
    finally:
        server.server_close()
    if callback is None:
        raise RuntimeError(
            "等待 ChatGPT 授权超时。若浏览器提示仅限某工作区，请点击‘添加账号 / 工作区…’重新授权。"
        )
    if callback.get("error"):
        description = callback.get("error_description") or callback.get("error")
        raise RuntimeError(f"ChatGPT 授权未完成：{description}")
    code = callback.get("code", "")
    if not code:
        raise RuntimeError("ChatGPT 回调中没有授权码。")
    returned_client_id = callback.get("client_id") or issued_client_id
    if not returned_client_id or returned_client_id == "dynamic_agent_client":
        raise RuntimeError("未收到已签发的 ChatGPT 客户端 ID，动态注册未完成。")
    if issued_client_id and returned_client_id != issued_client_id:
        raise RuntimeError("回调返回的客户端 ID 与当前授权账户不一致，已拒绝替换凭据。")

    token_payload = _token_post(
        {
            "grant_type": "authorization_code",
            "client_id": returned_client_id,
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
            "resource": API_BASE,
        },
        proxies,
    )
    granted_scopes = str(token_payload.get("scope", callback.get("scope", ""))).split()
    if REQUIRED_SCOPE not in granted_scopes:
        raise RuntimeError("此 ChatGPT 账号未授予 chatgpt.tokens.use.direct 权限，无法使用 ChatGPT 方案请求。")
    id_token = token_payload.get("id_token")
    access_token = token_payload.get("access_token")
    refresh_token = token_payload.get("refresh_token")
    if not id_token or not access_token or not refresh_token:
        raise RuntimeError("授权成功响应缺少 ID、访问或刷新令牌。")
    claims = _verified_identity(str(id_token), returned_client_id, nonce, proxies)
    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("已取消 ChatGPT 登录；原有连接已保留。")
    if old and old.get("subject") and old["subject"] != claims.get("sub"):
        raise RuntimeError("登录的 ChatGPT 账号与已保存的账号不一致。请在 ChatGPT 授权页选择原账号。")
    expires_in = int(token_payload.get("expires_in", 3600))
    credentials = {
        "client_id": returned_client_id,
        "ext_agent_host_id": host_id,
        "subject": claims.get("sub"),
        "email": claims.get("email", old.get("email", "") if old else ""),
        "id_token": id_token,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "scopes": granted_scopes,
        "expires_at": time.time() + expires_in,
        "earliest_refresh_at": token_payload.get("earliest_refresh_at"),
    }
    store_credentials(credentials)
    return credentials


def _refresh_credentials(credentials: dict[str, Any], proxies: dict[str, str] | None) -> dict[str, Any]:
    if not credentials.get("refresh_token"):
        raise RuntimeError("ChatGPT 授权已过期，请重新连接 ChatGPT。")
    payload = _token_post(
        {
            "grant_type": "refresh_token",
            "client_id": str(credentials["client_id"]),
            "refresh_token": str(credentials["refresh_token"]),
            "resource": API_BASE,
        },
        proxies,
    )
    if not payload.get("access_token") or not payload.get("refresh_token"):
        raise RuntimeError("ChatGPT 刷新授权未返回新的访问令牌和轮换刷新令牌。")
    refreshed = dict(credentials)
    refreshed["access_token"] = payload["access_token"]
    refreshed["refresh_token"] = payload["refresh_token"]
    refreshed["expires_at"] = time.time() + int(payload.get("expires_in", 3600))
    refreshed["earliest_refresh_at"] = payload.get("earliest_refresh_at")
    refreshed["scopes"] = str(payload.get("scope", " ".join(credentials.get("scopes", [])))).split()
    if payload.get("id_token"):
        claims = _verified_identity(str(payload["id_token"]), str(credentials["client_id"]), proxies=proxies)
        if claims.get("sub") != credentials.get("subject"):
            raise RuntimeError("刷新后的 ChatGPT 账号标识与当前账号不一致。")
        refreshed["id_token"] = payload["id_token"]
        refreshed["email"] = claims.get("email", refreshed.get("email", ""))
    store_credentials(refreshed, activate=False)
    return refreshed


def get_access_token(proxies: dict[str, str] | None = None) -> tuple[str, dict[str, Any]]:
    with _refresh_lock:
        credentials = load_credentials()
        if not credentials or not credentials.get("access_token"):
            raise RuntimeError("尚未连接 ChatGPT。请先在主界面点击‘Continue with ChatGPT’。")
        if float(credentials.get("expires_at", 0)) <= time.time() + 90:
            credentials = _refresh_credentials(credentials, proxies)
        scopes = credentials.get("scopes", [])
        if REQUIRED_SCOPE not in scopes:
            raise RuntimeError("保存的 ChatGPT 授权不包含 ChatGPT 方案使用权限，请重新连接。")
        return str(credentials["access_token"]), credentials


def list_models(proxies: dict[str, str] | None = None) -> list[tuple[str, str]]:
    token, _ = get_access_token(proxies)
    response = requests.get(f"{API_BASE}/models", headers={"Authorization": f"Bearer {token}"}, timeout=45, proxies=proxies)
    if not response.ok:
        raise RuntimeError(f"读取 ChatGPT 模型列表失败（HTTP {response.status_code}）：{response.text[:800]}")
    payload = response.json()
    models = payload.get("models", [])
    choices: list[tuple[str, str]] = []
    for item in models:
        if isinstance(item, dict) and item.get("slug"):
            slug = str(item["slug"])
            display = str(item.get("display_name") or slug)
            visibility = item.get("visibility")
            if visibility != "list":
                tag = str(visibility) if visibility else "未标记"
                display = f"{display}（接口标记：{tag}）"
            choices.append((display, slug))
    if not choices:
        raise RuntimeError("ChatGPT 模型目录没有返回带模型 ID 的条目。")
    return choices
