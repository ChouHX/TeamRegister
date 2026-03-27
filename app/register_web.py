from __future__ import annotations

import asyncio
import json
import re
import threading
import traceback
from contextlib import redirect_stdout, redirect_stderr
from multiprocessing import Process, Queue
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import ncs_register, payment_bind_app
from app.account_store import AccountStore

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
CONFIG_PATH = BASE_DIR / "config.json"
ACCOUNT_STORE = AccountStore()

app = FastAPI(title="ChatGPT 注册面板")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class RegisterState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = False
        self.current_task = ""
        self.last_output = ""
        self.last_error = ""
        self.last_success = False
        self.last_form = self._default_register_form()
        self.last_pay_form = self._default_pay_form()
        self.last_pay_result: dict[str, Any] = {}
        self.pay_verification: dict[str, Any] = {}
        self.pay_hosted_page: dict[str, Any] = {}
        self.pay_thread: Optional[threading.Thread] = None
        self.register_process: Optional[Process] = None
        self.register_log_thread: Optional[threading.Thread] = None
        self.register_queue: Optional[Queue] = None
        self.log_version = 0
        self.status_version = 0
        self.ws_clients: set[WebSocket] = set()
        self.ws_loop: Optional[asyncio.AbstractEventLoop] = None

    @staticmethod
    def _default_register_form() -> dict[str, str]:
        cfg = ncs_register._load_config()
        return {
            "proxy": str(cfg.get("proxy", ncs_register.DEFAULT_PROXY or "")),
            "total_accounts": str(cfg.get("total_accounts", ncs_register.DEFAULT_TOTAL_ACCOUNTS)),
            "max_workers": str(cfg.get("max_workers", 3)),
            "cpa_cleanup": "true" if ncs_register._as_bool(cfg.get("cpa_cleanup", False)) else "false",
            "cpa_upload_every_n": str(cfg.get("cpa_upload_every_n", ncs_register.CPA_UPLOAD_EVERY_N)),
            "run_preflight": "true" if ncs_register._as_bool(cfg.get("run_preflight", True)) else "false",
            "mail_provider": str(cfg.get("mail_provider", ncs_register.MAIL_PROVIDER)),
            "outlookmail_config_path": str(cfg.get("outlookmail_config_path", ncs_register.OUTLOOKMAIL_CONFIG_PATH)),
            "outlookmail_profile": str(cfg.get("outlookmail_profile", ncs_register.OUTLOOKMAIL_PROFILE_MODE)),
            "outlookmail_fetch_mode": str(cfg.get("outlookmail_fetch_mode", ncs_register.OUTLOOKMAIL_FETCH_MODE)),
            "tempmail_lol_api_base": str(cfg.get("tempmail_lol_api_base", ncs_register.TEMPMAIL_LOL_API_BASE)),
            "lamail_api_base": str(cfg.get("lamail_api_base", ncs_register.LAMAIL_API_BASE)),
            "lamail_api_key": str(cfg.get("lamail_api_key", ncs_register.LAMAIL_API_KEY)),
            "lamail_domain": str(cfg.get("lamail_domain", ",".join(ncs_register.LAMAIL_DOMAINS))),
            "cfmail_config_path": str(cfg.get("cfmail_config_path", ncs_register._CFMAIL_CONFIG_PATH)),
            "cfmail_profile": str(cfg.get("cfmail_profile", ncs_register.CFMAIL_PROFILE_MODE)),
            "upload_api_url": str(cfg.get("upload_api_url", ncs_register.UPLOAD_API_URL)),
            "upload_api_token": str(cfg.get("upload_api_token", ncs_register.UPLOAD_API_TOKEN)),
            "upload_api_proxy": str(cfg.get("upload_api_proxy", ncs_register.UPLOAD_API_PROXY)),
            "cpa_cleanup_enabled": "true" if bool(cfg.get("cpa_cleanup_enabled", ncs_register.CPA_CLEANUP_ENABLED)) else "false",
        }

    @staticmethod
    def _default_pay_form() -> dict[str, str]:
        cfg = payment_bind_app.load_config()
        return {
            "proxy": str(cfg.get("proxy", "")),
            "payment_access_token": "",
            "payment_generation_mode": payment_bind_app.normalize_payment_generation_mode(
                cfg.get("payment_generation_mode", payment_bind_app.DEFAULT_PAYMENT_GENERATION_MODE)
            ),
            "payment_billing_name": str(cfg.get("payment_billing_name", "")),
            "payment_billing_email": str(cfg.get("payment_billing_email", "")),
            "payment_billing_line1": str(cfg.get("payment_billing_line1", "")),
            "payment_billing_city": str(cfg.get("payment_billing_city", "")),
            "payment_billing_state": str(cfg.get("payment_billing_state", "")),
            "payment_billing_postal_code": str(cfg.get("payment_billing_postal_code", "")),
            "payment_billing_country": str(cfg.get("payment_billing_country", cfg.get("payment_country", "US"))),
            "payment_card_number": str(cfg.get("payment_card_number", "")),
            "payment_card_exp_month": str(cfg.get("payment_card_exp_month", "")),
            "payment_card_exp_year": str(cfg.get("payment_card_exp_year", "")),
            "payment_card_cvc": str(cfg.get("payment_card_cvc", "")),
        }


_REGISTER_FAST_DELAY_FACTOR = 0.45
_REGISTER_FAST_OTP_INTERVALS = {
    "default": 1.0,
    "outlookmail": 1.0,
    "tempmail_lol": 0.8,
    "lamail": 0.6,
    "cfmail": 1.0,
}

_REGISTER_LOG_SKIP_PATTERNS = (
    re.compile(r"^\s*$"),
    re.compile(r"^进度:\s*\["),
    re.compile(r"^\[阶段\]\s"),
    re.compile(r"^\[OTP\]\s等待中"),
    re.compile(r"^\[OAuth\]\sOTP 等待中"),
    re.compile(r"^\s*(ChatGPT 批量自动注册|注册数量:|邮箱服务:|OutlookMail 配置:|OutlookMail 模式:|OutlookMail 拉取:|cfmail 配置:|cfmail 模式:|TempMail\.lol:|LaMail:|LaMail 域名池:|OAuth:|Token输出:|CPA分批上传:|OTP轮询:|Delay系数:|输出文件:)\b"),
    re.compile(r"^[#=]{10,}$"),
)


def _cap_runtime_float(value: Any, *, fallback: float, upper: float, lower: float = 0.0) -> float:
    try:
        parsed = float(value)
    except Exception:
        parsed = fallback
    return max(lower, min(parsed, upper))


