#!/usr/bin/env python3
"""
独立绑卡工具（CLI 版）：
1. 扫描 codex_tokens/*.json
2. 交互选择账号
3. 按 config.json 执行 checkout -> m.stripe.com/6 -> confirm
4. 输出详细后台日志
"""

from __future__ import annotations

import base64
import json
import random
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from curl_cffi import requests as curl_requests

from app.account_store import AccountStore

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config.json"
CHATGPT_BASE = "https://chatgpt.com"
STRIPE_API = "https://api.stripe.com"
STRIPE_METRICS_URL = "https://m.stripe.com/6"
ACCOUNT_STORE = AccountStore()
DEFAULT_PAYMENT_PLAN_NAME = "chatgptteamplan"
DEFAULT_PAYMENT_WORKSPACE_NAME = "Artizancloud"
DEFAULT_PAYMENT_PRICE_INTERVAL = "month"
DEFAULT_PAYMENT_SEAT_QUANTITY = 5
DEFAULT_PAYMENT_PROMO_CAMPAIGN_ID = "team-1-month-free"
DEFAULT_PAYMENT_CANCEL_URL = "https://chatgpt.com/?promo_campaign=team1dollar#team-pricing"
DEFAULT_PAYMENT_CHECKOUT_UI_MODE = "custom"
DEFAULT_PAYMENT_REFERRER = "https://chatgpt.com/"


def load_config() -> dict[str, Any]:
    defaults = {
        "proxy": "http://127.0.0.1:7890",
        "payment_country": "US",
        "payment_user_agent_override": "",
        "payment_time_on_page_min_ms": 15000,
        "payment_time_on_page_max_ms": 45000,
        "payment_retry_enabled": True,
        "payment_retry_max_attempts": 3,
        "payment_retry_interval_ms": 2000,
        "payment_expected_amount": "0",
    }
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                defaults.update(data)
    return defaults


def mask_card(number: str) -> str:
    digits = re.sub(r"\D+", "", str(number or ""))
    if len(digits) < 8:
        return digits
    return f"{digits[:6]}{'*' * max(0, len(digits) - 10)}{digits[-4:]}"


def flatten_form_data(data: Any, prefix: str = "") -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    if isinstance(data, dict):
        for key, value in data.items():
            new_prefix = f"{prefix}[{key}]" if prefix else str(key)
            pairs.extend(flatten_form_data(value, new_prefix))
        return pairs
    if isinstance(data, (list, tuple)):
        for item in data:
            pairs.extend(flatten_form_data(item, f"{prefix}[]"))
        return pairs
    pairs.append((prefix, "" if data is None else str(data)))
    return pairs


def _account_dirs() -> list[Path]:
    cfg = load_config()
    raw_dir = str(cfg.get("token_json_dir") or "").strip()
    candidates: list[Path] = []
    if raw_dir:
        path = Path(raw_dir)
        if not path.is_absolute():
            path = BASE_DIR / path
        candidates.append(path)
    candidates.append(BASE_DIR / "data" / "codex_tokens")
    candidates.append(BASE_DIR / "codex_tokens")
    unique: list[Path] = []
    seen: set[str] = set()
    for item in candidates:
        key = str(item.resolve()) if item.exists() else str(item)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique



def _load_account_file(email: str) -> dict[str, Any]:
    for directory in _account_dirs():
        path = directory / f"{email}.json"
        if not path.exists():
            continue
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    raise FileNotFoundError(f"账号不存在: {email}")



def _has_checkout_context(account: dict[str, Any]) -> bool:
    cookies = account.get("cookies") if isinstance(account.get("cookies"), dict) else {}
    return bool(str(account.get("session_token") or "").strip()) and bool(cookies)



