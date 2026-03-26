from __future__ import annotations

import json
import threading
import traceback
from contextlib import redirect_stdout, redirect_stderr
from multiprocessing import Process, Queue
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import ncs_register, payment_bind_app
from app.account_store import AccountStore
from app.address_generator import generate_billing_address

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
        self.register_process: Optional[Process] = None
        self.register_log_thread: Optional[threading.Thread] = None
        self.register_queue: Optional[Queue] = None

    @staticmethod
    def _default_register_form() -> dict[str, str]:
        cfg = ncs_register._load_config()
        return {
            "proxy": str(cfg.get("proxy", ncs_register.DEFAULT_PROXY or "")),
            "total_accounts": str(cfg.get("total_accounts", ncs_register.DEFAULT_TOTAL_ACCOUNTS)),
            "max_workers": "3",
            "cpa_cleanup": "true" if ncs_register.CPA_CLEANUP_ENABLED else "false",
            "cpa_upload_every_n": str(cfg.get("cpa_upload_every_n", ncs_register.CPA_UPLOAD_EVERY_N)),
            "run_preflight": "true",
            "mail_provider": str(cfg.get("mail_provider", ncs_register.MAIL_PROVIDER)),
            "duckmail_api_base": str(cfg.get("duckmail_api_base", ncs_register.DUCKMAIL_API_BASE)),
            "duckmail_bearer": str(cfg.get("duckmail_bearer", ncs_register.DUCKMAIL_BEARER)),
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
        cfg = ncs_register._load_config()
        return {
            "pay_email": "",
            "proxy": str(cfg.get("proxy", "")),
            "payment_country": str(cfg.get("payment_country", "US")),
            "payment_card_number": "",
            "payment_card_exp_month": "",
            "payment_card_exp_year": "",
            "payment_card_cvc": "",
            "address_mode": "auto",
            "payment_billing_name": "",
            "payment_billing_line1": "",
            "payment_billing_city": "",
            "payment_billing_state": "",
            "payment_billing_postal_code": "",
        }


class _QueueLogWriter:
    def __init__(self, queue: Queue) -> None:
        self.queue = queue

    def write(self, data: str) -> int:
        if data:
            self.queue.put(("log", data))
            return len(data)
        return 0

    def flush(self) -> None:
        return None


STATE = RegisterState()


def _load_current_config() -> dict[str, Any]:
    return ncs_register._load_config()


def _reload_runtime_config() -> None:
    fresh = ncs_register._load_config()
    ncs_register._CONFIG = fresh
    ncs_register.DUCKMAIL_API_BASE = fresh["duckmail_api_base"]
    ncs_register.DUCKMAIL_BEARER = fresh["duckmail_bearer"]
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
    ncs_register.MAIL_PROVIDER = str(fresh.get("mail_provider", "duckmail")).strip().lower()
    ncs_register.REGISTER_DELAY_FACTOR = max(0.0, float(fresh.get("register_delay_factor", 1.0) or 1.0))
    ncs_register.OTP_POLL_INTERVAL_DEFAULT = max(0.2, float(fresh.get("otp_poll_interval_default", 2.0) or 2.0))
    ncs_register.OTP_POLL_INTERVAL_BY_PROVIDER = {
        "duckmail": max(0.2, float(fresh.get("otp_poll_interval_duckmail", ncs_register.OTP_POLL_INTERVAL_DEFAULT) or ncs_register.OTP_POLL_INTERVAL_DEFAULT)),
        "tempmail_lol": max(0.2, float(fresh.get("otp_poll_interval_tempmail_lol", 1.5) or 1.5)),
        "lamail": max(0.2, float(fresh.get("otp_poll_interval_lamail", 1.0) or 1.0)),
        "cfmail": max(0.2, float(fresh.get("otp_poll_interval_cfmail", 1.5) or 1.5)),
    }
    ncs_register._CFMAIL_CONFIG_PATH = str(fresh.get("cfmail_config_path", "zhuce5_cfmail_accounts.json")).strip()
    ncs_register.CFMAIL_PROFILE_MODE = str(fresh.get("cfmail_profile", "auto")).strip() or "auto"
    ncs_register.CFMAIL_ACCOUNTS = ncs_register._build_cfmail_accounts(
        ncs_register._load_cfmail_accounts_from_file(ncs_register._CFMAIL_CONFIG_PATH, silent=True)
    )


def _save_current_config(form: dict[str, str]) -> None:
    current = _load_current_config()
    upload_api_raw = form.get("upload_api_url", current.get("upload_api_url", "")).strip()
    upload_api_base = ""
    if upload_api_raw:
        parsed = ncs_register.urlparse(upload_api_raw)
        if parsed.scheme and parsed.netloc:
            upload_api_base = f"{parsed.scheme}://{parsed.netloc}"
        else:
            upload_api_base = upload_api_raw
    current.update(
        {
            "proxy": form.get("proxy", current.get("proxy", "")).strip(),
            "mail_provider": form.get("mail_provider", current.get("mail_provider", "duckmail")).strip(),
            "duckmail_api_base": form.get("duckmail_api_base", current.get("duckmail_api_base", "")).strip(),
            "duckmail_bearer": form.get("duckmail_bearer", current.get("duckmail_bearer", "")).strip(),
            "tempmail_lol_api_base": form.get("tempmail_lol_api_base", current.get("tempmail_lol_api_base", "")).strip(),
            "lamail_api_base": form.get("lamail_api_base", current.get("lamail_api_base", "")).strip(),
            "lamail_api_key": form.get("lamail_api_key", current.get("lamail_api_key", "")).strip(),
            "lamail_domain": form.get("lamail_domain", current.get("lamail_domain", "")).strip(),
            "cfmail_config_path": form.get("cfmail_config_path", current.get("cfmail_config_path", "")).strip(),
            "cfmail_profile": form.get("cfmail_profile", current.get("cfmail_profile", "")).strip(),
            "upload_api_url": upload_api_base,
            "upload_api_token": form.get("upload_api_token", current.get("upload_api_token", "")).strip(),
            "upload_api_proxy": form.get("upload_api_proxy", current.get("upload_api_proxy", "")).strip(),
            "cpa_cleanup_enabled": form.get("cpa_cleanup_enabled", str(current.get("cpa_cleanup_enabled", False))).lower() == "true",
            "cpa_upload_every_n": max(1, int(form.get("cpa_upload_every_n", current.get("cpa_upload_every_n", 3)) or 3)),
            "payment_country": form.get("payment_country", current.get("payment_country", "US")).strip(),
        }
    )
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(current, f, ensure_ascii=False, indent=2)


def _list_account_options() -> list[dict[str, str]]:
    return [
        {
            "email": str(row.get("email") or ""),
            "account_id": str(row.get("account_id") or ""),
            "expired": str(row.get("expired") or ""),
        }
        for row in ACCOUNT_STORE.list_accounts()
    ]


def _fetch_random_address(country_code: str) -> dict[str, Any]:
    code = str(country_code or "US").strip().upper() or "US"
    try:
        address = generate_billing_address(code)
        with STATE.lock:
            STATE.last_output += (
                f"\n[PAY] 本地地址生成: country={code}, city={str(address.get('city') or '-')}, "
                f"state={str(address.get('state') or '-')}, postal_code={str(address.get('postal_code') or '-')}\n"
            )
        return address
    except Exception as exc:
        with STATE.lock:
            STATE.last_output += f"\n[PAY] 本地地址生成异常: country={code}, error={exc}\n"
        return {}


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
        },
    )