def _configure_fast_register_runtime() -> None:
    ncs_register.REGISTER_DELAY_FACTOR = _cap_runtime_float(
        getattr(ncs_register, "REGISTER_DELAY_FACTOR", _REGISTER_FAST_DELAY_FACTOR),
        fallback=_REGISTER_FAST_DELAY_FACTOR,
        upper=_REGISTER_FAST_DELAY_FACTOR,
        lower=0.0,
    )
    ncs_register.OTP_POLL_INTERVAL_DEFAULT = _cap_runtime_float(
        getattr(ncs_register, "OTP_POLL_INTERVAL_DEFAULT", _REGISTER_FAST_OTP_INTERVALS["default"]),
        fallback=_REGISTER_FAST_OTP_INTERVALS["default"],
        upper=_REGISTER_FAST_OTP_INTERVALS["default"],
        lower=0.2,
    )
    current_intervals = dict(getattr(ncs_register, "OTP_POLL_INTERVAL_BY_PROVIDER", {}) or {})
    ncs_register.OTP_POLL_INTERVAL_BY_PROVIDER = {
        "outlookmail": _cap_runtime_float(
            current_intervals.get("outlookmail", ncs_register.OTP_POLL_INTERVAL_DEFAULT),
            fallback=_REGISTER_FAST_OTP_INTERVALS["outlookmail"],
            upper=_REGISTER_FAST_OTP_INTERVALS["outlookmail"],
            lower=0.2,
        ),
        "tempmail_lol": _cap_runtime_float(
            current_intervals.get("tempmail_lol", _REGISTER_FAST_OTP_INTERVALS["tempmail_lol"]),
            fallback=_REGISTER_FAST_OTP_INTERVALS["tempmail_lol"],
            upper=_REGISTER_FAST_OTP_INTERVALS["tempmail_lol"],
            lower=0.2,
        ),
        "lamail": _cap_runtime_float(
            current_intervals.get("lamail", _REGISTER_FAST_OTP_INTERVALS["lamail"]),
            fallback=_REGISTER_FAST_OTP_INTERVALS["lamail"],
            upper=_REGISTER_FAST_OTP_INTERVALS["lamail"],
            lower=0.2,
        ),
        "cfmail": _cap_runtime_float(
            current_intervals.get("cfmail", _REGISTER_FAST_OTP_INTERVALS["cfmail"]),
            fallback=_REGISTER_FAST_OTP_INTERVALS["cfmail"],
            upper=_REGISTER_FAST_OTP_INTERVALS["cfmail"],
            lower=0.2,
        ),
    }


def _normalize_register_log_line(line: str) -> str:
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", str(line or ""))
    return text.replace("\u0000", "").strip()


def _should_skip_register_log_line(line: str) -> bool:
    return any(pattern.search(line) for pattern in _REGISTER_LOG_SKIP_PATTERNS)


class _QueueLogWriter:
    def __init__(self, queue: Queue) -> None:
        self.queue = queue
        self._buffer = ""

    def _emit_line(self, raw_line: str) -> None:
        line = _normalize_register_log_line(raw_line)
        if not line or _should_skip_register_log_line(line):
            return
        self.queue.put(("log", f"{line}\n"))

    def _drain_buffer(self, *, force: bool = False) -> None:
        if not self._buffer:
            return
        text = self._buffer.replace("\r", "\n")
        parts = text.split("\n")
        remainder = "" if force else parts.pop()
        for raw_line in parts:
            self._emit_line(raw_line)
        self._buffer = remainder
        if force and self._buffer:
            self._emit_line(self._buffer)
            self._buffer = ""

    def write(self, data: str) -> int:
        if not data:
            return 0
        self._buffer += data
        if "\n" in data or "\r" in data:
            self._drain_buffer()
        return len(data)

    def flush(self) -> None:
        self._drain_buffer(force=True)


STATE = RegisterState()


async def _broadcast_ws(message: dict[str, Any]) -> None:
    stale: list[WebSocket] = []
    clients = list(STATE.ws_clients)
    for ws in clients:
        try:
            await ws.send_text(json.dumps(message, ensure_ascii=False))
        except Exception:
            stale.append(ws)
    if stale:
        with STATE.lock:
            for ws in stale:
                STATE.ws_clients.discard(ws)



def _schedule_ws_broadcast(message: dict[str, Any]) -> None:
    loop = STATE.ws_loop
    if loop is None or loop.is_closed():
        return
    try:
        asyncio.run_coroutine_threadsafe(_broadcast_ws(message), loop)
    except Exception:
        return



def _notify_status_update() -> None:
    with STATE.lock:
        STATE.status_version += 1
        payload = {
            "type": "status",
            "running": STATE.running,
            "current_task": STATE.current_task,
            "last_error": STATE.last_error,
            "last_success": STATE.last_success,
            "status_version": STATE.status_version,
            "pay_result": STATE.last_pay_result,
            "pay_verification": STATE.pay_verification,
            "pay_hosted_page": STATE.pay_hosted_page,
        }
    _schedule_ws_broadcast(payload)



def _append_output(message: str) -> None:
    if not message:
        return
    with STATE.lock:
        STATE.last_output += message
        STATE.log_version += 1
        payload = {
            "type": "log",
            "chunk": message,
            "log_version": STATE.log_version,
        }
    _schedule_ws_broadcast(payload)


def _load_current_config() -> dict[str, Any]:
    return ncs_register._load_config()


