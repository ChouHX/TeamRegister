#!/usr/bin/env python3
"""
独立绑卡工具（CLI 版）：
1. 扫描 codex_tokens/*.json
2. 交互选择账号
3. 按 config.json 执行 checkout -> m.stripe.com/6 -> confirm
4. 输出详细后台日志
"""

from __future__ import annotations

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
ACCOUNTS_DIR = BASE_DIR / "codex_tokens"
CHATGPT_BASE = "https://chatgpt.com"
STRIPE_API = "https://api.stripe.com"
STRIPE_METRICS_URL = "https://m.stripe.com/6"
ACCOUNT_STORE = AccountStore()


def load_config() -> dict[str, Any]:
    defaults = {
        "proxy": "http://127.0.0.1:7890",
        "payment_plan_name": "chatgptteamplan",
        "payment_workspace_name": "Artizancloud",
        "payment_price_interval": "month",
        "payment_seat_quantity": 5,
        "payment_country": "JP",
        "payment_currency": "JPY",
        "payment_promo_campaign_id": "team1dollar",
        "payment_cancel_url": "https://chatgpt.com/?promo_campaign=team1dollar#team-pricing",
        "payment_checkout_ui_mode": "custom",
        "payment_card_number": "",
        "payment_card_exp_month": "",
        "payment_card_exp_year": "",
        "payment_card_cvc": "",
        "payment_billing_name": "",
        "payment_billing_email": "",
        "payment_billing_line1": "",
        "payment_billing_city": "",
        "payment_billing_state": "",
        "payment_billing_postal_code": "",
        "payment_billing_country": "JP",
        "payment_user_agent_override": "",
        "payment_referrer": "https://chatgpt.com/",
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


def list_accounts() -> list[dict[str, Any]]:
    rows = ACCOUNT_STORE.list_accounts()
    if rows:
        return rows
    if not ACCOUNTS_DIR.exists():
        return []
    results: list[dict[str, Any]] = []
    for path in sorted([p for p in ACCOUNTS_DIR.glob("*.json") if p.is_file()], key=lambda x: x.name.lower()):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                results.append(data)
        except Exception:
            continue
    return results


def load_account(email: str) -> dict[str, Any]:
    account = ACCOUNT_STORE.get_account(email)
    if account:
        return account
    path = ACCOUNTS_DIR / f"{email}.json"
    if not path.exists():
        raise FileNotFoundError(f"账号不存在: {email}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("账号文件格式错误")
    return data


def choose_account_email() -> str:
    accounts = list_accounts()
    if not accounts:
        raise RuntimeError("未找到可用账号，请先注册或导入账号")
    print("\n可用账号列表：")
    for idx, account in enumerate(accounts, start=1):
        email = str(account.get("email") or "")
        expired = str(account.get("expired") or "")
        account_id = str(account.get("account_id") or "")
        print(f"[{idx}] {email} | account_id={account_id[:8]}... | expired={expired}")
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

    def _bootstrap_cookies(self) -> None:
        cookies = self.account.get("cookies") or {}
        if isinstance(cookies, dict):
            for name, value in cookies.items():
                if value:
                    self.session.cookies.set(str(name), str(value))
        session_token = str(self.account.get("session_token") or "")
        csrf_token = str(self.account.get("csrf_token") or "")
        if session_token:
            self.session.cookies.set("__Secure-next-auth.session-token", session_token, domain="chatgpt.com")
        if csrf_token:
            self.session.cookies.set("__Host-next-auth.csrf-token", csrf_token, domain="chatgpt.com")
        self.session.cookies.set("oai-did", self.device_id, domain="chatgpt.com")
        self._bootstrap_stripe_session()

    def _bootstrap_stripe_session(self) -> None:
        self.stripe_session.headers.update({
            "User-Agent": self.ua,
            "Accept": "application/json, text/plain, */*",
        })

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

    def _payment_context(self) -> dict[str, str]:
        access_token = str(self.account.get("access_token") or "").strip()
        session_token = self._cookie("__Secure-next-auth.session-token")
        csrf_token = self._cookie("__Host-next-auth.csrf-token")
        if not access_token:
            raise RuntimeError("账号文件缺少 access_token")
        if not session_token:
            raise RuntimeError("账号文件缺少 session_token/cookies，无法独立绑卡")
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

    def create_checkout(self) -> dict[str, Any]:
        self.log("开始创建 checkout session")
        ctx = self._payment_context()
        payload = {
            "plan_name": self.config.get("payment_plan_name", "chatgptteamplan"),
            "team_plan_data": {
                "workspace_name": self.config.get("payment_workspace_name", "Artizancloud"),
                "price_interval": self.config.get("payment_price_interval", "month"),
                "seat_quantity": int(self.config.get("payment_seat_quantity", 5) or 5),
            },
            "billing_details": {
                "country": str(self.config.get("payment_country", "JP") or "JP").upper(),
                "currency": str(self.config.get("payment_currency", "JPY") or "JPY").upper(),
            },
            "cancel_url": self.config.get("payment_cancel_url", "https://chatgpt.com/?promo_campaign=team1dollar#team-pricing"),
            "checkout_ui_mode": self.config.get("payment_checkout_ui_mode", "custom"),
        }
        promo_campaign_id = str(self.config.get("payment_promo_campaign_id") or "").strip()
        if promo_campaign_id:
            payload["promo_campaign"] = {
                "promo_campaign_id": promo_campaign_id,
                "is_coupon_from_query_param": True,
            }
        headers = {
            "Accept": "*/*",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {ctx['access_token']}",
            "Origin": CHATGPT_BASE,
            "Referer": str(self.config.get("payment_referrer") or "https://chatgpt.com/"),
            "User-Agent": self.ua,
            "oai-device-id": self.device_id,
            "openai-sentinel-token": ctx["sentinel_token"],
            "oai-client-version": "prod-payment-protocol",
            "oai-client-build-number": "payment-protocol",
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
        checkout_session_id = str(data.get("checkout_session_id") or data.get("session_id") or "")
        client_secret = str(data.get("client_secret") or "")
        self.stripe_publishable_key = self._extract_publishable_key(data)
        expected_amount = self._extract_expected_amount(data)
        self.log(f"checkout 成功: checkout_session_id={checkout_session_id}")
        self.log(f"checkout 顶层字段: {sorted(list(data.keys()))[:40]}")
        self.log(f"checkout 原始响应预览: {json.dumps(data, ensure_ascii=False)[:1200]}")
        if self.stripe_publishable_key:
            self.log(f"提取到 publishable_key: {self.stripe_publishable_key[:18]}...")
        if expected_amount is not None:
            self.log(f"提取到 expected_amount: {expected_amount}")
        else:
            self.log("未从 checkout 响应中提取到 expected_amount")
        if not checkout_session_id:
            raise RuntimeError(f"checkout 响应缺少 checkout_session_id: {data}")
        checkout_url = f"{CHATGPT_BASE}/checkout/openai_llc/{checkout_session_id}"
        self.log(f"生成 checkout_url: {checkout_url}")
        return {
            "checkout_session_id": checkout_session_id,
            "client_secret": client_secret,
            "publishable_key": self.stripe_publishable_key,
            "expected_amount": expected_amount,
            "checkout_url": checkout_url,
            "raw": data,
        }

    def _extract_amount_from_payload(self, payload: Any) -> Optional[int]:
        return self._extract_expected_amount(payload)

    def probe_checkout_page(self, checkout_url: str) -> dict[str, Any]:
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": str(self.config.get("payment_referrer") or "https://chatgpt.com/"),
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
            "Referer": str(self.config.get("payment_referrer") or "https://chatgpt.com/"),
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
            if not guid:
                m = re.search(r'"guid"\s*[:=]\s*"([^"]+)"', body_preview)
                if m:
                    guid = m.group(1)
            if not guid:
                continue
            if not muid:
                muid = self._cookie("__stripe_mid")
            if not sid:
                sid = self._cookie("__stripe_sid")
            if not muid:
                muid = str(uuid.uuid4())
            if not sid:
                sid = str(uuid.uuid4())
            self.session.cookies.set("__stripe_mid", muid, domain="api.stripe.com")
            self.session.cookies.set("__stripe_sid", sid, domain="api.stripe.com")
            self.log(f"获取风控成功: source={label} guid={guid[:12]}... muid={muid[:8]}... sid={sid[:8]}...")
            return {"guid": guid, "muid": muid, "sid": sid}

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
                "referrer": str(self.config.get("payment_referrer") or "https://chatgpt.com/"),
                "time_on_page": self.time_on_page,
            },
            "expected_payment_method_type": "card",
            "expected_amount": int(expected_amount),
            "key": publishable_key,
        }
        resp = self.session.post(
            f"{STRIPE_API}/v1/payment_pages/{checkout_session_id}/confirm",
            data=flatten_form_data(form_payload),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": "https://js.stripe.com",
                "Referer": str(self.config.get("payment_referrer") or "https://chatgpt.com/"),
                "User-Agent": self.ua,
            },
            timeout=45,
            impersonate="chrome136",
        )
        try:
            data = resp.json()
        except Exception:
            raise RuntimeError(f"confirm 返回非 JSON: {resp.status_code} {resp.text[:250]}")
        if resp.status_code >= 400 or data.get("error"):
            raise RuntimeError(f"confirm 失败: {resp.status_code} {json.dumps(data, ensure_ascii=False)[:300]}")
        self.log("confirm 成功")
        return data

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

        for attempt in range(1, attempts + 1):
            try:
                self.log(f"第 {attempt}/{attempts} 次尝试开始")
                checkout = self.create_checkout()
                page_probe = self.probe_checkout_page(checkout["checkout_url"])
                self.log(f"checkout 页面探测结果: {json.dumps(page_probe, ensure_ascii=False)[:1500]}")
                if page_probe.get("publishable_key") and not checkout.get("publishable_key"):
                    checkout["publishable_key"] = page_probe.get("publishable_key")
                    self.stripe_publishable_key = str(page_probe.get("publishable_key") or "")
                    self.log(f"从 checkout 页面提取到 publishable_key: {self.stripe_publishable_key[:18]}...")
                if checkout.get("expected_amount") is None and page_probe.get("expected_amount") is not None:
                    checkout["expected_amount"] = page_probe.get("expected_amount")
                    self.log(f"从 checkout 页面提取到 expected_amount={checkout['expected_amount']}")
                if checkout.get("expected_amount") is None:
                    amount_probe = self.fetch_checkout_amount_details(checkout["checkout_session_id"])
                    self.log(f"checkout 金额探测结果: {json.dumps(amount_probe, ensure_ascii=False)[:1500]}")
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
                return {
                    "email": self.account.get("email"),
                    "checkout_session_id": checkout["checkout_session_id"],
                    "card_mask": mask_card(str(self.config.get("payment_card_number") or "")),
                    "attempt": attempt,
                    "attempts_total": attempts,
                    "retry_enabled": retry_enabled,
                    "risk": risk,
                    "checkout": checkout["raw"],
                    "confirm": confirm,
                }
            except Exception as exc:
                error_text = f"第 {attempt}/{attempts} 次失败: {exc}"
                self.log(error_text)
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
    print(f"  账号目录: {ACCOUNTS_DIR}")
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