def _watch_register_queue(queue: Queue, process: Process) -> None:
    while True:
        item = queue.get()
        kind = item[0]
        if kind == "log":
            with STATE.lock:
                STATE.last_output += item[1]
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
            if process.is_alive():
                process.join(timeout=0.2)
            break


def _register_worker(queue: Queue, *, proxy: Optional[str], total_accounts: int, max_workers: int,
                     cpa_cleanup: bool, cpa_upload_every_n: int, run_preflight: bool) -> None:
    writer = _QueueLogWriter(queue)
    success = False
    error_text = ""
    try:
        with redirect_stdout(writer), redirect_stderr(writer):
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
        queue.put(("done", success, error_text))


def _run_pay_job(*, pay_email: str, pay_form: dict[str, str]) -> None:
    success = False
    error_text = ""
    writer = _QueueLogWriter(_ImmediateQueue())
    try:
        with redirect_stdout(writer), redirect_stderr(writer):
            cfg = payment_bind_app.load_config()
            cfg.update(
                {
                    "proxy": pay_form.get("proxy", "").strip(),
                    "payment_country": pay_form.get("payment_country", "US").strip(),
                    "payment_card_number": pay_form.get("payment_card_number", "").strip(),
                    "payment_card_exp_month": pay_form.get("payment_card_exp_month", "").strip(),
                    "payment_card_exp_year": pay_form.get("payment_card_exp_year", "").strip(),
                    "payment_card_cvc": pay_form.get("payment_card_cvc", "").strip(),
                    "payment_billing_name": pay_form.get("payment_billing_name", "").strip(),
                    "payment_billing_email": pay_form.get("payment_billing_email", pay_email).strip(),
                    "payment_billing_line1": pay_form.get("payment_billing_line1", "").strip(),
                    "payment_billing_city": pay_form.get("payment_billing_city", "").strip(),
                    "payment_billing_state": pay_form.get("payment_billing_state", "").strip(),
                    "payment_billing_postal_code": pay_form.get("payment_billing_postal_code", "").strip(),
                    "payment_billing_country": pay_form.get("payment_country", "US").strip(),
                }
            )
            account = payment_bind_app.load_account(pay_email)
            binder = payment_bind_app.PaymentBinder(cfg, account)
            result = binder.run()
            print(json.dumps(result, ensure_ascii=False, indent=2))
        success = True
    except Exception as exc:
        error_text = f"{exc}\n{traceback.format_exc()}"
    finally:
        with STATE.lock:
            STATE.running = False
            STATE.current_task = ""
            STATE.last_error = error_text
            STATE.last_success = success