def _reload_runtime_config() -> None:
    fresh = ncs_register._load_config()
    ncs_register._CONFIG = fresh
    ncs_register.OUTLOOKMAIL_CONFIG_PATH = str(fresh.get("outlookmail_config_path", "outlookmail_accounts.txt") or "").strip()
    ncs_register.OUTLOOKMAIL_PROFILE_MODE = str(fresh.get("outlookmail_profile", "auto") or "auto").strip() or "auto"
    ncs_register.OUTLOOKMAIL_FETCH_MODE = ncs_register._normalize_outlookmail_fetch_mode(
        fresh.get("outlookmail_fetch_mode", "auto")
    )
    ncs_register.OUTLOOKMAIL_ACCOUNTS = ncs_register._build_outlookmail_accounts(
        ncs_register._load_outlookmail_accounts_from_file(ncs_register.OUTLOOKMAIL_CONFIG_PATH, silent=True)
    )
    outlook_cfg_path = Path(ncs_register.OUTLOOKMAIL_CONFIG_PATH) if ncs_register.OUTLOOKMAIL_CONFIG_PATH else None
    ncs_register.OUTLOOKMAIL_CONFIG_MTIME = (
        outlook_cfg_path.stat().st_mtime if outlook_cfg_path and outlook_cfg_path.exists() else None
    )
    ncs_register.TEMPMAIL_LOL_API_BASE = fresh.get("tempmail_lol_api_base", "https://api.tempmail.lol/v2").rstrip("/")
    ncs_register.LAMAIL_API_BASE = fresh.get("lamail_api_base", "https://maliapi.215.im/v1").rstrip("/")
    ncs_register.LAMAIL_API_KEY = str(fresh.get("lamail_api_key", "") or "").strip()
    ncs_register.LAMAIL_DOMAINS = ncs_register._as_csv_list(fresh.get("lamail_domain", ""))
    ncs_register.LAMAIL_DOMAIN_TEXT = ", ".join(ncs_register.LAMAIL_DOMAINS)
    ncs_register.DEFAULT_TOTAL_ACCOUNTS = fresh["total_accounts"]
    ncs_register.DEFAULT_PROXY = fresh["proxy"]
    ncs_register.DEFAULT_OUTPUT_FILE = fresh["output_file"]
    ncs_register.ENABLE_OAUTH = ncs_register._as_bool(fresh.get("enable_oauth", True))
    ncs_register.OAUTH_REQUIRED = ncs_register._as_bool(fresh.get("oauth_required", True))
    ncs_register.OAUTH_ISSUER = fresh["oauth_issuer"].rstrip("/")
    ncs_register.OAUTH_CLIENT_ID = fresh["oauth_client_id"]
    ncs_register.OAUTH_REDIRECT_URI = fresh["oauth_redirect_uri"]
    ncs_register.AK_FILE = fresh["ak_file"]
    ncs_register.RK_FILE = fresh["rk_file"]
    ncs_register.TOKEN_JSON_DIR = fresh["token_json_dir"]
    ncs_register.UPLOAD_API_URL = fresh["upload_api_url"]
    ncs_register.UPLOAD_API_TOKEN = fresh["upload_api_token"]
    ncs_register.UPLOAD_API_PROXY = str(fresh.get("upload_api_proxy", "") or "").strip()
    ncs_register.CPA_CLEANUP_ENABLED = ncs_register._as_bool(fresh.get("cpa_cleanup_enabled", True))
    ncs_register.CPA_UPLOAD_EVERY_N = max(1, int(fresh.get("cpa_upload_every_n", 3) or 3))
    ncs_register.MAIL_PROVIDER = str(fresh.get("mail_provider", "outlookmail")).strip().lower()
    ncs_register.REGISTER_DELAY_FACTOR = max(0.0, float(fresh.get("register_delay_factor", 1.0) or 1.0))
    ncs_register.OTP_POLL_INTERVAL_DEFAULT = max(0.2, float(fresh.get("otp_poll_interval_default", 2.0) or 2.0))
    ncs_register.OTP_POLL_INTERVAL_BY_PROVIDER = {
        "outlookmail": max(0.2, float(fresh.get("otp_poll_interval_outlookmail", ncs_register.OTP_POLL_INTERVAL_DEFAULT) or ncs_register.OTP_POLL_INTERVAL_DEFAULT)),
        "tempmail_lol": max(0.2, float(fresh.get("otp_poll_interval_tempmail_lol", 1.5) or 1.5)),
        "lamail": max(0.2, float(fresh.get("otp_poll_interval_lamail", 1.0) or 1.0)),
        "cfmail": max(0.2, float(fresh.get("otp_poll_interval_cfmail", 1.5) or 1.5)),
    }
    ncs_register._CFMAIL_CONFIG_PATH = str(fresh.get("cfmail_config_path", "zhuce5_cfmail_accounts.json")).strip()
    ncs_register.CFMAIL_PROFILE_MODE = str(fresh.get("cfmail_profile", "auto")).strip() or "auto"
    ncs_register.CFMAIL_ACCOUNTS = ncs_register._build_cfmail_accounts(
        ncs_register._load_cfmail_accounts_from_file(ncs_register._CFMAIL_CONFIG_PATH, silent=True)
    )


def _normalize_upload_api_base(raw_url: str) -> str:
    value = str(raw_url or "").strip()
    if not value:
        return ""
    normalized = ncs_register._normalize_management_api_root(value)
    if normalized:
        return f"{normalized.rstrip('/')}/auth-files"
    return value



def _save_config_file(current: dict[str, Any]) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(current, f, ensure_ascii=False, indent=2)



def _save_register_config(form: dict[str, str]) -> None:
    current = _load_current_config()
    current.update(
        {
            "proxy": form.get("proxy", current.get("proxy", "")).strip(),
            "total_accounts": max(1, int(form.get("total_accounts", current.get("total_accounts", 1)) or 1)),
            "max_workers": max(1, int(form.get("max_workers", current.get("max_workers", 3)) or 3)),
            "cpa_cleanup": str(form.get("cpa_cleanup", current.get("cpa_cleanup", False))).lower() == "true",
            "cpa_upload_every_n": max(1, int(form.get("cpa_upload_every_n", current.get("cpa_upload_every_n", 3)) or 3)),
            "run_preflight": str(form.get("run_preflight", current.get("run_preflight", True))).lower() == "true",
            "mail_provider": form.get("mail_provider", current.get("mail_provider", "outlookmail")).strip(),
            "outlookmail_config_path": form.get("outlookmail_config_path", current.get("outlookmail_config_path", "")).strip(),
            "outlookmail_profile": form.get("outlookmail_profile", current.get("outlookmail_profile", "auto")).strip() or "auto",
            "outlookmail_fetch_mode": ncs_register._normalize_outlookmail_fetch_mode(
                form.get("outlookmail_fetch_mode", current.get("outlookmail_fetch_mode", "auto"))
            ),
            "tempmail_lol_api_base": form.get("tempmail_lol_api_base", current.get("tempmail_lol_api_base", "")).strip(),
            "lamail_api_base": form.get("lamail_api_base", current.get("lamail_api_base", "")).strip(),
            "lamail_api_key": form.get("lamail_api_key", current.get("lamail_api_key", "")).strip(),
            "lamail_domain": form.get("lamail_domain", current.get("lamail_domain", "")).strip(),
            "cfmail_config_path": form.get("cfmail_config_path", current.get("cfmail_config_path", "")).strip(),
            "cfmail_profile": form.get("cfmail_profile", current.get("cfmail_profile", "")).strip(),
            "upload_api_url": _normalize_upload_api_base(form.get("upload_api_url", current.get("upload_api_url", ""))),
            "upload_api_token": form.get("upload_api_token", current.get("upload_api_token", "")).strip(),
            "upload_api_proxy": form.get("upload_api_proxy", current.get("upload_api_proxy", "")).strip(),
            "cpa_cleanup_enabled": form.get("cpa_cleanup_enabled", str(current.get("cpa_cleanup_enabled", False))).lower() == "true",
        }
    )
    current.pop("duckmail_api_base", None)
    current.pop("duckmail_bearer", None)
    current.pop("otp_poll_interval_duckmail", None)
    _save_config_file(current)