def _merge_account_sources(primary: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    merged = dict(fallback)
    merged.update(primary)
    for key in ("session_token", "csrf_token", "device_id", "user_agent", "sec_ch_ua", "access_token", "refresh_token", "id_token", "account_id", "expired", "last_refresh", "password"):
        if not str(merged.get(key) or "").strip() and str(fallback.get(key) or "").strip():
            merged[key] = fallback.get(key)
    fallback_cookies = fallback.get("cookies") if isinstance(fallback.get("cookies"), dict) else {}
    primary_cookies = primary.get("cookies") if isinstance(primary.get("cookies"), dict) else {}
    merged["cookies"] = primary_cookies or fallback_cookies
    return merged



def list_accounts() -> list[dict[str, Any]]:
    rows = ACCOUNT_STORE.list_accounts()
    if rows:
        return rows
    results: list[dict[str, Any]] = []
    for directory in _account_dirs():
        if not directory.exists():
            continue
        for path in sorted([p for p in directory.glob("*.json") if p.is_file()], key=lambda x: x.name.lower()):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    results.append(data)
            except Exception:
                continue
        if results:
            break
    return results



def load_account(email: str) -> dict[str, Any]:
    sqlite_account = ACCOUNT_STORE.get_account(email)
    file_account: dict[str, Any] | None = None
    try:
        file_account = _load_account_file(email)
    except Exception:
        file_account = None

    if sqlite_account and file_account:
        merged = _merge_account_sources(sqlite_account, file_account)
        if _has_checkout_context(merged):
            return merged
        return merged
    if sqlite_account:
        return sqlite_account
    if file_account:
        return file_account
    raise FileNotFoundError(f"账号不存在: {email}")


def choose_account_email() -> str:
    accounts = list_accounts()
    if not accounts:
        raise RuntimeError("未找到可用账号，请先注册或导入账号")
    print("\n可用账号列表：")
    for idx, account in enumerate(accounts, start=1):
        email = str(account.get("email") or "")
        expired = str(account.get("expired") or "")
        account_id = str(account.get("account_id") or "")
        status = "bind-ready" if _has_checkout_context(account) else "missing-session"
        print(f"[{idx}] {email} | account_id={account_id[:8]}... | expired={expired} | {status}")
    while True:
        raw = input("\n请选择账号编号: ").strip()
        if raw.isdigit():
            index = int(raw)
            if 1 <= index <= len(accounts):
                return str(accounts[index - 1].get("email") or "")
        print("输入无效，请重新输入。")


class PaymentBinder:
    def __init__(self, config: dict[str, Any], account: dict[str, Any]):
        self.config = config
        self.account = account
        self.session = curl_requests.Session(impersonate="chrome136")
        self.stripe_session = curl_requests.Session(impersonate="chrome136")
        self.proxy = str(config.get("proxy") or "").strip()
        if self.proxy:
            self.session.proxies = {"http": self.proxy, "https": self.proxy}
            self.stripe_session.proxies = {"http": self.proxy, "https": self.proxy}

        self.device_id = str(account.get("device_id") or account.get("oai_device_id") or uuid.uuid4())
        self.ua = str(
            account.get("user_agent")
            or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.7103.93 Safari/537.36"
        )
        self.sec_ch_ua = str(
            account.get("sec_ch_ua")
            or '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"'
        )
        self.stripe_js_id = str(uuid.uuid4())
        self.payment_user_agent = str(
            config.get("payment_user_agent_override")
            or "stripe.js/f197c9c0f0; stripe-js-v3/f197c9c0f0; checkout"
        )
        min_ms = max(1000, int(config.get("payment_time_on_page_min_ms", 15000) or 15000))
        max_ms = max(min_ms, int(config.get("payment_time_on_page_max_ms", 45000) or 45000))
        self.time_on_page = random.randint(min_ms, max_ms)
        self.stripe_publishable_key = ""

        self._bootstrap_cookies()

    def log(self, message: str) -> None:
        print(f"[bind] {message}")

    def _seed_cookie(self, name: str, value: str) -> None:
        cookie_name = str(name or "").strip()
        cookie_value = str(value or "").strip()
        if not cookie_name or not cookie_value:
            return
        if cookie_name in {"__Secure-next-auth.session-token", "__Host-next-auth.csrf-token", "_account", "oai-hlib", "cf_clearance", "__cf_bm", "_cf_uvid", "_cfuvid", "__cflb", "oai-did"}:
            self.session.cookies.set(cookie_name, cookie_value, domain="chatgpt.com")
            self.session.cookies.set(cookie_name, cookie_value, domain=".chatgpt.com")
        if cookie_name in {"login_session", "auth_provider", "hydra_redirect", "oai-client-auth-session", "auth-session-minimized", "oai-did"}:
            self.session.cookies.set(cookie_name, cookie_value, domain="auth.openai.com")
            self.session.cookies.set(cookie_name, cookie_value, domain=".auth.openai.com")
        self.session.cookies.set(cookie_name, cookie_value)

    def _bootstrap_cookies(self) -> None:
        cookies = self.account.get("cookies") or {}
        if isinstance(cookies, dict):
            for name, value in cookies.items():
                self._seed_cookie(str(name), str(value))
        session_token = str(self.account.get("session_token") or "")
        csrf_token = str(self.account.get("csrf_token") or "")
        if session_token:
            self._seed_cookie("__Secure-next-auth.session-token", session_token)
        if csrf_token:
            self._seed_cookie("__Host-next-auth.csrf-token", csrf_token)
        self._seed_cookie("oai-did", self.device_id)
        self._bootstrap_stripe_session()

    def _bootstrap_stripe_session(self) -> None:
        self.stripe_session.headers.update({
            "User-Agent": self.ua,
            "Accept": "application/json, text/plain, */*",
        })

    def _fresh_recovery_session(self):
        recovery = curl_requests.Session(impersonate="chrome136")
        if self.proxy:
            recovery.proxies = {"http": self.proxy, "https": self.proxy}
        carry_cookie_names = {
            "cf_clearance", "__cf_bm", "_cf_uvid", "_cfuvid", "__cflb",
            "oai-did", "__Secure-next-auth.session-token", "__Host-next-auth.csrf-token",
            "_account", "oai-hlib",
        }
        try:
            for cookie in self.session.cookies.jar:
                name = str(getattr(cookie, "name", "") or "")
                value = str(getattr(cookie, "value", "") or "")
                domain = str(getattr(cookie, "domain", "") or "")
                if not name or not value or name not in carry_cookie_names:
                    continue
                if "chatgpt.com" in domain:
                    recovery.cookies.set(name, value, domain=domain or ".chatgpt.com")
                elif name in {"cf_clearance", "__cf_bm", "_cf_uvid", "_cfuvid", "__cflb", "oai-did"}:
                    recovery.cookies.set(name, value, domain=domain or ".chatgpt.com")
        except Exception:
            pass
        recovery.cookies.set("oai-did", self.device_id, domain="chatgpt.com")
        recovery.cookies.set("oai-did", self.device_id, domain=".chatgpt.com")
        recovery.cookies.set("oai-did", self.device_id, domain="auth.openai.com")
        recovery.cookies.set("oai-did", self.device_id, domain=".auth.openai.com")
        return recovery

    def _cookie(self, name: str) -> str:
        try:
            for cookie in self.session.cookies.jar:
                if getattr(cookie, "name", "") == name:
                    return str(getattr(cookie, "value", "") or "")
        except Exception:
            pass
        try:
            return str(self.session.cookies.get(name) or "")
        except Exception:
            return ""

    def _trace_headers(self) -> dict[str, str]:
        trace_id = random.randint(10**17, 10**18 - 1)
        parent_id = random.randint(10**17, 10**18 - 1)
        return {
            "traceparent": f"00-{uuid.uuid4().hex}-{format(parent_id, '016x')}-01",
            "tracestate": "dd=s:1;o:rum",
            "x-datadog-origin": "rum",
            "x-datadog-sampling-priority": "1",
            "x-datadog-trace-id": str(trace_id),
            "x-datadog-parent-id": str(parent_id),
        }

    def _sentinel_token(self) -> str:
        req_body = json.dumps({"p": "", "id": self.device_id, "flow": "authorize_continue"})
        resp = self.session.post(
            "https://sentinel.openai.com/backend-api/sentinel/req",
            data=req_body,
            headers={
                "Content-Type": "text/plain;charset=UTF-8",
                "Referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html",
                "Origin": "https://sentinel.openai.com",
                "User-Agent": self.ua,
                "sec-ch-ua": self.sec_ch_ua,
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
            },
            timeout=20,
            impersonate="chrome136",
        )
        if resp.status_code != 200:
            raise RuntimeError(f"sentinel 请求失败: {resp.status_code} {resp.text[:180]}")
        data = resp.json()
        token = str(data.get("token") or "")
        if not token:
            raise RuntimeError("sentinel 响应缺少 token")
        return json.dumps({"p": "", "t": "", "c": token, "id": self.device_id, "flow": "authorize_continue"}, separators=(",", ":"))

    def _extract_code(self, url: str) -> str:
        if not url or "code=" not in url:
            return ""
        try:
            from urllib.parse import parse_qs, urlparse
            return str(parse_qs(urlparse(url).query).get("code", [""])[0] or "")
        except Exception:
            return ""

    def _decode_auth_session(self) -> dict[str, Any] | None:
        for cookie in self.session.cookies.jar:
            if getattr(cookie, "name", "") != "oai-client-auth-session":
                continue
            value = str(getattr(cookie, "value", "") or "")
            first_part = value.split(".")[0] if "." in value else value
            pad = 4 - len(first_part) % 4
            if pad != 4:
                first_part += "=" * pad
            try:
                raw = json.loads(base64.urlsafe_b64decode(first_part).decode("utf-8"))
                if isinstance(raw, dict):
                    return raw
            except Exception:
                continue
        return None

    def _follow_redirect_for_code(self, url: str, referer: str | None = None, depth: int = 10) -> str:
        current = str(url or "").strip()
        current_referer = str(referer or "").strip()
        for _ in range(max(1, depth)):
            if not current:
                return ""
            try:
                headers = {
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "User-Agent": self.ua,
                    "Upgrade-Insecure-Requests": "1",
                }
                if current_referer:
                    headers["Referer"] = current_referer
                resp = self.session.get(
                    current,
                    headers=headers,
                    allow_redirects=False,
                    timeout=20,
                    impersonate="chrome136",
                )
            except Exception as exc:
                match = re.search(r'(https?://localhost[^\s\'"]+)', str(exc))
                if match:
                    return self._extract_code(match.group(1))
                return ""
            code = self._extract_code(str(resp.url or ""))
            if code:
                return code
            if resp.status_code not in (301, 302, 303, 307, 308):
                return ""
            location = str(resp.headers.get("Location") or "").strip()
            code = self._extract_code(location)
            if code:
                return code
            if location.startswith("/"):
                location = f"https://auth.openai.com{location}"
            current_referer = current
            current = location
        return ""

    def _recover_session_via_signin_openai(self) -> tuple[str, str]:
        email = str(self.account.get("email") or "").strip()
        password = str(self.account.get("password") or "").strip()
        if not email or not password:
            raise RuntimeError("Pay 阶段独立补 session 需要账号 email/password")

        self.log("检测到缺少 session_token，开始在 Pay 阶段独立补 next-auth 会话")
        recovery_session = self._fresh_recovery_session()
        old_session = self.session
        self.session = recovery_session
        try:
            csrf_resp = self.session.get(
            f"{CHATGPT_BASE}/api/auth/csrf",
            headers={
                "Accept": "application/json",
                "Referer": f"{CHATGPT_BASE}/auth/login",
                "User-Agent": self.ua,
            },
            timeout=20,
            impersonate="chrome136",
        )
            if csrf_resp.status_code != 200:
                raise RuntimeError(f"Pay 阶段获取 csrf 失败: {csrf_resp.status_code} {csrf_resp.text[:180]}")
            csrf_data = csrf_resp.json() if csrf_resp.content else {}
            csrf_token = str(csrf_data.get("csrfToken") or "")
            if not csrf_token:
                raise RuntimeError("Pay 阶段 csrf 响应缺少 csrfToken")

            signin_resp = self.session.post(
                f"{CHATGPT_BASE}/api/auth/signin/openai",
                data={
                    "csrfToken": csrf_token,
                    "callbackUrl": f"{CHATGPT_BASE}/",
                    "json": "true",
                },
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": CHATGPT_BASE,
                    "Referer": f"{CHATGPT_BASE}/auth/login",
                    "User-Agent": self.ua,
                },
                timeout=20,
                impersonate="chrome136",
            )
            if signin_resp.status_code != 200:
                raise RuntimeError(f"Pay 阶段 signin/openai 失败: {signin_resp.status_code} {signin_resp.text[:180]}")
            signin_data = signin_resp.json() if signin_resp.content else {}
            auth_url = str(signin_data.get("url") or "").strip()
            if not auth_url:
                raise RuntimeError("Pay 阶段 signin/openai 未返回授权链接")

            auth_resp = self.session.get(
                auth_url,
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Referer": f"{CHATGPT_BASE}/auth/login",
                    "Upgrade-Insecure-Requests": "1",
                    "User-Agent": self.ua,
                },
                allow_redirects=True,
                timeout=30,
                impersonate="chrome136",
            )
            final_auth_url = str(auth_resp.url or "")
            self.log(f"Pay 阶段 auth_url GET: status={auth_resp.status_code} final_url={final_auth_url}")

            authorize_headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Origin": "https://auth.openai.com",
                "Referer": final_auth_url if final_auth_url.startswith("https://auth.openai.com") else "https://auth.openai.com/log-in",
                "User-Agent": self.ua,
                "oai-device-id": self.device_id,
                "openai-sentinel-token": self._sentinel_token(),
            }
            authorize_headers.update(self._trace_headers())
            authorize_resp = self.session.post(
                "https://auth.openai.com/api/accounts/authorize/continue",
                json={"username": {"kind": "email", "value": email}},
                headers=authorize_headers,
                timeout=30,
                impersonate="chrome136",
            )
            if authorize_resp.status_code != 200:
                raise RuntimeError(f"Pay 阶段 authorize/continue 失败: {authorize_resp.status_code} {authorize_resp.text[:200]}")

            password_headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Origin": "https://auth.openai.com",
                "Referer": "https://auth.openai.com/log-in/password",
                "User-Agent": self.ua,
                "oai-device-id": self.device_id,
                "openai-sentinel-token": self._sentinel_token(),
            }
            password_headers.update(self._trace_headers())
            password_resp = self.session.post(
                "https://auth.openai.com/api/accounts/password/verify",
                json={"password": password},
                headers=password_headers,
                timeout=30,
                impersonate="chrome136",
            )
            if password_resp.status_code != 200:
                raise RuntimeError(f"Pay 阶段 password/verify 失败: {password_resp.status_code} {password_resp.text[:200]}")

            password_data = password_resp.json() if password_resp.content else {}
            page_type = str((password_data.get("page") or {}).get("type") or "")
            continue_url = str(password_data.get("continue_url") or "").strip()
            if page_type == "email_otp_verification" or "email-verification" in continue_url:
                raise RuntimeError("Pay 阶段补 session 命中邮箱 OTP，当前独立绑卡流程暂不支持")
            if continue_url.startswith("/"):
                continue_url = f"https://auth.openai.com{continue_url}"
            if not continue_url:
                raise RuntimeError("Pay 阶段补 session 未获取到 continue_url")

            code = self._follow_redirect_for_code(continue_url, referer="https://auth.openai.com/log-in/password")
            if not code:
                session_data = self._decode_auth_session() or {}
                workspaces = session_data.get("workspaces") or []
                workspace_id = str((workspaces[0] or {}).get("id") or "") if workspaces else ""
                if workspace_id:
                    workspace_headers = {
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                        "Origin": "https://auth.openai.com",
                        "Referer": continue_url,
                        "User-Agent": self.ua,
                        "oai-device-id": self.device_id,
                    }
                    workspace_headers.update(self._trace_headers())
                    ws_resp = self.session.post(
                        "https://auth.openai.com/api/accounts/workspace/select",
                        json={"workspace_id": workspace_id},
                        headers=workspace_headers,
                        timeout=30,
                        allow_redirects=False,
                        impersonate="chrome136",
                    )
                    if ws_resp.status_code in (301, 302, 303, 307, 308):
                        code = self._extract_code(str(ws_resp.headers.get("Location") or "")) or self._follow_redirect_for_code(str(ws_resp.headers.get("Location") or ""), referer=continue_url)
                    elif ws_resp.status_code == 200:
                        ws_data = ws_resp.json() if ws_resp.content else {}
                        orgs = ((ws_data.get("data") or {}).get("orgs") or [])
                        ws_next = str(ws_data.get("continue_url") or "").strip()
                        org_id = str((orgs[0] or {}).get("id") or "") if orgs else ""
                        project_id = ""
                        if orgs:
                            projects = (orgs[0] or {}).get("projects") or []
                            project_id = str((projects[0] or {}).get("id") or "") if projects else ""
                        if org_id:
                            org_headers = {
                                "Accept": "application/json",
                                "Content-Type": "application/json",
                                "Origin": "https://auth.openai.com",
                                "Referer": ws_next if ws_next.startswith("http") else f"https://auth.openai.com{ws_next or ''}",
                                "User-Agent": self.ua,
                                "oai-device-id": self.device_id,
                            }
                            org_headers.update(self._trace_headers())
                            body = {"org_id": org_id}
                            if project_id:
                                body["project_id"] = project_id
                            org_resp = self.session.post(
                                "https://auth.openai.com/api/accounts/organization/select",
                                json=body,
                                headers=org_headers,
                                timeout=30,
                                allow_redirects=False,
                                impersonate="chrome136",
                            )
                            location = str(org_resp.headers.get("Location") or "")
                            if org_resp.status_code in (301, 302, 303, 307, 308):
                                code = self._extract_code(location) or self._follow_redirect_for_code(location, referer=org_headers["Referer"])
                            elif org_resp.status_code == 200:
                                org_data = org_resp.json() if org_resp.content else {}
                                org_next = str(org_data.get("continue_url") or "").strip()
                                if org_next.startswith("/"):
                                    org_next = f"https://auth.openai.com{org_next}"
                                code = self._follow_redirect_for_code(org_next, referer=org_headers["Referer"])
                        elif ws_next:
                            if ws_next.startswith("/"):
                                ws_next = f"https://auth.openai.com{ws_next}"
                            code = self._follow_redirect_for_code(ws_next, referer=continue_url)

            if not code:
                raise RuntimeError(f"Pay 阶段未能提取 callback code: continue_url={continue_url}")

            state = ""
            try:
                from urllib.parse import parse_qs, urlparse
                state = str(parse_qs(urlparse(auth_url).query).get("state", [""])[0] or "")
            except Exception:
                state = ""
            if not state:
                raise RuntimeError("Pay 阶段 signin/openai 返回的 auth_url 缺少 state")

            callback_url = f"{CHATGPT_BASE}/api/auth/callback/openai?code={code}&state={state}"
            callback_resp = self.session.get(
                callback_url,
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Referer": "https://auth.openai.com/",
                    "Upgrade-Insecure-Requests": "1",
                    "User-Agent": self.ua,
                },
                allow_redirects=True,
                timeout=30,
                impersonate="chrome136",
            )
            final_url = str(callback_resp.url or "")
            session_token = self._cookie("__Secure-next-auth.session-token")
            csrf_cookie = self._cookie("__Host-next-auth.csrf-token") or csrf_token
            self.log(f"Pay 阶段会话补链完成: callback_final_url={final_url}, session_token={'yes' if session_token else 'no'}")
            if not session_token:
                raise RuntimeError(f"Pay 阶段未能建立 session_token: final_url={final_url}")
            self.account["session_token"] = session_token
            self.account["csrf_token"] = csrf_cookie
            self.account["cookies"] = {str(getattr(c, 'name', '') or ''): str(getattr(c, 'value', '') or '') for c in self.session.cookies.jar if str(getattr(c, 'name', '') or '')}
            return session_token, csrf_cookie
        finally:
            if self._cookie("__Secure-next-auth.session-token"):
                old_session = self.session
            else:
                self.session = old_session

    def _payment_context(self) -> dict[str, str]:
        access_token = str(self.account.get("access_token") or "").strip()
        session_token = self._cookie("__Secure-next-auth.session-token")
        csrf_token = self._cookie("__Host-next-auth.csrf-token")
        if not access_token:
            raise RuntimeError("账号文件缺少 access_token")
        if not session_token:
            session_token, csrf_token = self._recover_session_via_signin_openai()
        return {
            "access_token": access_token,
            "session_token": session_token,
            "csrf_token": csrf_token,
            "sentinel_token": self._sentinel_token(),
        }

    def _extract_publishable_key(self, payload: Any) -> str:
        found = set()

        def walk(value: Any):
            if isinstance(value, dict):
                for k, v in value.items():
                    key_text = str(k or "").lower()
                    if key_text in {"publishable_key", "publishablekey", "stripe_publishable_key", "key"}:
                        if isinstance(v, str) and v.startswith(("pk_live_", "pk_test_")):
                            found.add(v)
                    walk(v)
                return
            if isinstance(value, list):
                for item in value:
                    walk(item)
                return
            if isinstance(value, str):
                for match in re.findall(r"pk_(?:live|test)_[A-Za-z0-9_]+", value):
                    found.add(match)

        walk(payload)
        if found:
            return sorted(found, key=len, reverse=True)[0]
        return ""

    def _extract_expected_amount(self, payload: Any) -> Optional[int]:
        manual_value = str(self.config.get("payment_expected_amount") or "").strip()
        if manual_value.isdigit() and int(manual_value) >= 0:
            return int(manual_value)

        candidates: list[int] = []

        def record_number(value: Any):
            try:
                if isinstance(value, bool):
                    return
                if isinstance(value, (int, float)):
                    num = int(value)
                elif isinstance(value, str) and value.strip().isdigit():
                    num = int(value.strip())
                else:
                    return
                if num > 0:
                    candidates.append(num)
            except Exception:
                return

        def walk(value: Any):
            if isinstance(value, dict):
                for k, v in value.items():
                    key_text = str(k or "").lower()
                    if key_text in {
                        "expected_amount", "amount_total", "amount_due", "total_amount", "amount",
                        "total", "subtotal", "grand_total", "unit_amount", "total_details_amount"
                    }:
                        record_number(v)
                    walk(v)
                return
            if isinstance(value, list):
                for item in value:
                    walk(item)
                return

        walk(payload)
        if candidates:
            return max(candidates)
        return None

    def _stripe_api_headers(self, *, content_type: str = "application/x-www-form-urlencoded") -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": content_type,
            "Origin": "https://js.stripe.com",
            "Referer": "https://js.stripe.com/",
            "User-Agent": self.ua,
            "Accept-Language": "en-US,en;q=0.9",
            "sec-ch-ua": self.sec_ch_ua,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        }

    def _normalize_checkout_session_id(self, payload: dict[str, Any]) -> str:
        checkout_session_id = str(
            payload.get("checkout_session_id")
            or payload.get("session_id")
            or payload.get("id")
            or ""
        ).strip()
        if checkout_session_id:
            return checkout_session_id
        checkout_url = str(payload.get("checkout_url") or "")
        match = re.search(r"/(cs_(?:live|test)_[A-Za-z0-9_]+)", checkout_url)
        if match:
            return match.group(1)
        return ""

    def init_stripe_hosted_page(self, checkout_session_id: str, publishable_key: str) -> dict[str, Any]:
        stripe_version = "2025-03-31.basil; checkout_server_update_beta=v1; checkout_manual_approval_preview=v1"
        data = {
            "browser_locale": "zh-CN",
            "browser_timezone": "Asia/Tokyo",
            "elements_session_client[client_betas][0]": "custom_checkout_server_updates_1",
            "elements_session_client[client_betas][1]": "custom_checkout_manual_approval_1",
            "elements_session_client[elements_init_source]": "custom_checkout",
            "elements_session_client[referrer_host]": "chatgpt.com",
            "elements_session_client[stripe_js_id]": self.stripe_js_id,
            "elements_session_client[locale]": "zh-CN",
            "elements_session_client[is_aggregation_expected]": "false",
            "key": publishable_key,
            "_stripe_version": stripe_version,
        }
        resp = self.stripe_session.post(
            f"{STRIPE_API}/v1/payment_pages/{checkout_session_id}/init",
            data=data,
            headers=self._stripe_api_headers(),
            timeout=30,
            impersonate="chrome136",
        )
        body_preview = (resp.text or "")[:500].replace("\n", " ").replace("\r", " ")
        try:
            payload = resp.json() if resp.content else {}
        except Exception:
            payload = {"body_preview": body_preview}
        self.log(f"payment_pages/init: status={resp.status_code}")
        if resp.status_code == 200 and isinstance(payload, dict):
            hosted_url = str(payload.get("stripe_hosted_url") or "").strip()
            if hosted_url:
                self.log(f"提取到 stripe_hosted_url: {hosted_url}")
            return payload
        return payload if isinstance(payload, dict) else {}

    def create_checkout(self) -> dict[str, Any]:
        self.log("开始创建 checkout session")
        ctx = self._payment_context()
        payment_country = str(self.config.get("payment_country", "JP") or "JP").upper()
        payload = {
            "plan_name": DEFAULT_PAYMENT_PLAN_NAME,
            "team_plan_data": {
                "workspace_name": DEFAULT_PAYMENT_WORKSPACE_NAME,
                "price_interval": DEFAULT_PAYMENT_PRICE_INTERVAL,
                "seat_quantity": DEFAULT_PAYMENT_SEAT_QUANTITY,
            },
            "billing_details": {
                "country": payment_country,
                "currency": "USD" if payment_country == "US" else "GBP",
            },
            "cancel_url": DEFAULT_PAYMENT_CANCEL_URL,
            "checkout_ui_mode": DEFAULT_PAYMENT_CHECKOUT_UI_MODE,
        }
        promo_campaign_id = DEFAULT_PAYMENT_PROMO_CAMPAIGN_ID
        if promo_campaign_id:
            payload["promo_campaign"] = {
                "promo_campaign_id": promo_campaign_id,
                "is_coupon_from_query_param": True,
            }
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {ctx['access_token']}",
            "Origin": CHATGPT_BASE,
            "Referer": f"https://chatgpt.com/?promo_campaign={DEFAULT_PAYMENT_PROMO_CAMPAIGN_ID.replace('-', '')}#team-pricing",
            "User-Agent": self.ua,
            "oai-device-id": self.device_id,
            "openai-sentinel-token": ctx["sentinel_token"],
            "oai-client-version": "prod-d7360e59f",
            "oai-client-build-number": "payment-protocol",
            "sec-ch-ua": self.sec_ch_ua,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
        }
        if ctx["csrf_token"]:
            headers["x-csrf-token"] = ctx["csrf_token"]
        headers.update(self._trace_headers())
        resp = self.session.post(
            f"{CHATGPT_BASE}/backend-api/payments/checkout",
            json=payload,
            headers=headers,
            timeout=30,
            impersonate="chrome136",
        )
        if resp.status_code != 200:
            raise RuntimeError(f"checkout 失败: {resp.status_code} {resp.text[:250]}")
        data = resp.json()
        checkout_session_id = self._normalize_checkout_session_id(data)
        client_secret = str(data.get("client_secret") or "")
        self.stripe_publishable_key = self._extract_publishable_key(data)
        expected_amount = self._extract_expected_amount(data)
        self.log(f"checkout 成功: checkout_session_id={checkout_session_id}")
        if self.stripe_publishable_key:
            self.log(f"提取到 publishable_key: {self.stripe_publishable_key[:18]}...")
        if expected_amount is not None:
            self.log(f"提取到 expected_amount: {expected_amount}")
        if not checkout_session_id:
            raise RuntimeError(f"checkout 响应缺少 checkout_session_id: {data}")
        checkout_url = f"{CHATGPT_BASE}/checkout/openai_llc/{checkout_session_id}"
        self.log(f"生成 checkout_url: {checkout_url}")

        stripe_hosted_url = ""
        if self.stripe_publishable_key:
            init_payload = self.init_stripe_hosted_page(checkout_session_id, self.stripe_publishable_key)
            stripe_hosted_url = str(init_payload.get("stripe_hosted_url") or "").strip()
            if stripe_hosted_url and expected_amount is None:
                expected_amount = self._extract_expected_amount(init_payload)

        return {
            "checkout_session_id": checkout_session_id,
            "client_secret": client_secret,
            "publishable_key": self.stripe_publishable_key,
            "expected_amount": expected_amount,
            "checkout_url": checkout_url,
            "stripe_hosted_url": stripe_hosted_url,
            "raw": data,
        }

    def _extract_amount_from_payload(self, payload: Any) -> Optional[int]:
        return self._extract_expected_amount(payload)

    def probe_checkout_page(self, checkout_url: str) -> dict[str, Any]:
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": DEFAULT_PAYMENT_REFERRER,
            "User-Agent": self.ua,
        }
        resp = self.session.get(
            checkout_url,
            headers=headers,
            timeout=30,
            impersonate="chrome136",
            allow_redirects=True,
        )
        body = resp.text or ""
        preview = body[:1200].replace("\n", " ").replace("\r", " ")
        publishable_key = self._extract_publishable_key(body)
        expected_amount = self._extract_amount_from_payload(body)
        return {
            "final_url": str(getattr(resp, "url", "") or ""),
            "status": resp.status_code,
            "content_type": str(resp.headers.get("content-type", "") or ""),
            "body_preview": preview,
            "publishable_key": publishable_key,
            "expected_amount": expected_amount,
        }

    def fetch_checkout_amount_details(self, checkout_session_id: str) -> dict[str, Any]:
        publishable_key = str(self.stripe_publishable_key or "").strip()
        if not publishable_key.startswith(("pk_live_", "pk_test_")):
            return {"error": "missing_publishable_key"}

        headers = {
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://js.stripe.com",
            "Referer": DEFAULT_PAYMENT_REFERRER,
            "User-Agent": self.ua,
            "Accept-Language": "en-US,en;q=0.9",
            "sec-ch-ua": self.sec_ch_ua,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Content-Type": "application/x-www-form-urlencoded",
        }

        candidates: list[dict[str, Any]] = []
        request_specs = [
            {
                "name": "payment_page_detail",
                "method": "POST",
                "url": f"{STRIPE_API}/v1/payment_pages/{checkout_session_id}",
                "data": {
                    "key": publishable_key,
                },
            },
            {
                "name": "payment_page_init",
                "method": "POST",
                "url": f"{STRIPE_API}/v1/payment_pages/{checkout_session_id}/init",
                "data": {
                    "key": publishable_key,
                },
            },
        ]

        for spec in request_specs:
            try:
                resp = self.stripe_session.post(
                    spec["url"],
                    data=spec["data"],
                    headers=headers,
                    timeout=30,
                    impersonate="chrome136",
                )
                body = resp.text or ""
                item: dict[str, Any] = {
                    "name": spec["name"],
                    "url": spec["url"],
                    "status": resp.status_code,
                    "content_type": str(resp.headers.get("content-type", "") or ""),
                    "body_preview": body[:500].replace("\n", " ").replace("\r", " "),
                }
                try:
                    parsed = resp.json()
                    item["json"] = parsed
                    item["json_keys"] = sorted(parsed.keys()) if isinstance(parsed, dict) else []
                    extracted = self._extract_amount_from_payload(parsed)
                    if extracted is not None:
                        item["expected_amount"] = extracted
                except Exception:
                    pass
                candidates.append(item)
            except Exception as exc:
                candidates.append({
                    "name": spec["name"],
                    "url": spec["url"],
                    "error": str(exc),
                })

        return {"requests": candidates}

    def _stripe_metrics_request(self, method: str = "GET", extra_params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        params = {"id": self.stripe_js_id}
        if extra_params:
            params.update(extra_params)
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://js.stripe.com/",
            "Origin": "https://js.stripe.com",
            "User-Agent": self.ua,
            "Accept-Language": "en-US,en;q=0.9",
            "sec-ch-ua": self.sec_ch_ua,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        }
        request_method = method.upper().strip()
        stripe_cookie_names = ["__stripe_mid", "__stripe_sid"]
        cookie_header = "; ".join(
            f"{name}={value}" for name in stripe_cookie_names
            if (value := self._cookie(name))
        )
        if cookie_header:
            headers["Cookie"] = cookie_header
        if request_method == "POST":
            resp = self.stripe_session.post(
                STRIPE_METRICS_URL,
                data=params,
                headers={**headers, "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
                timeout=30,
                impersonate="chrome136",
            )
            request_url = STRIPE_METRICS_URL
        else:
            resp = self.stripe_session.get(
                STRIPE_METRICS_URL,
                params=params,
                headers=headers,
                timeout=30,
                impersonate="chrome136",
            )
            request_url = f"{STRIPE_METRICS_URL}?id={params.get('id', '')}"

        body = resp.text or ""
        final_url = str(getattr(resp, "url", "") or "")
        content_type = str(resp.headers.get("content-type", "") or "")
        body_preview = body[:300].replace("\n", " ").replace("\r", " ")
        data: dict[str, Any] = {
            "request_method": request_method,
            "request_url": request_url,
            "final_url": final_url,
            "status": resp.status_code,
            "content_type": content_type,
            "body_preview": body_preview,
            "proxy": self.proxy,
            "cookie_names": stripe_cookie_names if cookie_header else [],
            "cookie_length": len(cookie_header),
            "header_keys": sorted(headers.keys()),
        }
        try:
            parsed = resp.json()
            if isinstance(parsed, dict):
                data["json_keys"] = sorted(parsed.keys())
                data["guid"] = parsed.get("guid")
                data["muid"] = parsed.get("muid")
                data["sid"] = parsed.get("sid")
        except Exception:
            pass
        try:
            for cookie in self.stripe_session.cookies.jar:
                name = str(getattr(cookie, "name", "") or "")
                value = str(getattr(cookie, "value", "") or "")
                if name == "__stripe_mid" and value:
                    data.setdefault("muid", value)
                if name == "__stripe_sid" and value:
                    data.setdefault("sid", value)
        except Exception:
            pass
        return data

    def test_stripe_connectivity(self) -> dict[str, Any]:
        self.log("开始测试 Stripe 连通性 (GET /6)")
        get_diag = self._stripe_metrics_request("GET")
        result: dict[str, Any] = {"get": get_diag}
        publishable_key = self.stripe_publishable_key or ""
        if publishable_key:
            self.log("开始测试 Stripe 连通性 (POST /6)")
            post_diag = self._stripe_metrics_request(
                "POST",
                {
                    "key": publishable_key,
                    "guid": "",
                    "muid": self._cookie("__stripe_mid") or str(uuid.uuid4()),
                    "sid": self._cookie("__stripe_sid") or str(uuid.uuid4()),
                    "source": "m-stripe",
                },
            )
            result["post"] = post_diag
            result["publishable_key"] = publishable_key
        return result

    def fetch_stripe_risk(self) -> dict[str, str]:
        self.log("开始获取 Stripe 风控参数")
        diag = self.test_stripe_connectivity()
        candidates = []
        if isinstance(diag.get("get"), dict):
            candidates.append(("GET", diag["get"]))
        if isinstance(diag.get("post"), dict):
            candidates.append(("POST", diag["post"]))

        for label, item in candidates:
            self.log(f"检查 {label} /6 返回: status={item.get('status')} final_url={item.get('final_url')}")
            body_preview = str(item.get("body_preview") or "")
            guid = str(item.get("guid") or "")
            muid = str(item.get("muid") or "")
            sid = str(item.get("sid") or "")
            guid_source = ""
            muid_source = ""
            sid_source = ""
            if guid:
                guid_source = f"{label.lower()}_6"
            if not guid:
                m = re.search(r'"guid"\s*[:=]\s*"([^"]+)"', body_preview)
                if m:
                    guid = m.group(1)
                    guid_source = f"{label.lower()}_body"
            if not guid:
                continue
            if not muid:
                muid = self._cookie("__stripe_mid")
                if muid:
                    muid_source = "cookie"
            else:
                muid_source = f"{label.lower()}_6"
            if not sid:
                sid = self._cookie("__stripe_sid")
                if sid:
                    sid_source = "cookie"
            else:
                sid_source = f"{label.lower()}_6"
            if not muid:
                muid = str(uuid.uuid4())
                muid_source = "uuid_fallback"
            if not sid:
                sid = str(uuid.uuid4())
                sid_source = "uuid_fallback"
            self.session.cookies.set("__stripe_mid", muid, domain="api.stripe.com")
            self.session.cookies.set("__stripe_sid", sid, domain="api.stripe.com")
            self.log(
                f"风控参数准备完成: guid={guid[:12]}... muid={muid[:8]}... sid={sid[:8]}... "
                f"guid_source={guid_source or 'unknown'}"
            )
            return {
                "guid": guid,
                "muid": muid,
                "sid": sid,
                "guid_source": guid_source or "unknown",
                "muid_source": muid_source or "unknown",
                "sid_source": sid_source or "unknown",
            }

        get_item = diag.get("get") or {}
        post_item = diag.get("post") or {}
        publishable_key = str(diag.get("publishable_key") or self.stripe_publishable_key or "")
        if not publishable_key:
            raise RuntimeError(
                "未能解析 guid，且 checkout 响应中未提取到 Stripe publishable key\n"
                f"get.final_url={get_item.get('final_url', '')}\n"
                f"get.status={get_item.get('status', '')}\n"
                f"get.content_type={get_item.get('content_type', '')}\n"
                f"get.body_preview={get_item.get('body_preview', '')}"
            )
        raise RuntimeError(
            "未能解析 guid\n"
            f"publishable_key={publishable_key[:18]}...\n"
            f"get.final_url={get_item.get('final_url', '')}\n"
            f"get.status={get_item.get('status', '')}\n"
            f"get.content_type={get_item.get('content_type', '')}\n"
            f"get.body_preview={get_item.get('body_preview', '')}\n"
            f"post.final_url={post_item.get('final_url', '')}\n"
            f"post.status={post_item.get('status', '')}\n"
            f"post.content_type={post_item.get('content_type', '')}\n"
            f"post.body_preview={post_item.get('body_preview', '')}"
        )

    def _summarize_confirm(self, payload: dict[str, Any]) -> dict[str, Any]:
        setup_intent = payload.get("setup_intent") if isinstance(payload.get("setup_intent"), dict) else {}
        total_summary = payload.get("total_summary") if isinstance(payload.get("total_summary"), dict) else {}
        customer = payload.get("customer") if isinstance(payload.get("customer"), dict) else {}
        recurring_details = payload.get("recurring_details") if isinstance(payload.get("recurring_details"), dict) else {}
        invoice = payload.get("invoice") if isinstance(payload.get("invoice"), dict) else {}
        next_action_obj = setup_intent.get("next_action") if isinstance(setup_intent.get("next_action"), dict) else {}
        setup_intent_status = str(setup_intent.get("status") or "")
        next_action = str(next_action_obj.get("type") or "") if isinstance(setup_intent, dict) else ""
        payment_status = str(payload.get("payment_status") or "")
        checkout_status = str(payload.get("status") or "")
        requires_action = setup_intent_status == "requires_action"
        is_paid = payment_status == "paid"
        is_success = is_paid or (checkout_status == "complete" and not requires_action)
        if requires_action:
            final_state = "requires_action"
            final_message = "需要前端完成 3DS/银行挑战，当前仅完成协议提交，未最终生效"
        elif is_success:
            final_state = "succeeded"
            final_message = "支付/绑卡已成功完成"
        elif checkout_status == "open" and payment_status == "unpaid":
            final_state = "pending"
            final_message = "会话仍处于打开且未支付状态，需继续观察后续结果"
        else:
            final_state = "unknown"
            final_message = "状态未闭环，请结合 Stripe/ChatGPT 页面进一步确认"
        summary = {
            "session_id": str(payload.get("session_id") or payload.get("id") or ""),
            "status": checkout_status,
            "payment_status": payment_status,
            "ui_mode": str(payload.get("ui_mode") or ""),
            "currency": str(payload.get("currency") or ""),
            "amount_subtotal": total_summary.get("subtotal"),
            "amount_total": total_summary.get("total"),
            "amount_due": total_summary.get("due"),
            "customer_email": str(payload.get("customer_email") or customer.get("email") or ""),
            "customer_name": str((customer.get("name") if isinstance(customer, dict) else "") or ""),
            "setup_intent_id": str(setup_intent.get("id") or ""),
            "setup_intent_client_secret": str(setup_intent.get("client_secret") or ""),
            "setup_intent_status": setup_intent_status,
            "setup_intent_next_action": next_action,
            "setup_intent_next_action_url": str(next_action_obj.get("redirect_to_url") or {}).strip() if isinstance(next_action_obj.get("redirect_to_url"), str) else str(((next_action_obj.get("redirect_to_url") or {}).get("url") if isinstance(next_action_obj.get("redirect_to_url"), dict) else "") or ""),
            "return_url": str(payload.get("return_url") or ""),
            "stripe_hosted_url": str(payload.get("stripe_hosted_url") or ""),
            "requires_action": requires_action,
            "trial_total": invoice.get("total") if isinstance(invoice, dict) else None,
            "renewal_total": recurring_details.get("total") if isinstance(recurring_details, dict) else None,
            "final_state": final_state,
            "final_message": final_message,
            "is_success": is_success,
        }
        return summary

    def confirm(self, checkout_session_id: str, client_secret: str, risk: dict[str, str], expected_amount: Optional[int]) -> dict[str, Any]:
        billing_email = str(self.config.get("payment_billing_email") or self.account.get("email") or "")
        publishable_key = str(self.stripe_publishable_key or "").strip()
        if not publishable_key.startswith(("pk_live_", "pk_test_")):
            raise RuntimeError(f"confirm 缺少有效 publishable key: {publishable_key[:40]}")
        if expected_amount is None or int(expected_amount) < 0:
            raise RuntimeError(f"confirm 缺少有效 expected_amount: {expected_amount}")
        self.log(
            f"开始 confirm: checkout_session_id={checkout_session_id} "
            f"publishable_key={publishable_key[:18]}... expected_amount={expected_amount}"
        )
        form_payload = {
            "payment_method_data": {
                "type": "card",
                "billing_details": {
                    "name": str(self.config.get("payment_billing_name") or self.account.get("email") or "OpenAI User"),
                    "email": billing_email,
                    "address": {
                        "line1": str(self.config.get("payment_billing_line1") or ""),
                        "city": str(self.config.get("payment_billing_city") or ""),
                        "state": str(self.config.get("payment_billing_state") or ""),
                        "postal_code": str(self.config.get("payment_billing_postal_code") or ""),
                        "country": str(self.config.get("payment_billing_country") or self.config.get("payment_country") or "JP").upper(),
                    },
                },
                "card": {
                    "number": str(self.config.get("payment_card_number") or ""),
                    "exp_month": str(self.config.get("payment_card_exp_month") or ""),
                    "exp_year": str(self.config.get("payment_card_exp_year") or ""),
                    "cvc": str(self.config.get("payment_card_cvc") or ""),
                },
                "guid": risk["guid"],
                "muid": risk["muid"],
                "sid": risk["sid"],
                "payment_user_agent": self.payment_user_agent,
                "referrer": DEFAULT_PAYMENT_REFERRER,
                "time_on_page": self.time_on_page,
            },
            "expected_payment_method_type": "card",
            "expected_amount": int(expected_amount),
            "key": publishable_key,
        }
        self.log("confirm 使用标准协议字段模式")
        resp = self.session.post(
            f"{STRIPE_API}/v1/payment_pages/{checkout_session_id}/confirm",
            data=flatten_form_data(form_payload),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": "https://js.stripe.com",
                "Referer": DEFAULT_PAYMENT_REFERRER,
                "User-Agent": self.ua,
            },
            timeout=45,
            impersonate="chrome136",
        )
        try:
            data = resp.json()
        except Exception:
            raise RuntimeError(f"confirm 返回非 JSON: {resp.status_code} {resp.text[:250]}")
        error_obj = data.get("error") if isinstance(data.get("error"), dict) else {}
        if resp.status_code >= 400 or error_obj:
            error_message = str(error_obj.get("message") or "")
            if "unsupported for publishable key tokenization" in error_message.lower():
                raise RuntimeError(
                    "confirm 失败: 当前商户不支持直接卡号协议提交，请改走 stripe_hosted_url 前端页完成支付/验证"
                )
            raise RuntimeError(f"confirm 失败: {resp.status_code} {json.dumps(data, ensure_ascii=False)[:300]}")
        summary = self._summarize_confirm(data)
        self.log(
            "confirm 成功: "
            f"status={summary.get('status') or '-'} "
            f"payment_status={summary.get('payment_status') or '-'} "
            f"setup_intent_status={summary.get('setup_intent_status') or '-'} "
            f"next_action={summary.get('setup_intent_next_action') or '-'} "
            f"final_state={summary.get('final_state') or '-'}"
        )
        return {"raw": data, "summary": summary}

    def run(self) -> dict[str, Any]:
        for field in ("payment_card_number", "payment_card_exp_month", "payment_card_exp_year", "payment_card_cvc"):
            if not str(self.config.get(field) or "").strip():
                raise RuntimeError(f"缺少配置字段: {field}")

        retry_enabled = str(self.config.get("payment_retry_enabled", True)).strip().lower() not in {"0", "false", "no", "off"}
        max_attempts = max(1, int(self.config.get("payment_retry_max_attempts", 3) or 3))
        retry_interval_ms = max(0, int(self.config.get("payment_retry_interval_ms", 2000) or 2000))
        attempts = max_attempts if retry_enabled else 1
        errors: list[str] = []

        self.log(f"开始绑卡: email={self.account.get('email')} card={mask_card(str(self.config.get('payment_card_number') or ''))}")
        self.log(f"重试设置: enabled={retry_enabled} attempts={attempts} interval_ms={retry_interval_ms}")

        def _build_result(
            *,
            attempt: int,
            attempts_total: int,
            retry_enabled: bool,
            checkout: dict[str, Any],
            risk: dict[str, Any],
            confirm_summary: dict[str, Any],
            verification: Optional[dict[str, Any]] = None,
        ) -> dict[str, Any]:
            card_digits = re.sub(r"\D+", "", str(self.config.get("payment_card_number") or ""))
            return {
                "email": self.account.get("email"),
                "checkout_session_id": checkout.get("checkout_session_id") or "",
                "card_mask": mask_card(str(self.config.get("payment_card_number") or "")),
                "card_bin": card_digits[:6] if len(card_digits) >= 6 else card_digits,
                "attempt": attempt,
                "attempts_total": attempts_total,
                "retry_enabled": retry_enabled,
                "stripe_hosted_url": checkout.get("stripe_hosted_url") or "",
                "checkout": {
                    "checkout_session_id": checkout.get("checkout_session_id") or "",
                    "publishable_key": checkout.get("publishable_key") or "",
                    "publishable_key_prefix": str(checkout.get("publishable_key") or "")[:18],
                    "expected_amount": checkout.get("expected_amount"),
                    "checkout_url": checkout.get("checkout_url") or "",
                    "stripe_hosted_url": checkout.get("stripe_hosted_url") or "",
                    "status": str((checkout.get("raw") or {}).get("status") or ""),
                    "payment_status": str((checkout.get("raw") or {}).get("payment_status") or ""),
                    "processor_entity": str((checkout.get("raw") or {}).get("processor_entity") or ""),
                    "billing_details": (checkout.get("raw") or {}).get("billing_details") or {},
                },
                "risk": risk,
                "confirm": confirm_summary,
                "confirm_status": {
                    "checkout_status": confirm_summary.get("status") if isinstance(confirm_summary, dict) else "",
                    "payment_status": confirm_summary.get("payment_status") if isinstance(confirm_summary, dict) else "",
                    "setup_intent_id": confirm_summary.get("setup_intent_id") if isinstance(confirm_summary, dict) else "",
                    "setup_intent_client_secret": confirm_summary.get("setup_intent_client_secret") if isinstance(confirm_summary, dict) else "",
                    "setup_intent_status": confirm_summary.get("setup_intent_status") if isinstance(confirm_summary, dict) else "",
                    "setup_intent_next_action": confirm_summary.get("setup_intent_next_action") if isinstance(confirm_summary, dict) else "",
                    "setup_intent_next_action_url": confirm_summary.get("setup_intent_next_action_url") if isinstance(confirm_summary, dict) else "",
                    "requires_action": confirm_summary.get("requires_action") if isinstance(confirm_summary, dict) else False,
                    "return_url": confirm_summary.get("return_url") if isinstance(confirm_summary, dict) else "",
                    "final_state": confirm_summary.get("final_state") if isinstance(confirm_summary, dict) else "",
                    "final_message": confirm_summary.get("final_message") if isinstance(confirm_summary, dict) else "",
                    "is_success": confirm_summary.get("is_success") if isinstance(confirm_summary, dict) else False,
                },
                "verification": verification or {},
            }

        for attempt in range(1, attempts + 1):
            try:
                self.log(f"第 {attempt}/{attempts} 次尝试开始")
                checkout = self.create_checkout()
                page_probe = self.probe_checkout_page(checkout["checkout_url"])
                if page_probe.get("publishable_key") and not checkout.get("publishable_key"):
                    checkout["publishable_key"] = page_probe.get("publishable_key")
                    self.stripe_publishable_key = str(page_probe.get("publishable_key") or "")
                    self.log(f"从 checkout 页面提取到 publishable_key: {self.stripe_publishable_key[:18]}...")
                if checkout.get("expected_amount") is None and page_probe.get("expected_amount") is not None:
                    checkout["expected_amount"] = page_probe.get("expected_amount")
                    self.log(f"从 checkout 页面提取到 expected_amount={checkout['expected_amount']}")
                if checkout.get("expected_amount") is None:
                    amount_probe = self.fetch_checkout_amount_details(checkout["checkout_session_id"])
                    for item in amount_probe.get("requests", []):
                        if isinstance(item, dict) and item.get("expected_amount") is not None:
                            checkout["expected_amount"] = item.get("expected_amount")
                            self.log(f"从 {item.get('name')} 提取到 expected_amount={checkout['expected_amount']}")
                            break
                risk = self.fetch_stripe_risk()
                confirm = self.confirm(
                    checkout["checkout_session_id"],
                    checkout["client_secret"],
                    risk,
                    checkout.get("expected_amount"),
                )
                confirm_summary = confirm.get("summary") if isinstance(confirm, dict) else {}
                result = _build_result(
                    attempt=attempt,
                    attempts_total=attempts,
                    retry_enabled=retry_enabled,
                    checkout=checkout,
                    risk=risk,
                    confirm_summary=confirm_summary,
                )
                if result["confirm_status"]["requires_action"]:
                    verification_url = (
                        result["confirm_status"].get("setup_intent_next_action_url")
                        or result["confirm_status"].get("return_url")
                        or result["checkout"].get("checkout_url")
                        or result.get("stripe_hosted_url")
                        or ""
                    )
                    result["verification"] = {
                        "required": True,
                        "status": "awaiting_human_verification",
                        "reason": result["confirm_status"].get("setup_intent_next_action") or "requires_action",
                        "message": "检测到支付验证页，请在前端打开验证链接手动完成验证后，再继续后处理。",
                        "verification_url": verification_url,
                    }
                    self.log(
                        "检测到需要人工验证: "
                        f"reason={result['verification']['reason']} "
                        f"verification_url={verification_url or '-'}"
                    )
                self.log(
                    "当前 confirm 后状态: "
                    f"checkout_status={result['confirm_status']['checkout_status'] or '-'} "
                    f"payment_status={result['confirm_status']['payment_status'] or '-'} "
                    f"setup_intent_status={result['confirm_status']['setup_intent_status'] or '-'} "
                    f"requires_action={result['confirm_status']['requires_action']} "
                    f"final_state={result['confirm_status']['final_state'] or '-'}"
                )
                return result
            except Exception as exc:
                error_text = f"第 {attempt}/{attempts} 次失败: {exc}"
                self.log(error_text)
                if "不支持直接卡号协议提交" in str(exc):
                    result = {
                        "email": self.account.get("email"),
                        "checkout_session_id": checkout.get("checkout_session_id") if 'checkout' in locals() else "",
                        "card_mask": mask_card(str(self.config.get("payment_card_number") or "")),
                        "attempt": attempt,
                        "attempts_total": attempts,
                        "retry_enabled": retry_enabled,
                        "stripe_hosted_url": (checkout.get("stripe_hosted_url") if 'checkout' in locals() else "") or "",
                        "checkout": {
                            "checkout_session_id": (checkout.get("checkout_session_id") if 'checkout' in locals() else "") or "",
                            "checkout_url": (checkout.get("checkout_url") if 'checkout' in locals() else "") or "",
                            "stripe_hosted_url": (checkout.get("stripe_hosted_url") if 'checkout' in locals() else "") or "",
                            "status": str(((checkout.get("raw") if 'checkout' in locals() else {}) or {}).get("status") or ""),
                            "payment_status": str(((checkout.get("raw") if 'checkout' in locals() else {}) or {}).get("payment_status") or ""),
                            "billing_details": (((checkout.get("raw") if 'checkout' in locals() else {}) or {}).get("billing_details") or {}),
                        },
                        "risk": risk if 'risk' in locals() else {},
                        "confirm": {},
                        "confirm_status": {
                            "checkout_status": str(((checkout.get("raw") if 'checkout' in locals() else {}) or {}).get("status") or "open"),
                            "payment_status": str(((checkout.get("raw") if 'checkout' in locals() else {}) or {}).get("payment_status") or "unpaid"),
                            "setup_intent_status": "",
                            "setup_intent_next_action": "hosted_page",
                            "requires_action": True,
                            "return_url": "",
                            "final_state": "hosted_page_required",
                            "final_message": "当前商户不支持直接卡号协议 confirm，请打开 stripe_hosted_url 在前端页面完成支付/验证",
                            "is_success": False,
                        },
                    }
                    self.log(
                        "当前商户不支持协议直提卡号，已切换为 hosted page 模式返回结果: "
                        f"bin={result.get('card_bin') or '-'} pk={str((((checkout.get('publishable_key') if 'checkout' in locals() else '') or ''))[:18]) or '-'}"
                    )
                    return result
                errors.append(error_text)
                if attempt >= attempts:
                    raise RuntimeError("\n".join(errors))
                if retry_interval_ms > 0:
                    self.log(f"等待 {retry_interval_ms}ms 后重试")
                    time.sleep(retry_interval_ms / 1000)

        raise RuntimeError("未知重试错误")


def main() -> int:
    config = load_config()
    print("=" * 60)
    print("  独立绑卡工具（CLI）")
    print(f"  配置文件: {CONFIG_PATH}")
    print(f"  账号目录: {', '.join(str(path) for path in _account_dirs())}")
    print("=" * 60)

    try:
        selected_email = choose_account_email()
        account = load_account(selected_email)
        print(f"\n[info] 已选择账号: {selected_email}")
        binder = PaymentBinder(config, account)
        result = binder.run()
        print("\n[OK] 绑卡执行完成")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except KeyboardInterrupt:
        print("\n[info] 已取消")
        return 1
    except Exception as exc:
        print(f"\n[FAIL] {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