class _ImmediateQueue:
    def put(self, item: tuple[str, str]) -> None:
        if item[0] == "log":
            with STATE.lock:
                STATE.last_output += item[1]


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
            }
        )


@app.post("/register/save", response_class=HTMLResponse)
def save_register_config(
    proxy: str = Form(""),
    total_accounts: int = Form(1),
    max_workers: int = Form(1),
    cpa_cleanup: str = Form("false"),
    cpa_upload_every_n: int = Form(1),
    run_preflight: str = Form("true"),
    mail_provider: str = Form("duckmail"),
    duckmail_api_base: str = Form(""),
    duckmail_bearer: str = Form(""),
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
    STATE.last_form.update(
        {
            "proxy": proxy,
            "total_accounts": str(total_accounts),
            "max_workers": str(max_workers),
            "cpa_cleanup": cpa_cleanup,
            "cpa_upload_every_n": str(cpa_upload_every_n),
            "run_preflight": run_preflight,
            "mail_provider": mail_provider,
            "duckmail_api_base": duckmail_api_base,
            "duckmail_bearer": duckmail_bearer,
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
    _save_current_config({**STATE.last_form, **STATE.last_pay_form})
    _reload_runtime_config()
    return RedirectResponse(url="/?tab=register", status_code=303)


@app.post("/register/start", response_class=HTMLResponse)
def start_register(
    proxy: str = Form(""),
    total_accounts: int = Form(1),
    max_workers: int = Form(1),
    cpa_cleanup: str = Form("false"),
    cpa_upload_every_n: int = Form(1),
    run_preflight: str = Form("true"),
    mail_provider: str = Form("duckmail"),
    duckmail_api_base: str = Form(""),
    duckmail_bearer: str = Form(""),
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
        "duckmail_api_base": duckmail_api_base,
        "duckmail_bearer": duckmail_bearer,
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
            return RedirectResponse(url="/?tab=register", status_code=303)
        STATE.running = True
        STATE.current_task = "register"
        STATE.last_output = ""
        STATE.last_error = ""
        STATE.last_success = False
        STATE.last_form.update(form_data)

    _save_current_config({**STATE.last_form, **STATE.last_pay_form})
    _reload_runtime_config()

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
        },
        daemon=True,
    )
    process.start()

    with STATE.lock:
        STATE.register_process = process
        STATE.register_queue = queue
        STATE.register_log_thread = threading.Thread(target=_watch_register_queue, args=(queue, process), daemon=True)
        STATE.register_log_thread.start()

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
            STATE.last_output += "\n[WEB] 已强制终止注册任务\n"
            STATE.register_process = None
            STATE.register_queue = None
            STATE.register_log_thread = None
    return RedirectResponse(url="/?tab=register", status_code=303)


@app.post("/pay/save", response_class=HTMLResponse)
def save_pay_config(
    pay_email: str = Form(""),
    proxy: str = Form(""),
    payment_country: str = Form("US"),
    payment_card_number: str = Form(""),
    payment_card_exp_month: str = Form(""),
    payment_card_exp_year: str = Form(""),
    payment_card_cvc: str = Form(""),
    address_mode: str = Form("auto"),
    payment_billing_name: str = Form(""),
    payment_billing_line1: str = Form(""),
    payment_billing_city: str = Form(""),
    payment_billing_state: str = Form(""),
    payment_billing_postal_code: str = Form(""),
):
    pay_form = {
        "pay_email": pay_email,
        "proxy": proxy,
        "payment_country": payment_country,
        "payment_card_number": payment_card_number,
        "payment_card_exp_month": payment_card_exp_month,
        "payment_card_exp_year": payment_card_exp_year,
        "payment_card_cvc": payment_card_cvc,
        "address_mode": address_mode,
        "payment_billing_name": payment_billing_name,
        "payment_billing_line1": payment_billing_line1,
        "payment_billing_city": payment_billing_city,
        "payment_billing_state": payment_billing_state,
        "payment_billing_postal_code": payment_billing_postal_code,
    }
    if not pay_form["pay_email"] and _list_account_options():
        pay_form["pay_email"] = _list_account_options()[0]["email"]
    account = ACCOUNT_STORE.get_account(pay_form["pay_email"]) or {}
    if account:
        pay_form["payment_billing_email"] = str(account.get("email") or pay_form["pay_email"])
    STATE.last_pay_form.update(pay_form)
    _save_current_config({**STATE.last_form, **STATE.last_pay_form})
    _reload_runtime_config()
    return RedirectResponse(url="/?tab=pay", status_code=303)


@app.post("/pay/start", response_class=HTMLResponse)
def start_pay(
    pay_email: str = Form(""),
    proxy: str = Form(""),
    payment_country: str = Form("US"),
    payment_card_number: str = Form(""),
    payment_card_exp_month: str = Form(""),
    payment_card_exp_year: str = Form(""),
    payment_card_cvc: str = Form(""),
    address_mode: str = Form("auto"),
    payment_billing_name: str = Form(""),
    payment_billing_line1: str = Form(""),
    payment_billing_city: str = Form(""),
    payment_billing_state: str = Form(""),
    payment_billing_postal_code: str = Form(""),
):
    pay_form = {
        "pay_email": pay_email,
        "proxy": proxy,
        "payment_country": payment_country,
        "payment_card_number": payment_card_number,
        "payment_card_exp_month": payment_card_exp_month,
        "payment_card_exp_year": payment_card_exp_year,
        "payment_card_cvc": payment_card_cvc,
        "address_mode": address_mode,
        "payment_billing_name": payment_billing_name,
        "payment_billing_line1": payment_billing_line1,
        "payment_billing_city": payment_billing_city,
        "payment_billing_state": payment_billing_state,
        "payment_billing_postal_code": payment_billing_postal_code,
    }
    account = ACCOUNT_STORE.get_account(pay_email) or {}
    if account:
        pay_form["payment_billing_email"] = str(account.get("email") or pay_email)

    with STATE.lock:
        if STATE.running:
            STATE.last_error = "当前已有任务在执行，请等待完成后再启动新任务"
            STATE.last_pay_form.update(pay_form)
            return RedirectResponse(url="/?tab=pay", status_code=303)
        STATE.running = True
        STATE.current_task = "pay"
        STATE.last_output = ""
        STATE.last_error = ""
        STATE.last_success = False
        STATE.last_pay_form.update(pay_form)

    _save_current_config({**STATE.last_form, **STATE.last_pay_form})
    _reload_runtime_config()

    thread = threading.Thread(target=_run_pay_job, kwargs={"pay_email": pay_email, "pay_form": dict(STATE.last_pay_form)}, daemon=True)
    thread.start()
    return RedirectResponse(url="/?tab=pay", status_code=303)


@app.get("/pay/fill-address")
def fill_pay_address(
    payment_country: str = "US",
):
    address = _fetch_random_address(payment_country)
    with STATE.lock:
        STATE.last_output += (
            f"\n[PAY] 自动地址获取: country={payment_country or '-'}"
            f", city={str(address.get('city') or '-')}, state={str(address.get('state') or '-') }"
            f", postal_code={str(address.get('postal_code') or '-')}\n"
        )
    return JSONResponse(address)