def _save_pay_config(form: dict[str, str]) -> None:
    current = _load_current_config()
    current.update(
        {
            "proxy": form.get("proxy", current.get("proxy", "")).strip(),
            "payment_generation_mode": payment_bind_app.normalize_payment_generation_mode(
                form.get("payment_generation_mode", current.get("payment_generation_mode", payment_bind_app.DEFAULT_PAYMENT_GENERATION_MODE))
            ),
            "payment_billing_name": str(form.get("payment_billing_name", current.get("payment_billing_name", "")) or "").strip(),
            "payment_billing_email": str(form.get("payment_billing_email", current.get("payment_billing_email", "")) or "").strip(),
            "payment_billing_line1": str(form.get("payment_billing_line1", current.get("payment_billing_line1", "")) or "").strip(),
            "payment_billing_city": str(form.get("payment_billing_city", current.get("payment_billing_city", "")) or "").strip(),
            "payment_billing_state": str(form.get("payment_billing_state", current.get("payment_billing_state", "")) or "").strip(),
            "payment_billing_postal_code": str(form.get("payment_billing_postal_code", current.get("payment_billing_postal_code", "")) or "").strip(),
            "payment_billing_country": str(form.get("payment_billing_country", current.get("payment_billing_country", current.get("payment_country", "US"))) or "US").strip().upper(),
            "payment_card_number": str(form.get("payment_card_number", current.get("payment_card_number", "")) or "").strip(),
            "payment_card_exp_month": str(form.get("payment_card_exp_month", current.get("payment_card_exp_month", "")) or "").strip(),
            "payment_card_exp_year": str(form.get("payment_card_exp_year", current.get("payment_card_exp_year", "")) or "").strip(),
            "payment_card_cvc": str(form.get("payment_card_cvc", current.get("payment_card_cvc", "")) or "").strip(),
        }
    )
    _save_config_file(current)


def _payment_profile_from_form(form: dict[str, str]) -> dict[str, str]:
    profile: dict[str, str] = {}
    for field in payment_bind_app.PAYMENT_PROFILE_FIELDS:
        value = str(form.get(field) or "").strip()
        if value:
            profile[field] = value
    return profile


def _merge_pay_form_with_account_profile(form: dict[str, str]) -> tuple[dict[str, str], str]:
    merged = dict(form)
    access_token = str(merged.get("payment_access_token") or "").strip()
    account = ACCOUNT_STORE.find_account_by_access_token(access_token)
    if not account:
        return merged, ""
    profile = account.get("payment_profile") if isinstance(account.get("payment_profile"), dict) else {}
    for field in payment_bind_app.PAYMENT_PROFILE_FIELDS:
        if str(merged.get(field) or "").strip():
            continue
        value = str(profile.get(field) or "").strip() if isinstance(profile, dict) else ""
        if value:
            merged[field] = value
    return merged, str(account.get("email") or "").strip()


def _save_pay_profile_for_account(email: str, form: dict[str, str]) -> bool:
    target = str(email or "").strip()
    if not target:
        return False
    return ACCOUNT_STORE.save_payment_profile(target, _payment_profile_from_form(form))



def _apply_register_form_runtime(form: dict[str, str]) -> None:
    ncs_register.OUTLOOKMAIL_CONFIG_PATH = form.get("outlookmail_config_path", ncs_register.OUTLOOKMAIL_CONFIG_PATH).strip()
    ncs_register.OUTLOOKMAIL_PROFILE_MODE = form.get("outlookmail_profile", ncs_register.OUTLOOKMAIL_PROFILE_MODE).strip() or "auto"
    ncs_register.OUTLOOKMAIL_FETCH_MODE = ncs_register._normalize_outlookmail_fetch_mode(
        form.get("outlookmail_fetch_mode", ncs_register.OUTLOOKMAIL_FETCH_MODE)
    )
    ncs_register.OUTLOOKMAIL_ACCOUNTS = ncs_register._build_outlookmail_accounts(
        ncs_register._load_outlookmail_accounts_from_file(ncs_register.OUTLOOKMAIL_CONFIG_PATH, silent=True)
    )
    outlook_cfg_path = Path(ncs_register.OUTLOOKMAIL_CONFIG_PATH) if ncs_register.OUTLOOKMAIL_CONFIG_PATH else None
    ncs_register.OUTLOOKMAIL_CONFIG_MTIME = (
        outlook_cfg_path.stat().st_mtime if outlook_cfg_path and outlook_cfg_path.exists() else None
    )
    ncs_register.TEMPMAIL_LOL_API_BASE = form.get("tempmail_lol_api_base", ncs_register.TEMPMAIL_LOL_API_BASE).strip().rstrip("/")
    ncs_register.LAMAIL_API_BASE = form.get("lamail_api_base", ncs_register.LAMAIL_API_BASE).strip().rstrip("/")
    ncs_register.LAMAIL_API_KEY = form.get("lamail_api_key", ncs_register.LAMAIL_API_KEY).strip()
    ncs_register.LAMAIL_DOMAINS = ncs_register._as_csv_list(form.get("lamail_domain", ",".join(ncs_register.LAMAIL_DOMAINS)))
    ncs_register.LAMAIL_DOMAIN_TEXT = ", ".join(ncs_register.LAMAIL_DOMAINS)
    ncs_register.MAIL_PROVIDER = form.get("mail_provider", ncs_register.MAIL_PROVIDER).strip().lower()
    ncs_register.UPLOAD_API_URL = _normalize_upload_api_base(form.get("upload_api_url", ncs_register.UPLOAD_API_URL))
    ncs_register.UPLOAD_API_TOKEN = form.get("upload_api_token", ncs_register.UPLOAD_API_TOKEN).strip()
    ncs_register.UPLOAD_API_PROXY = form.get("upload_api_proxy", ncs_register.UPLOAD_API_PROXY).strip()
    ncs_register.CPA_CLEANUP_ENABLED = str(form.get("cpa_cleanup_enabled", "false")).lower() == "true"
    ncs_register.CPA_UPLOAD_EVERY_N = max(1, int(form.get("cpa_upload_every_n", ncs_register.CPA_UPLOAD_EVERY_N) or 1))
    ncs_register.DEFAULT_PROXY = form.get("proxy", ncs_register.DEFAULT_PROXY or "").strip()
    ncs_register.DEFAULT_TOTAL_ACCOUNTS = max(1, int(form.get("total_accounts", ncs_register.DEFAULT_TOTAL_ACCOUNTS) or 1))


def _list_account_options() -> list[dict[str, str]]:
    return [
        {
            "email": str(row.get("email") or ""),
            "account_id": str(row.get("account_id") or ""),
            "expired": str(row.get("expired") or ""),
            "access_token": str(row.get("access_token") or ""),
        }
        for row in ACCOUNT_STORE.list_accounts()
    ]



def _account_summary(row: dict[str, Any]) -> dict[str, Any]:
    cookies = row.get("cookies") if isinstance(row.get("cookies"), dict) else {}
    return {
        "email": str(row.get("email") or ""),
        "account_id": str(row.get("account_id") or ""),
        "expired": str(row.get("expired") or ""),
        "last_refresh": str(row.get("last_refresh") or ""),
        "type": str(row.get("type") or "codex"),
        "access_token": str(row.get("access_token") or ""),
        "tags": row.get("tags") if isinstance(row.get("tags"), list) else [],
        "has_access_token": bool(str(row.get("access_token") or "").strip()),
        "has_refresh_token": bool(str(row.get("refresh_token") or "").strip()),
        "has_session_token": bool(str(row.get("session_token") or "").strip()),
        "has_csrf_token": bool(str(row.get("csrf_token") or "").strip()),
        "cookies_count": len(cookies),
        "has_checkout_context": bool(str(row.get("session_token") or "").strip()) and bool(cookies),
    }


def _sync_forms_from_config_if_idle() -> None:
    with STATE.lock:
        if STATE.running:
            return
        STATE.last_form = RegisterState._default_register_form()
        STATE.last_pay_form = RegisterState._default_pay_form()


def _render_index(request: Request, *, active_tab: str = "register") -> HTMLResponse:
    _reload_runtime_config()
    _sync_forms_from_config_if_idle()
    cfg = _load_current_config()
    accounts = ACCOUNT_STORE.list_accounts()
    return templates.TemplateResponse(
        request,
        "register.html",
        {
            "mail_provider": cfg.get("mail_provider", ncs_register.MAIL_PROVIDER),
            "default_proxy": cfg.get("proxy", ncs_register.DEFAULT_PROXY or ""),
            "default_total_accounts": cfg.get("total_accounts", ncs_register.DEFAULT_TOTAL_ACCOUNTS),
            "upload_api_configured": bool(cfg.get("upload_api_url", ncs_register.UPLOAD_API_URL)),
            "state": STATE,
            "active_tab": active_tab,
            "account_options": _list_account_options(),
            "account_rows": [_account_summary(row) for row in accounts],
        },
    )


def _watch_register_queue(queue: Queue, process: Process) -> None:
    while True:
        item = queue.get()
        kind = item[0]
        if kind == "log":
            _append_output(item[1])
            continue
        if kind == "done":
            with STATE.lock:
                STATE.running = False
                STATE.current_task = ""
                STATE.last_success = bool(item[1])
                STATE.last_error = str(item[2] or "")
                STATE.register_process = None
                STATE.register_queue = None
                STATE.register_log_thread = None
            _notify_status_update()
            if process.is_alive():
                process.join(timeout=0.2)
            break


def _register_worker(queue: Queue, *, proxy: Optional[str], total_accounts: int, max_workers: int,
                     cpa_cleanup: bool, cpa_upload_every_n: int, run_preflight: bool,
                     config_overrides: Optional[dict[str, str]] = None) -> None:
    writer = _QueueLogWriter(queue)
    success = False
    error_text = ""
    try:
        with redirect_stdout(writer), redirect_stderr(writer):
            if config_overrides:
                _apply_register_form_runtime(config_overrides)
            _configure_fast_register_runtime()
            provider = ncs_register.MAIL_PROVIDER
            if run_preflight:
                passed = ncs_register._quick_preflight(proxy=proxy, provider=provider)
                if not passed:
                    raise RuntimeError("预检未通过，请更换代理或降低并发后重试")
            ncs_register.run_batch(
                total_accounts=total_accounts,
                output_file=ncs_register.DEFAULT_OUTPUT_FILE,
                max_workers=max_workers,
                proxy=proxy,
                cpa_cleanup=cpa_cleanup,
                cpa_upload_every_n=cpa_upload_every_n,
            )
        success = True
    except Exception as exc:
        error_text = f"{exc}\n{traceback.format_exc()}"
    finally:
        writer.flush()
        queue.put(("done", success, error_text))


def _run_pay_job(*, pay_form: dict[str, str]) -> None:
    success = False
    error_text = ""
    try:
        access_token = str(pay_form.get("payment_access_token") or "").strip()
        if not access_token:
            raise RuntimeError("缺少 access token")

        matched_account = ACCOUNT_STORE.find_account_by_access_token(access_token)
        account = dict(matched_account) if matched_account else {"email": "", "access_token": access_token}
        account["access_token"] = access_token

        cfg = payment_bind_app.load_config()
        cfg.update(
            {
                "proxy": pay_form.get("proxy", "").strip(),
                "payment_generation_mode": payment_bind_app.normalize_payment_generation_mode(
                    pay_form.get("payment_generation_mode")
                ),
                "payment_billing_name": str(pay_form.get("payment_billing_name") or "").strip(),
                "payment_billing_email": str(pay_form.get("payment_billing_email") or "").strip(),
                "payment_billing_line1": str(pay_form.get("payment_billing_line1") or "").strip(),
                "payment_billing_city": str(pay_form.get("payment_billing_city") or "").strip(),
                "payment_billing_state": str(pay_form.get("payment_billing_state") or "").strip(),
                "payment_billing_postal_code": str(pay_form.get("payment_billing_postal_code") or "").strip(),
                "payment_billing_country": str(pay_form.get("payment_billing_country") or cfg.get("payment_country") or "US").strip().upper(),
                "payment_card_number": str(pay_form.get("payment_card_number") or "").strip(),
                "payment_card_exp_month": str(pay_form.get("payment_card_exp_month") or "").strip(),
                "payment_card_exp_year": str(pay_form.get("payment_card_exp_year") or "").strip(),
                "payment_card_cvc": str(pay_form.get("payment_card_cvc") or "").strip(),
            }
        )
        generation_mode = payment_bind_app.normalize_payment_generation_mode(cfg.get("payment_generation_mode"))
        binder = payment_bind_app.PaymentBinder(cfg, account)
        checkout = binder.create_checkout(mode=generation_mode)
        checkout_url = str(checkout.get("checkout_url") or "").strip()
        stripe_hosted_url = str(checkout.get("stripe_hosted_url") or "").strip()
        primary_url = str(checkout.get("primary_url") or "").strip()
        primary_label = str(checkout.get("primary_label") or ("Stripe Hosted 地址" if generation_mode == "stripe_hosted" else "Checkout 地址"))
        verification = {
            "required": bool(primary_url),
            "status": "awaiting_human_verification" if primary_url else "host_page_unavailable",
            "reason": "stripe_hosted_url" if generation_mode == "stripe_hosted" else "checkout_url",
            "message": (
                "已生成 Stripe Hosted 支付链接，请在新页面完成验证。"
                if generation_mode == "stripe_hosted" and primary_url
                else "已生成 checkout 地址，请在新页面完成验证。"
                if primary_url else "未生成可用的支付链接。"
            ),
            "verification_url": primary_url,
            "render_mode": "external_link",
        }
        result = {
            "email": str(account.get("email") or ""),
            "payment_generation_mode": generation_mode,
            "primary_url": primary_url,
            "primary_label": primary_label,
            "checkout_session_id": checkout.get("checkout_session_id") or "",
            "checkout": {
                "checkout_session_id": checkout.get("checkout_session_id") or "",
                "client_secret": checkout.get("client_secret") or "",
                "publishable_key": checkout.get("publishable_key") or "",
                "expected_amount": checkout.get("expected_amount"),
                "checkout_url": checkout_url,
                "stripe_hosted_url": stripe_hosted_url,
            },
            "confirm": {},
            "confirm_status": {
                "checkout_status": "open",
                "payment_status": "pending_verification",
                "setup_intent_id": "",
                "setup_intent_client_secret": "",
                "setup_intent_status": "",
                "setup_intent_next_action": "stripe_hosted" if generation_mode == "stripe_hosted" else "checkout_only",
                "setup_intent_next_action_url": primary_url,
                "requires_action": bool(primary_url),
                "return_url": primary_url,
                "final_state": "primary_url_generated" if primary_url else "primary_url_missing",
                "final_message": verification["message"],
                "is_success": False,
            },
            "verification": verification,
            "strip_host_page_url": stripe_hosted_url,
        }
        hosted_page = {
            "mode": generation_mode,
            "primary_label": primary_label,
            "primary_url": primary_url,
            "checkout_session_id": str(checkout.get("checkout_session_id") or ""),
            "url": primary_url,
            "checkout_url": checkout_url,
            "stripe_hosted_url": stripe_hosted_url,
            "strip_host_page_url": stripe_hosted_url,
            "publishable_key_prefix": str(checkout.get("publishable_key") or "")[:18],
            "expected_amount": checkout.get("expected_amount"),
        }
        with STATE.lock:
            STATE.last_pay_result = result
            STATE.pay_verification = verification
            STATE.pay_hosted_page = hosted_page
        success = bool(primary_url)
        if not primary_url:
            error_text = "未生成可用的支付链接"
    except Exception as exc:
        error_text = f"{exc}\n{traceback.format_exc()}"
    finally:
        with STATE.lock:
            STATE.running = False
            STATE.current_task = ""
            STATE.last_error = error_text
            STATE.last_success = success
            STATE.pay_thread = None
        _notify_status_update()


class _ImmediateQueue:
    def put(self, item: tuple[str, str]) -> None:
        if item[0] == "log":
            _append_output(item[1])


@app.get("/", response_class=HTMLResponse)
def index(request: Request, tab: str = "register"):
    return _render_index(request, active_tab=tab)


@app.get("/status")
def get_status():
    with STATE.lock:
        return JSONResponse(
            {
                "running": STATE.running,
                "current_task": STATE.current_task,
                "last_output": STATE.last_output,
                "last_error": STATE.last_error,
                "last_success": STATE.last_success,
                "log_version": STATE.log_version,
                "status_version": STATE.status_version,
                "pay_result": STATE.last_pay_result,
                "pay_verification": STATE.pay_verification,
                "pay_hosted_page": STATE.pay_hosted_page,
            }
        )


@app.websocket("/ws")
async def websocket_status(websocket: WebSocket):
    await websocket.accept()
    with STATE.lock:
        STATE.ws_loop = asyncio.get_running_loop()
        STATE.ws_clients.add(websocket)
        init_payload = {
            "type": "init",
            "running": STATE.running,
            "current_task": STATE.current_task,
            "last_output": STATE.last_output,
            "last_error": STATE.last_error,
            "last_success": STATE.last_success,
            "log_version": STATE.log_version,
            "status_version": STATE.status_version,
            "pay_result": STATE.last_pay_result,
            "pay_verification": STATE.pay_verification,
            "pay_hosted_page": STATE.pay_hosted_page,
        }
    await websocket.send_text(json.dumps(init_payload, ensure_ascii=False))
    try:
        while True:
            await asyncio.sleep(30)
            await websocket.send_text(json.dumps({"type": "ping"}, ensure_ascii=False))
    except WebSocketDisconnect:
        with STATE.lock:
            STATE.ws_clients.discard(websocket)
    except Exception:
        with STATE.lock:
            STATE.ws_clients.discard(websocket)


@app.post("/accounts/export")
def export_account(email: str = Form("")):
    target = str(email or "").strip()
    if not target:
        with STATE.lock:
            STATE.last_error = "导出失败：缺少账号邮箱"
        return RedirectResponse(url="/?tab=accounts", status_code=303)
    try:
        path = ACCOUNT_STORE.export_account_json(target)
        with STATE.lock:
            STATE.last_error = ""
        _append_output(f"\n[ACCOUNTS] 已导出账号文件: {path.name}\n")
        return FileResponse(path=str(path), filename=path.name, media_type="application/json")
    except Exception as exc:
        with STATE.lock:
            STATE.last_error = f"导出失败: {exc}"
        return RedirectResponse(url="/?tab=accounts", status_code=303)


@app.post("/accounts/export-batch")
def export_accounts_batch(emails: list[str] = Form(default=[])):
    targets = [str(email or "").strip() for email in emails if str(email or "").strip()]
    if not targets:
        with STATE.lock:
            STATE.last_error = "批量导出失败：没有选中任何账号"
        return RedirectResponse(url="/?tab=accounts", status_code=303)
    try:
        zip_path = ACCOUNT_STORE.export_accounts_zip(targets)
        with STATE.lock:
            STATE.last_error = ""
        _append_output(f"\n[ACCOUNTS] 已批量导出账号压缩包: {zip_path.name} ({len(targets)} 个)\n")
        return FileResponse(path=str(zip_path), filename=zip_path.name, media_type="application/zip")
    except Exception as exc:
        with STATE.lock:
            STATE.last_error = f"批量导出失败: {exc}"
        return RedirectResponse(url="/?tab=accounts", status_code=303)


@app.post("/accounts/delete")
def delete_account(email: str = Form("")):
    target = str(email or "").strip()
    if not target:
        with STATE.lock:
            STATE.last_error = "删除失败：缺少账号邮箱"
        return RedirectResponse(url="/?tab=accounts", status_code=303)
    deleted = ACCOUNT_STORE.delete_account(target)
    with STATE.lock:
        if deleted:
            STATE.last_error = ""
        else:
            STATE.last_error = f"删除失败：账号不存在 {target}"
    if deleted:
        _append_output(f"\n[ACCOUNTS] 已删除账号: {target}\n")
    return RedirectResponse(url="/?tab=accounts", status_code=303)


@app.post("/accounts/delete-batch")
def delete_accounts_batch(emails: list[str] = Form(default=[])):
    targets = list(dict.fromkeys(str(email or "").strip() for email in emails if str(email or "").strip()))
    if not targets:
        with STATE.lock:
            STATE.last_error = "批量删除失败：没有选中任何账号"
        return RedirectResponse(url="/?tab=accounts", status_code=303)
    deleted_count = ACCOUNT_STORE.delete_accounts(targets)
    with STATE.lock:
        if deleted_count > 0:
            STATE.last_error = ""
        else:
            STATE.last_error = "批量删除失败：选中的账号均不存在"
    if deleted_count > 0:
        missing_count = max(0, len(targets) - deleted_count)
        summary = f"\n[ACCOUNTS] 已批量删除账号: {deleted_count} 个"
        if missing_count:
            summary += f"，其中 {missing_count} 个不存在"
        summary += "\n"
        _append_output(summary)
    return RedirectResponse(url="/?tab=accounts", status_code=303)


@app.post("/register/save", response_class=HTMLResponse)
def save_register_config(
    proxy: str = Form(""),
    total_accounts: int = Form(1),
    max_workers: int = Form(1),
    cpa_cleanup: str = Form("false"),
    cpa_upload_every_n: int = Form(1),
    run_preflight: str = Form("true"),
    mail_provider: str = Form("outlookmail"),
    outlookmail_config_path: str = Form(""),
    outlookmail_profile: str = Form("auto"),
    outlookmail_fetch_mode: str = Form("auto"),
    tempmail_lol_api_base: str = Form(""),
    lamail_api_base: str = Form(""),
    lamail_api_key: str = Form(""),
    lamail_domain: str = Form(""),
    cfmail_config_path: str = Form(""),
    cfmail_profile: str = Form("auto"),
    upload_api_url: str = Form(""),
    upload_api_token: str = Form(""),
    upload_api_proxy: str = Form(""),
    cpa_cleanup_enabled: str = Form("false"),
):
    with STATE.lock:
        STATE.last_form.update(
            {
                "proxy": proxy,
                "total_accounts": str(total_accounts),
                "max_workers": str(max_workers),
                "cpa_cleanup": cpa_cleanup,
                "cpa_upload_every_n": str(cpa_upload_every_n),
                "run_preflight": run_preflight,
                "mail_provider": mail_provider,
                "outlookmail_config_path": outlookmail_config_path,
                "outlookmail_profile": outlookmail_profile,
                "outlookmail_fetch_mode": outlookmail_fetch_mode,
                "tempmail_lol_api_base": tempmail_lol_api_base,
                "lamail_api_base": lamail_api_base,
                "lamail_api_key": lamail_api_key,
                "lamail_domain": lamail_domain,
                "cfmail_config_path": cfmail_config_path,
                "cfmail_profile": cfmail_profile,
                "upload_api_url": upload_api_url,
                "upload_api_token": upload_api_token,
                "upload_api_proxy": upload_api_proxy,
                "cpa_cleanup_enabled": cpa_cleanup_enabled,
            }
        )
        snapshot = dict(STATE.last_form)
        STATE.last_error = ""
    _save_register_config(snapshot)
    _reload_runtime_config()
    _append_output("\n[WEB] 注册配置已保存\n")
    _notify_status_update()
    return RedirectResponse(url="/?tab=register", status_code=303)


@app.post("/register/start", response_class=HTMLResponse)
def start_register(
    proxy: str = Form(""),
    total_accounts: int = Form(1),
    max_workers: int = Form(1),
    cpa_cleanup: str = Form("false"),
    cpa_upload_every_n: int = Form(1),
    run_preflight: str = Form("true"),
    mail_provider: str = Form("outlookmail"),
    outlookmail_config_path: str = Form(""),
    outlookmail_profile: str = Form("auto"),
    outlookmail_fetch_mode: str = Form("auto"),
    tempmail_lol_api_base: str = Form(""),
    lamail_api_base: str = Form(""),
    lamail_api_key: str = Form(""),
    lamail_domain: str = Form(""),
    cfmail_config_path: str = Form(""),
    cfmail_profile: str = Form("auto"),
    upload_api_url: str = Form(""),
    upload_api_token: str = Form(""),
    upload_api_proxy: str = Form(""),
    cpa_cleanup_enabled: str = Form("false"),
):
    form_data = {
        "proxy": proxy,
        "total_accounts": str(total_accounts),
        "max_workers": str(max_workers),
        "cpa_cleanup": cpa_cleanup,
        "cpa_upload_every_n": str(cpa_upload_every_n),
        "run_preflight": run_preflight,
        "mail_provider": mail_provider,
        "outlookmail_config_path": outlookmail_config_path,
        "outlookmail_profile": outlookmail_profile,
        "outlookmail_fetch_mode": outlookmail_fetch_mode,
        "tempmail_lol_api_base": tempmail_lol_api_base,
        "lamail_api_base": lamail_api_base,
        "lamail_api_key": lamail_api_key,
        "lamail_domain": lamail_domain,
        "cfmail_config_path": cfmail_config_path,
        "cfmail_profile": cfmail_profile,
        "upload_api_url": upload_api_url,
        "upload_api_token": upload_api_token,
        "upload_api_proxy": upload_api_proxy,
        "cpa_cleanup_enabled": cpa_cleanup_enabled,
    }

    with STATE.lock:
        if STATE.running:
            STATE.last_error = "当前已有任务在执行，请等待完成后再启动新任务"
            STATE.last_form.update(form_data)
            _notify_status_update()
            return RedirectResponse(url="/?tab=register", status_code=303)
        STATE.running = True
        STATE.current_task = "register"
        STATE.last_output = ""
        STATE.last_error = ""
        STATE.last_success = False
        STATE.log_version += 1
        STATE.last_form.update(form_data)
    _notify_status_update()

    queue: Queue = Queue()
    process = Process(
        target=_register_worker,
        kwargs={
            "queue": queue,
            "proxy": proxy.strip() or None,
            "total_accounts": max(1, int(total_accounts)),
            "max_workers": max(1, int(max_workers)),
            "cpa_cleanup": str(cpa_cleanup).lower() == "true",
            "cpa_upload_every_n": max(1, int(cpa_upload_every_n)),
            "run_preflight": str(run_preflight).lower() == "true",
            "config_overrides": dict(form_data),
        },
        daemon=True,
    )
    process.start()

    with STATE.lock:
        STATE.register_process = process
        STATE.register_queue = queue
        STATE.register_log_thread = threading.Thread(target=_watch_register_queue, args=(queue, process), daemon=True)
        STATE.register_log_thread.start()

    _append_output("[WEB] 注册任务已启动，正在实时输出日志...\n")
    return RedirectResponse(url="/?tab=register", status_code=303)


@app.post("/register/stop")
def stop_register_task():
    with STATE.lock:
        process = STATE.register_process
        if process and process.is_alive():
            process.terminate()
            process.join(timeout=1)
            STATE.running = False
            STATE.current_task = ""
            STATE.last_success = False
            STATE.last_error = "注册任务已被人工暂停/终止"
            STATE.register_process = None
            STATE.register_queue = None
            STATE.register_log_thread = None
    _append_output("\n[WEB] 已强制终止注册任务\n")
    _notify_status_update()
    return RedirectResponse(url="/?tab=register", status_code=303)


@app.post("/pay/save", response_class=HTMLResponse)
def save_pay_config(
    proxy: str = Form(""),
    payment_access_token: str = Form(""),
    payment_generation_mode: str = Form(payment_bind_app.DEFAULT_PAYMENT_GENERATION_MODE),
    payment_billing_name: str = Form(""),
    payment_billing_email: str = Form(""),
    payment_billing_line1: str = Form(""),
    payment_billing_city: str = Form(""),
    payment_billing_state: str = Form(""),
    payment_billing_postal_code: str = Form(""),
    payment_billing_country: str = Form("US"),
    payment_card_number: str = Form(""),
    payment_card_exp_month: str = Form(""),
    payment_card_exp_year: str = Form(""),
    payment_card_cvc: str = Form(""),
):
    pay_form = {
        "proxy": proxy,
        "payment_access_token": payment_access_token,
        "payment_generation_mode": payment_bind_app.normalize_payment_generation_mode(payment_generation_mode),
        "payment_billing_name": payment_billing_name,
        "payment_billing_email": payment_billing_email,
        "payment_billing_line1": payment_billing_line1,
        "payment_billing_city": payment_billing_city,
        "payment_billing_state": payment_billing_state,
        "payment_billing_postal_code": payment_billing_postal_code,
        "payment_billing_country": payment_billing_country,
        "payment_card_number": payment_card_number,
        "payment_card_exp_month": payment_card_exp_month,
        "payment_card_exp_year": payment_card_exp_year,
        "payment_card_cvc": payment_card_cvc,
    }
    hydrated_form, matched_email = _merge_pay_form_with_account_profile(pay_form)
    with STATE.lock:
        STATE.last_pay_form.update(hydrated_form)
        snapshot = dict(STATE.last_pay_form)
        STATE.last_error = ""
    _append_output("\n[WEB] Pay 配置已保存\n")
    _notify_status_update()
    _save_pay_config(snapshot)
    if matched_email:
        _save_pay_profile_for_account(matched_email, snapshot)
    _reload_runtime_config()
    return RedirectResponse(url="/?tab=pay", status_code=303)


@app.post("/pay/start", response_class=HTMLResponse)
def start_pay(
    proxy: str = Form(""),
    payment_access_token: str = Form(""),
    payment_generation_mode: str = Form(payment_bind_app.DEFAULT_PAYMENT_GENERATION_MODE),
    payment_billing_name: str = Form(""),
    payment_billing_email: str = Form(""),
    payment_billing_line1: str = Form(""),
    payment_billing_city: str = Form(""),
    payment_billing_state: str = Form(""),
    payment_billing_postal_code: str = Form(""),
    payment_billing_country: str = Form("US"),
    payment_card_number: str = Form(""),
    payment_card_exp_month: str = Form(""),
    payment_card_exp_year: str = Form(""),
    payment_card_cvc: str = Form(""),
):
    pay_form = {
        "proxy": proxy,
        "payment_access_token": payment_access_token,
        "payment_generation_mode": payment_bind_app.normalize_payment_generation_mode(payment_generation_mode),
        "payment_billing_name": payment_billing_name,
        "payment_billing_email": payment_billing_email,
        "payment_billing_line1": payment_billing_line1,
        "payment_billing_city": payment_billing_city,
        "payment_billing_state": payment_billing_state,
        "payment_billing_postal_code": payment_billing_postal_code,
        "payment_billing_country": payment_billing_country,
        "payment_card_number": payment_card_number,
        "payment_card_exp_month": payment_card_exp_month,
        "payment_card_exp_year": payment_card_exp_year,
        "payment_card_cvc": payment_card_cvc,
    }
    hydrated_form, matched_email = _merge_pay_form_with_account_profile(pay_form)

    with STATE.lock:
        if STATE.running:
            STATE.last_error = "当前已有任务在执行，请等待完成后再启动新任务"
            STATE.last_pay_form.update(hydrated_form)
            _notify_status_update()
            return RedirectResponse(url="/?tab=pay", status_code=303)
        STATE.running = True
        STATE.current_task = "pay"
        STATE.last_output = ""
        STATE.last_error = ""
        STATE.last_success = False
        STATE.last_pay_result = {}
        STATE.pay_verification = {}
        STATE.pay_hosted_page = {}
        STATE.log_version += 1
        STATE.last_pay_form.update(hydrated_form)
        job_form = dict(STATE.last_pay_form)
    _notify_status_update()

    if matched_email:
        _save_pay_profile_for_account(matched_email, job_form)

    thread = threading.Thread(target=_run_pay_job, kwargs={"pay_form": job_form}, daemon=True)
    with STATE.lock:
        STATE.pay_thread = thread
    thread.start()
    return RedirectResponse(url="/?tab=pay", status_code=303)


@app.post("/pay/stop")
def stop_pay_task():
    with STATE.lock:
        running_pay = STATE.running and STATE.current_task == "pay"
        if not running_pay:
            STATE.last_error = "当前没有正在执行的 Pay 任务"
            return RedirectResponse(url="/?tab=pay", status_code=303)
        STATE.running = False
        STATE.current_task = ""
        STATE.last_success = False
        STATE.last_error = "Pay 任务已被人工停止，当前页面表单数据仍已保留"
        STATE.pay_thread = None
    _append_output("\n[WEB] 已停止 Pay 任务，表单内容已保留，可直接再次保存或重启。\n")
    _notify_status_update()
    return RedirectResponse(url="/?tab=pay", status_code=303)


@app.post("/pay/verification/continue")
def continue_pay_verification():
    with STATE.lock:
        verification = dict(STATE.pay_verification or {})
        result = dict(STATE.last_pay_result or {})
        if not verification.get("required"):
            return JSONResponse({"ok": False, "message": "当前没有待处理的人工验证任务"}, status_code=400)
        access_token = str(STATE.last_pay_form.get("payment_access_token") or "").strip()
        matched_email = ""
        matched_account = ACCOUNT_STORE.find_account_by_access_token(access_token)
        if matched_account:
            matched_email = str(matched_account.get("email") or "").strip()
        if matched_email:
            ACCOUNT_STORE.mark_account_team_enabled(matched_email)
            result["team_tagged_email"] = matched_email
        verification["status"] = "manual_verification_acknowledged"
        verification["message"] = f"已确认人工验证完成{('，并已为账号打上 TEAM 标签' if matched_email else '')}。"
        result["verification"] = verification
        STATE.pay_verification = verification
        STATE.last_pay_result = result
        STATE.last_success = True
        STATE.last_error = ""
    _notify_status_update()
    return JSONResponse({"ok": True, "verification": verification, "pay_result": result})
