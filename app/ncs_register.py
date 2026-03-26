"""
ChatGPT 批量自动注册工具 (并发版) - 支持 DuckMail / TempMail.lol / LaMail / CF 自建邮箱
依赖: pip install curl_cffi
"""

import os
import re
import uuid
import json
import random
import string
import time
import sys
import math
import shutil
import builtins
import threading
import traceback
import secrets
import hashlib
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, parse_qs, urlencode
from dataclasses import dataclass
from typing import Any, Dict, Optional

from curl_cffi import requests as curl_requests

from app.account_store import AccountStore

# ================= 加载配置 =================
def _load_config():
    config = {
        "total_accounts": 3,
        "mail_provider": "duckmail",
        "cfmail_config_path": "zhuce5_cfmail_accounts.json",
        "cfmail_profile": "auto",
        "duckmail_api_base": "https://api.duckmail.sbs",
        "duckmail_bearer": "",
        "tempmail_lol_api_base": "https://api.tempmail.lol/v2",
        "lamail_api_base": "https://maliapi.215.im/v1",
        "lamail_api_key": "",
        "lamail_domain": "",
        "proxy": "http://127.0.0.1:7890",
        "output_file": "registered_accounts.txt",
        "enable_oauth": True,
        "oauth_required": True,
        "oauth_issuer": "https://auth.openai.com",
        "oauth_client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
        "oauth_redirect_uri": "http://localhost:1455/auth/callback",
        "ak_file": "ak.txt",
        "rk_file": "rk.txt",
        "token_json_dir": "codex_tokens",
        "upload_api_url": "",
        "upload_api_token": "",
        "upload_api_proxy": "",
        "cpa_cleanup_enabled": True,
        "cpa_upload_every_n": 3,
        "register_delay_factor": 1.0,
        "otp_poll_interval_default": 2.0,
        "otp_poll_interval_duckmail": 2.0,
        "otp_poll_interval_tempmail_lol": 1.5,
        "otp_poll_interval_lamail": 1.0,
        "otp_poll_interval_cfmail": 1.5,
        "enable_payment_verification": False,
        "payment_verification_required": False,
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
    }

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_path = os.path.join(base_dir, "config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                file_config = json.load(f)
                config.update(file_config)
        except Exception as e:
            print(f"⚠️ 加载 config.json 失败: {e}")

    return config


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _as_csv_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = str(value).split(",")
    return [str(item).strip() for item in items if str(item).strip()]


_CONFIG = _load_config()
DUCKMAIL_API_BASE = _CONFIG["duckmail_api_base"]
DUCKMAIL_BEARER = _CONFIG["duckmail_bearer"]
TEMPMAIL_LOL_API_BASE = _CONFIG.get("tempmail_lol_api_base", "https://api.tempmail.lol/v2").rstrip("/")
LAMAIL_API_BASE = _CONFIG.get("lamail_api_base", "https://maliapi.215.im/v1").rstrip("/")
LAMAIL_API_KEY = str(_CONFIG.get("lamail_api_key", "") or "").strip()
LAMAIL_DOMAINS = _as_csv_list(_CONFIG.get("lamail_domain", ""))
LAMAIL_DOMAIN_TEXT = ", ".join(LAMAIL_DOMAINS)
DEFAULT_TOTAL_ACCOUNTS = _CONFIG["total_accounts"]
DEFAULT_PROXY = _CONFIG["proxy"]
DEFAULT_OUTPUT_FILE = _CONFIG["output_file"]
ENABLE_OAUTH = _as_bool(_CONFIG.get("enable_oauth", True))
OAUTH_REQUIRED = _as_bool(_CONFIG.get("oauth_required", True))
OAUTH_ISSUER = _CONFIG["oauth_issuer"].rstrip("/")
OAUTH_CLIENT_ID = _CONFIG["oauth_client_id"]
OAUTH_REDIRECT_URI = _CONFIG["oauth_redirect_uri"]
AK_FILE = _CONFIG["ak_file"]
RK_FILE = _CONFIG["rk_file"]
TOKEN_JSON_DIR = _CONFIG["token_json_dir"]
UPLOAD_API_URL = _CONFIG["upload_api_url"]
UPLOAD_API_TOKEN = _CONFIG["upload_api_token"]
UPLOAD_API_PROXY = str(_CONFIG.get("upload_api_proxy", "") or "").strip()
CPA_CLEANUP_ENABLED = _as_bool(_CONFIG.get("cpa_cleanup_enabled", True))
CPA_UPLOAD_EVERY_N = max(1, int(_CONFIG.get("cpa_upload_every_n", 3) or 3))
MAIL_PROVIDER = str(_CONFIG.get("mail_provider", "duckmail")).strip().lower()
REGISTER_DELAY_FACTOR = max(0.0, float(_CONFIG.get("register_delay_factor", 1.0) or 1.0))
OTP_POLL_INTERVAL_DEFAULT = max(0.2, float(_CONFIG.get("otp_poll_interval_default", 2.0) or 2.0))
OTP_POLL_INTERVAL_BY_PROVIDER = {
    "duckmail": max(0.2, float(_CONFIG.get("otp_poll_interval_duckmail", OTP_POLL_INTERVAL_DEFAULT) or OTP_POLL_INTERVAL_DEFAULT)),
    "tempmail_lol": max(0.2, float(_CONFIG.get("otp_poll_interval_tempmail_lol", 1.5) or 1.5)),
    "lamail": max(0.2, float(_CONFIG.get("otp_poll_interval_lamail", 1.0) or 1.0)),
    "cfmail": max(0.2, float(_CONFIG.get("otp_poll_interval_cfmail", 1.5) or 1.5)),
}

_CPA_UPLOAD_QUEUE: list[str] = []
_CPA_UPLOAD_QUEUE_LOCK = threading.Lock()


def _get_otp_poll_interval(provider: str) -> float:
    return OTP_POLL_INTERVAL_BY_PROVIDER.get(str(provider or "").strip().lower(), OTP_POLL_INTERVAL_DEFAULT)


def _normalize_management_api_root(raw_url: str) -> str:
    value = str(raw_url or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        return ""
    path = (parsed.path or "").rstrip("/")
    lowered_path = path.lower()
    if lowered_path.endswith("/management.html"):
        path = path[: -len("/management.html")]
        lowered_path = path.lower()
    if lowered_path.endswith("/v0/management"):
        base_path = path
    elif lowered_path.endswith("/auth-files") or lowered_path.endswith("/api-call"):
        base_path = path.rsplit("/", 1)[0]
    elif "/v0/management/" in lowered_path:
        prefix = path[: lowered_path.index("/v0/management/")]
        base_path = f"{prefix}/v0/management"
    elif lowered_path.endswith("/v0"):
        base_path = f"{path}/management"
    else:
        base_path = "/v0/management"
    return f"{parsed.scheme}://{parsed.netloc}{base_path}".rstrip("/")


def _management_auth_headers() -> Dict[str, str]:
    token = str(UPLOAD_API_TOKEN or "").strip()
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}

_ACCOUNT_STORE = AccountStore()

# 全局线程锁
_print_lock = threading.RLock()
_file_lock = threading.Lock()
_original_print = builtins.print

_progress_state = {
    "active": False,
    "done": 0,
    "total": 1,
    "success": 0,
    "fail": 0,
    "start_time": 0.0,
}

_STOP_REQUESTED = False


def _clear_progress_line_unlocked():
    cols = shutil.get_terminal_size((110, 20)).columns
    _original_print("\r" + " " * max(10, cols - 1) + "\r", end="", flush=True)


def _render_apt_like_progress(done: int, total: int, success: int, fail: int, start_time: float):
    """在终端底部输出类似 apt install 的单行进度条。"""
    total = max(1, int(total or 1))
    done = max(0, min(int(done or 0), total))
    success = max(0, int(success or 0))
    fail = max(0, int(fail or 0))

    with _print_lock:
        _progress_state.update({
            "active": done < total,
            "done": done,
            "total": total,
            "success": success,
            "fail": fail,
            "start_time": float(start_time or time.time()),
        })

        percent = (done / total) * 100
        cols = shutil.get_terminal_size((110, 20)).columns
        elapsed = max(0.0, time.time() - _progress_state["start_time"])
        speed = (done / elapsed) if elapsed > 0 else 0.0

        right_text = f" {percent:6.2f}% [{done}/{total}] 成功:{success} 失败:{fail} 速率:{speed:.2f}/s"
        bar_width = max(12, min(50, cols - len(right_text) - 8))
        filled = int((done / total) * bar_width)

        if done >= total:
            bar = "=" * bar_width
        elif filled <= 0:
            bar = ">" + " " * (bar_width - 1)
        else:
            bar = "=" * (filled - 1) + ">" + " " * (bar_width - filled)

        line = f"\r进度: [{bar}]" + right_text
        _original_print(line, end="", flush=True)


def _print_with_progress(*args, **kwargs):
    with _print_lock:
        if _progress_state["active"]:
            _clear_progress_line_unlocked()

        _original_print(*args, **kwargs)

        if _progress_state["active"]:
            _render_apt_like_progress(
                _progress_state["done"],
                _progress_state["total"],
                _progress_state["success"],
                _progress_state["fail"],
                _progress_state["start_time"],
            )


# 全局接管 print，保证日志输出后能恢复底部进度条
builtins.print = _print_with_progress


# ================= CF 自建邮箱 (cfmail) 支持 =================

@dataclass(frozen=True)
class CfmailAccount:
    name: str
    worker_domain: str
    email_domain: str
    admin_password: str


def _normalize_host(value: str) -> str:
    value = str(value or "").strip()
    if value.startswith("https://"):
        value = value[len("https://"):]
    elif value.startswith("http://"):
        value = value[len("http://"):]
    return value.strip().strip("/")


def _normalize_cfmail_account(raw: Dict[str, Any]) -> Optional[CfmailAccount]:
    if not isinstance(raw, dict):
        return None
    if not raw.get("enabled", True):
        return None
    name = str(raw.get("name") or "").strip()
    worker_domain = _normalize_host(raw.get("worker_domain") or raw.get("WORKER_DOMAIN") or "")
    email_domain = _normalize_host(raw.get("email_domain") or raw.get("EMAIL_DOMAIN") or "")
    admin_password = str(raw.get("admin_password") or raw.get("ADMIN_PASSWORD") or "").strip()
    if not name or not worker_domain or not email_domain or not admin_password:
        return None
    return CfmailAccount(name=name, worker_domain=worker_domain,
                         email_domain=email_domain, admin_password=admin_password)


def _load_cfmail_accounts_from_file(config_path: str, *, silent: bool = False) -> list:
    path = str(config_path or "").strip()
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        if not silent:
            print(f"[警告] 读取 cfmail 配置文件失败: {path}，错误: {e}")
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        accounts = data.get("accounts")
        if isinstance(accounts, list):
            return accounts
    if not silent:
        print(f"[警告] cfmail 配置文件格式无效: {path}")
    return []


def _build_cfmail_accounts(raw_accounts: list) -> list:
    accounts = []
    seen_names = set()
    for item in raw_accounts:
        account = _normalize_cfmail_account(item)
        if not account:
            continue
        key = account.name.lower()
        if key in seen_names:
            continue
        seen_names.add(key)
        accounts.append(account)

    return accounts


# cfmail 全局状态
_cfmail_account_lock = threading.Lock()
_cfmail_account_index = 0
_cfmail_reload_lock = threading.Lock()
_cfmail_failure_lock = threading.Lock()

_CFMAIL_CONFIG_PATH = str(_CONFIG.get("cfmail_config_path", "zhuce5_cfmail_accounts.json")).strip()
CFMAIL_PROFILE_MODE = str(_CONFIG.get("cfmail_profile", "auto")).strip() or "auto"
CFMAIL_ACCOUNTS: list = _build_cfmail_accounts(
    _load_cfmail_accounts_from_file(_CFMAIL_CONFIG_PATH, silent=True)
)
CFMAIL_HOT_RELOAD_ENABLED = True
CFMAIL_CONFIG_MTIME = (
    os.path.getmtime(_CFMAIL_CONFIG_PATH) if os.path.exists(_CFMAIL_CONFIG_PATH) else None
)
CFMAIL_FAIL_THRESHOLD = 3
CFMAIL_COOLDOWN_SECONDS = 1800
CFMAIL_FAILURE_STATE: Dict[str, Dict[str, Any]] = {}


def _cfmail_skip_remaining_seconds(account_name: str) -> int:
    key = str(account_name or "").strip().lower()
    if not key:
        return 0
    with _cfmail_failure_lock:
        state = CFMAIL_FAILURE_STATE.get(key) or {}
        cooldown_until = float(state.get("cooldown_until") or 0)
    remaining = int(math.ceil(cooldown_until - time.time()))
    return max(0, remaining)


def _record_cfmail_success(account_name: str) -> None:
    key = str(account_name or "").strip().lower()
    if not key:
        return
    with _cfmail_failure_lock:
        state = CFMAIL_FAILURE_STATE.setdefault(key, {"name": account_name})
        state["consecutive_failures"] = 0
        state["cooldown_until"] = 0
        state["last_error"] = ""
        state["last_success_at"] = time.time()


def _record_cfmail_failure(account_name: str, reason: str = "") -> None:
    key = str(account_name or "").strip().lower()
    if not key:
        return
    now = time.time()
    with _cfmail_failure_lock:
        state = CFMAIL_FAILURE_STATE.setdefault(key, {"name": account_name})
        state["consecutive_failures"] = int(state.get("consecutive_failures") or 0) + 1
        state["last_error"] = str(reason or "")[:300]
        state["last_failed_at"] = now
        if state["consecutive_failures"] >= CFMAIL_FAIL_THRESHOLD:
            state["cooldown_until"] = max(
                float(state.get("cooldown_until") or 0),
                now + CFMAIL_COOLDOWN_SECONDS,
            )
            state["consecutive_failures"] = 0
            remaining = int(math.ceil(state["cooldown_until"] - now))
            print(f"[警告] cfmail 配置 {account_name} 连续失败，已跳过 {remaining} 秒")


def _reload_cfmail_accounts_if_needed(force: bool = False) -> bool:
    global CFMAIL_CONFIG_MTIME
    if not CFMAIL_HOT_RELOAD_ENABLED:
        return False
    config_path = _CFMAIL_CONFIG_PATH
    if not config_path:
        return False
    try:
        mtime = os.path.getmtime(config_path)
    except OSError:
        return False
    with _cfmail_reload_lock:
        if not force and CFMAIL_CONFIG_MTIME == mtime:
            return False
        raw_accounts = _load_cfmail_accounts_from_file(config_path)
        new_accounts = _build_cfmail_accounts(raw_accounts)
        if not new_accounts:
            CFMAIL_CONFIG_MTIME = mtime
            return False
        global CFMAIL_ACCOUNTS, _cfmail_account_index
        CFMAIL_ACCOUNTS = new_accounts
        _cfmail_account_index = 0
        CFMAIL_CONFIG_MTIME = mtime
        return True


def _select_cfmail_account(profile_name: str = "auto") -> Optional[CfmailAccount]:
    global _cfmail_account_index
    accounts = CFMAIL_ACCOUNTS
    if not accounts:
        return None

    selected_name = str(profile_name or "auto").strip()
    if selected_name and selected_name.lower() != "auto":
        for account in accounts:
            if account.name.lower() == selected_name.lower():
                return account
        return None

    with _cfmail_account_lock:
        start_index = _cfmail_account_index % len(accounts)
        for offset in range(len(accounts)):
            index = (start_index + offset) % len(accounts)
            account = accounts[index]
            if _cfmail_skip_remaining_seconds(account.name) > 0:
                continue
            _cfmail_account_index = (index + 1) % len(accounts)
            return account
    return None


def _cfmail_headers(*, jwt: str = "", use_json: bool = False) -> Dict[str, str]:
    headers = {"Accept": "application/json"}
    if use_json:
        headers["Content-Type"] = "application/json"
    if jwt:
        headers["Authorization"] = f"Bearer {jwt}"
    return headers


def _lamail_headers(*, bearer: str = "", use_json: bool = False, api_key: str = "") -> Dict[str, str]:
    headers = {"Accept": "application/json"}
    if use_json:
        headers["Content-Type"] = "application/json"
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    if api_key:
        headers["X-API-Key"] = api_key
    return headers


def _lamail_unwrap_json(resp, *, action: str = "请求") -> Any:
    try:
        payload = resp.json() if resp.content else {}
    except Exception:
        body = (resp.text or "")[:220].replace("\n", " ")
        raise Exception(f"LaMail {action}返回非 JSON: HTTP {resp.status_code}, body={body}")

    if isinstance(payload, dict) and "success" in payload:
        if not payload.get("success", False):
            raise Exception(
                f"LaMail {action}失败: HTTP {resp.status_code}, error={payload.get('error') or 'unknown'}"
            )
        return payload.get("data")
    return payload


# ================= Chrome 指纹配置 =================

_CHROME_PROFILES = [
    {
        "major": 131, "impersonate": "chrome131",
        "build": 6778, "patch_range": (69, 205),
        "sec_ch_ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    },
    {
        "major": 133, "impersonate": "chrome133a",
        "build": 6943, "patch_range": (33, 153),
        "sec_ch_ua": '"Not(A:Brand";v="99", "Google Chrome";v="133", "Chromium";v="133"',
    },
    {
        "major": 136, "impersonate": "chrome136",
        "build": 7103, "patch_range": (48, 175),
        "sec_ch_ua": '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
    },
    {
        "major": 142, "impersonate": "chrome142",
        "build": 7540, "patch_range": (30, 150),
        "sec_ch_ua": '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"',
    },
]


def _random_chrome_version():
    profile = random.choice(_CHROME_PROFILES)
    major = profile["major"]
    build = profile["build"]
    patch = random.randint(*profile["patch_range"])
    full_ver = f"{major}.0.{build}.{patch}"
    ua = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{full_ver} Safari/537.36"
    return profile["impersonate"], major, full_ver, ua, profile["sec_ch_ua"]


def _random_delay(low=0.3, high=1.0):
    factor = max(0.0, float(REGISTER_DELAY_FACTOR or 0.0))
    scaled_low = max(0.0, float(low) * factor)
    scaled_high = max(scaled_low, float(high) * factor)
    if scaled_high <= 0:
        return
    time.sleep(random.uniform(scaled_low, scaled_high))


def _make_trace_headers():
    trace_id = random.randint(10**17, 10**18 - 1)
    parent_id = random.randint(10**17, 10**18 - 1)
    tp = f"00-{uuid.uuid4().hex}-{format(parent_id, '016x')}-01"
    return {
        "traceparent": tp, "tracestate": "dd=s:1;o:rum",
        "x-datadog-origin": "rum", "x-datadog-sampling-priority": "1",
        "x-datadog-trace-id": str(trace_id), "x-datadog-parent-id": str(parent_id),
    }


def _generate_pkce():
    code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


# ================= Sentinel Token =================

class SentinelTokenGenerator:
    MAX_ATTEMPTS = 500000
    ERROR_PREFIX = "wQ8Lk5FbGpA2NcR9dShT6gYjU7VxZ4D"

    def __init__(self, device_id=None, user_agent=None):
        self.device_id = device_id or str(uuid.uuid4())
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/145.0.0.0 Safari/537.36"
        )
        self.requirements_seed = str(random.random())
        self.sid = str(uuid.uuid4())

    @staticmethod
    def _fnv1a_32(text: str):
        h = 2166136261
        for ch in text:
            h ^= ord(ch)
            h = (h * 16777619) & 0xFFFFFFFF
        h ^= (h >> 16)
        h = (h * 2246822507) & 0xFFFFFFFF
        h ^= (h >> 13)
        h = (h * 3266489909) & 0xFFFFFFFF
        h ^= (h >> 16)
        h &= 0xFFFFFFFF
        return format(h, "08x")

    def _get_config(self):
        now_str = time.strftime(
            "%a %b %d %Y %H:%M:%S GMT+0000 (Coordinated Universal Time)",
            time.gmtime(),
        )
        perf_now = random.uniform(1000, 50000)
        time_origin = time.time() * 1000 - perf_now
        nav_prop = random.choice([
            "vendorSub", "productSub", "vendor", "maxTouchPoints",
            "scheduling", "userActivation", "doNotTrack", "geolocation",
            "connection", "plugins", "mimeTypes", "pdfViewerEnabled",
            "webkitTemporaryStorage", "webkitPersistentStorage",
            "hardwareConcurrency", "cookieEnabled", "credentials",
            "mediaDevices", "permissions", "locks", "ink",
        ])
        nav_val = f"{nav_prop}-undefined"
        return [
            "1920x1080", now_str, 4294705152, random.random(),
            self.user_agent,
            "https://sentinel.openai.com/sentinel/20260124ceb8/sdk.js",
            None, None, "en-US", "en-US,en", random.random(), nav_val,
            random.choice(["location", "implementation", "URL", "documentURI", "compatMode"]),
            random.choice(["Object", "Function", "Array", "Number", "parseFloat", "undefined"]),
            perf_now, self.sid, "", random.choice([4, 8, 12, 16]), time_origin,
        ]

    @staticmethod
    def _base64_encode(data):
        raw = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return base64.b64encode(raw).decode("ascii")

    def _run_check(self, start_time, seed, difficulty, config, nonce):
        config[3] = nonce
        config[9] = round((time.time() - start_time) * 1000)
        data = self._base64_encode(config)
        hash_hex = self._fnv1a_32(seed + data)
        diff_len = len(difficulty)
        if hash_hex[:diff_len] <= difficulty:
            return data + "~S"
        return None

    def generate_token(self, seed=None, difficulty=None):
        seed = seed if seed is not None else self.requirements_seed
        difficulty = str(difficulty or "0")
        start_time = time.time()
        config = self._get_config()
        for i in range(self.MAX_ATTEMPTS):
            result = self._run_check(start_time, seed, difficulty, config, i)
            if result:
                return "gAAAAAB" + result
        return "gAAAAAB" + self.ERROR_PREFIX + self._base64_encode(str(None))

    def generate_requirements_token(self):
        config = self._get_config()
        config[3] = 1
        config[9] = round(random.uniform(5, 50))
        data = self._base64_encode(config)
        return "gAAAAAC" + data


def fetch_sentinel_challenge(session, device_id, flow="authorize_continue", user_agent=None,
                             sec_ch_ua=None, impersonate=None):
    generator = SentinelTokenGenerator(device_id=device_id, user_agent=user_agent)
    req_body = {"p": generator.generate_requirements_token(), "id": device_id, "flow": flow}
    headers = {
        "Content-Type": "text/plain;charset=UTF-8",
        "Referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html",
        "Origin": "https://sentinel.openai.com",
        "User-Agent": user_agent or "Mozilla/5.0",
        "sec-ch-ua": sec_ch_ua or '"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }
    kwargs = {"data": json.dumps(req_body), "headers": headers, "timeout": 20}
    if impersonate:
        kwargs["impersonate"] = impersonate
    try:
        resp = session.post("https://sentinel.openai.com/backend-api/sentinel/req", **kwargs)
    except Exception:
        return None
    if resp.status_code != 200:
        return None
    try:
        return resp.json()
    except Exception:
        return None


def build_sentinel_token(session, device_id, flow="authorize_continue", user_agent=None,
                         sec_ch_ua=None, impersonate=None):
    challenge = fetch_sentinel_challenge(session, device_id, flow=flow,
                                         user_agent=user_agent, sec_ch_ua=sec_ch_ua,
                                         impersonate=impersonate)
    if not challenge:
        return None
    c_value = challenge.get("token", "")
    if not c_value:
        return None
    pow_data = challenge.get("proofofwork") or {}
    generator = SentinelTokenGenerator(device_id=device_id, user_agent=user_agent)
    if pow_data.get("required") and pow_data.get("seed"):
        p_value = generator.generate_token(seed=pow_data.get("seed"),
                                            difficulty=pow_data.get("difficulty", "0"))
    else:
        p_value = generator.generate_requirements_token()
    return json.dumps({"p": p_value, "t": "", "c": c_value, "id": device_id, "flow": flow},
                      separators=(",", ":"))


def _extract_code_from_url(url: str):
    if not url or "code=" not in url:
        return None
    try:
        return parse_qs(urlparse(url).query).get("code", [None])[0]
    except Exception:
        return None


def _decode_jwt_payload(token: str):
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        payload = parts[1]
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += "=" * padding
        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)
    except Exception:
        return {}


def _mask_card_number(number: str) -> str:
    digits = re.sub(r"\D+", "", str(number or ""))
    if len(digits) < 8:
        return digits
    return f"{digits[:6]}{'*' * max(0, len(digits) - 10)}{digits[-4:]}"


def _cookie_value(session, name: str) -> str:
    try:
        for cookie in session.cookies.jar:
            if getattr(cookie, "name", "") == name:
                return str(getattr(cookie, "value", "") or "")
    except Exception:
        pass
    try:
        value = session.cookies.get(name)
        return str(value or "")
    except Exception:
        return ""


def _flatten_form_data(data: dict, prefix: str = "") -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    if isinstance(data, dict):
        for key, value in data.items():
            new_prefix = f"{prefix}[{key}]" if prefix else str(key)
            pairs.extend(_flatten_form_data(value, new_prefix))
        return pairs
    if isinstance(data, (list, tuple)):
        for item in data:
            pairs.extend(_flatten_form_data(item, f"{prefix}[]"))
        return pairs
    pairs.append((prefix, "" if data is None else str(data)))
    return pairs


def _save_codex_tokens(email: str, tokens: dict, register=None):
    access_token = tokens.get("access_token", "")
    refresh_token = tokens.get("refresh_token", "")
    id_token = tokens.get("id_token", "")

    if access_token:
        with _file_lock:
            with open(AK_FILE, "a", encoding="utf-8") as f:
                f.write(f"{access_token}\n")

    if refresh_token:
        with _file_lock:
            with open(RK_FILE, "a", encoding="utf-8") as f:
                f.write(f"{refresh_token}\n")

    if not access_token:
        return

    payload = _decode_jwt_payload(access_token)
    auth_info = payload.get("https://api.openai.com/auth", {})
    account_id = auth_info.get("chatgpt_account_id", "")
    exp_timestamp = payload.get("exp")
    expired_str = ""
    if isinstance(exp_timestamp, int) and exp_timestamp > 0:
        from datetime import datetime, timezone, timedelta
        exp_dt = datetime.fromtimestamp(exp_timestamp, tz=timezone(timedelta(hours=8)))
        expired_str = exp_dt.strftime("%Y-%m-%dT%H:%M:%S+08:00")

    session_token = str(tokens.get("session_token") or "").strip()
    csrf_token = str(tokens.get("csrf_token") or "").strip()
    cookies = tokens.get("cookies") if isinstance(tokens.get("cookies"), dict) else {}
    if not isinstance(cookies, dict):
        cookies = {}
    user_agent = str(tokens.get("user_agent") or "").strip()
    sec_ch_ua = str(tokens.get("sec_ch_ua") or "").strip()
    device_id = str(tokens.get("device_id") or "").strip()
    if register is not None:
        session_token = session_token or _cookie_value(register.session, "__Secure-next-auth.session-token")
        csrf_token = csrf_token or _cookie_value(register.session, "__Host-next-auth.csrf-token")
        user_agent = user_agent or str(getattr(register, "ua", "") or "")
        sec_ch_ua = sec_ch_ua or str(getattr(register, "sec_ch_ua", "") or "")
        device_id = device_id or str(getattr(register, "device_id", "") or "")
        try:
            for cookie in register.session.cookies.jar:
                name = str(getattr(cookie, "name", "") or "")
                value = str(getattr(cookie, "value", "") or "")
                if name and value and name not in cookies:
                    cookies[name] = value
        except Exception:
            pass

    from datetime import datetime, timezone, timedelta
    now = datetime.now(tz=timezone(timedelta(hours=8)))
    token_data = {
        "type": "codex", "email": email, "expired": expired_str,
        "id_token": id_token, "account_id": account_id,
        "access_token": access_token,
        "last_refresh": now.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "refresh_token": refresh_token,
        "session_token": session_token,
        "csrf_token": csrf_token,
        "device_id": device_id,
        "user_agent": user_agent,
        "sec_ch_ua": sec_ch_ua,
        "cookies": cookies,
    }

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    token_dir = TOKEN_JSON_DIR if os.path.isabs(TOKEN_JSON_DIR) else os.path.join(base_dir, TOKEN_JSON_DIR)
    os.makedirs(token_dir, exist_ok=True)
    token_path = os.path.join(token_dir, f"{email}.json")
    with _file_lock:
        with open(token_path, "w", encoding="utf-8") as f:
            json.dump(token_data, f, ensure_ascii=False)

    try:
        _ACCOUNT_STORE.upsert_account(token_data)
    except Exception as e:
        print(f"[SQLite] 写入账号库失败: {e}")


def _upload_token_json(filepath):
    def _resolve_upload_proxy_candidates() -> list:
        raw = (UPLOAD_API_PROXY or "").strip()
        if raw:
            lowered = raw.lower()
            if lowered in {"direct", "none", "off", "false", "0"}:
                return [None]
            if lowered == "default":
                return [DEFAULT_PROXY or None, None]
            return [raw]
        if DEFAULT_PROXY:
            return [DEFAULT_PROXY, None]
        return [None]

    base_api = _normalize_management_api_root(UPLOAD_API_URL)
    if not base_api:
        print(f"  [CPA] ❌ upload_api_url 无法识别为管理接口地址: {UPLOAD_API_URL}")
        return False

    upload_endpoint = f"{base_api}/auth-files"
    filename = os.path.basename(filepath)
    proxy_candidates = []
    for item in _resolve_upload_proxy_candidates():
        if item not in proxy_candidates:
            proxy_candidates.append(item)

    last_exception = None

    for idx, proxy in enumerate(proxy_candidates):
        mp = None
        session = None
        try:
            from curl_cffi import CurlMime
            mp = CurlMime()
            mp.addpart(name="file", content_type="application/json",
                       filename=filename, local_path=filepath)
            session = curl_requests.Session()
            if proxy:
                session.proxies = {"http": proxy, "https": proxy}

            resp = session.post(
                upload_endpoint,
                multipart=mp,
                headers=_management_auth_headers(),
                verify=False,
                timeout=30,
            )
            if 200 <= resp.status_code < 300:
                mode = f" via proxy {proxy}" if proxy else " direct"
                print(f"  [CPA] ✅ {filename} 已上传到管理接口 {upload_endpoint}{mode}")
                return True

            detail = (resp.text or "")[:300]
            print(f"  [CPA] ❌ {filename} 上传失败: status={resp.status_code}, endpoint={upload_endpoint}, body={detail}")
            return False
        except Exception as e:
            last_exception = e
            if proxy and idx < len(proxy_candidates) - 1:
                print(f"  [CPA] ⚠️ {filename} 通过代理上传异常，改为直连重试: {e}")
                continue
            print(f"  [CPA] ❌ {filename} 上传异常: endpoint={upload_endpoint}, error={e}")
            return False
        finally:
            if mp:
                mp.close()
            if session:
                try:
                    session.close()
                except Exception:
                    pass

    if last_exception:
        print(f"  [CPA] ❌ {filename} 上传异常: {last_exception}")
    return False


def _flush_cpa_upload_queue(force: bool = False):
    if not UPLOAD_API_URL:
        return 0, 0
    with _CPA_UPLOAD_QUEUE_LOCK:
        if not _CPA_UPLOAD_QUEUE:
            return 0, 0
        filepaths = list(dict.fromkeys(_CPA_UPLOAD_QUEUE))
        _CPA_UPLOAD_QUEUE.clear()
    print(f"\n{'='*60}\n  [CPA] 开始异步上传 {len(filepaths)} 个账号到 CPA 管理平台\n{'='*60}")
    uploaded = 0
    failed = 0
    for filepath in filepaths:
        if not os.path.isfile(filepath):
            continue
        if _upload_token_json(filepath):
            try:
                os.remove(filepath)
            except Exception:
                pass
            uploaded += 1
        else:
            failed += 1
            if not force:
                with _CPA_UPLOAD_QUEUE_LOCK:
                    if filepath not in _CPA_UPLOAD_QUEUE:
                        _CPA_UPLOAD_QUEUE.append(filepath)
    print(f"\n  [CPA] 上传完成: 成功 {uploaded} 个, 失败 {failed} 个\n{'='*60}")
    return uploaded, failed


def _enqueue_tokens_for_cpa(limit: int | None = None) -> int:
    if not UPLOAD_API_URL:
        return 0
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    token_dir = TOKEN_JSON_DIR if os.path.isabs(TOKEN_JSON_DIR) else os.path.join(base_dir, TOKEN_JSON_DIR)
    if not os.path.isdir(token_dir):
        return 0
    json_files = [f for f in os.listdir(token_dir) if f.endswith(".json")]
    if not json_files:
        return 0
    json_files.sort()
    if limit is not None:
        json_files = json_files[:max(0, int(limit))]
    added = 0
    with _CPA_UPLOAD_QUEUE_LOCK:
        existing = set(_CPA_UPLOAD_QUEUE)
        for filename in json_files:
            filepath = os.path.join(token_dir, filename)
            if filepath in existing:
                continue
            _CPA_UPLOAD_QUEUE.append(filepath)
            existing.add(filepath)
            added += 1
    return added


def _upload_all_tokens_to_cpa(force: bool = False):
    if not UPLOAD_API_URL:
        print("\n[CPA] ⚠️ 未配置 upload_api_url，跳过 CPA 上传")
        return
    added = _enqueue_tokens_for_cpa()
    if added > 0:
        print(f"[CPA] 已加入上传队列: {added} 个文件")
    _flush_cpa_upload_queue(force=force)


# ================= CPA Codex 清理引擎 =================

from datetime import datetime as _cpa_datetime
from urllib.parse import urlparse as _cpa_urlparse, urlunparse as _cpa_urlunparse

_CPA_STATUS_KEYWORDS = {"token_invalidated", "token_revoked", "usage_limit_reached"}
_CPA_MESSAGE_KEYWORDS = [
    "额度获取失败：401", '"status":401', '"status": 401',
    "your authentication token has been invalidated.",
    "encountered invalidated oauth token for user",
    "token_invalidated", "token_revoked", "usage_limit_reached",
]
_CPA_PROBE_TARGET_URL = "https://chatgpt.com/backend-api/codex/responses/compact"
_CPA_PROBE_MODEL = "gpt-5.1-codex"


def _cpa_normalize_api_root(raw_url):
    value = (raw_url or "").strip()
    if not value:
        return ""
    parsed = _cpa_urlparse(value)
    path = parsed.path or ""
    if path.endswith("/management.html"):
        path = path[:-len("/management.html")] + "/v0/management"
    for suffix in ("/api-call", "/auth-files"):
        if path.endswith(suffix):
            path = path[:-len(suffix)]
    normalized = _cpa_urlunparse((parsed.scheme, parsed.netloc, path.rstrip("/"), "", "", ""))
    return normalized.rstrip("/")


class _CpaCleanupConfig:
    def __init__(self, management_url, management_token, management_timeout=15,
                 active_probe=True, probe_timeout=8, probe_workers=12,
                 delete_workers=8, max_active_probes=120):
        self.management_url = management_url
        self.management_token = management_token
        self.management_timeout = management_timeout
        self.active_probe = active_probe
        self.probe_timeout = probe_timeout
        self.probe_workers = probe_workers
        self.delete_workers = delete_workers
        self.max_active_probes = max_active_probes

    @classmethod
    def from_mapping(cls, data):
        def to_int(name, default, minimum):
            try:
                parsed = int(data.get(name, default))
            except Exception:
                parsed = default
            return max(minimum, parsed)

        def to_bool(name, default):
            raw = data.get(name, default)
            if isinstance(raw, bool):
                return raw
            text = str(raw).strip().lower()
            if text in {"1", "true", "yes", "on"}:
                return True
            if text in {"0", "false", "no", "off"}:
                return False
            return default

        return cls(
            management_url=_cpa_normalize_api_root(str(data.get("management_url", "") or "")),
            management_token=str(data.get("management_token", "") or "").strip(),
            management_timeout=to_int("management_timeout", 15, 1),
            active_probe=to_bool("active_probe", True),
            probe_timeout=to_int("probe_timeout", 8, 1),
            probe_workers=to_int("probe_workers", 12, 1),
            delete_workers=to_int("delete_workers", 8, 1),
            max_active_probes=to_int("max_active_probes", 120, 0),
        )

    def validate(self):
        if not self.management_url:
            return False, "management_url 不能为空"
        if not self.management_token:
            return False, "management_token 不能为空"
        if not self.management_url.startswith(("http://", "https://")):
            return False, "management_url 必须以 http:// 或 https:// 开头"
        return True, ""


class _CpaManagementGateway:
    def __init__(self, config):
        self.config = config

    @property
    def _headers(self):
        return _management_auth_headers()

    @property
    def auth_files_endpoint(self):
        return self.config.management_url.rstrip("/") + "/auth-files"

    @property
    def api_call_endpoint(self):
        return self.config.management_url.rstrip("/") + "/api-call"

    def list_auth_files(self):
        resp = curl_requests.get(self.auth_files_endpoint, headers=self._headers,
                                 timeout=self.config.management_timeout)
        if resp.status_code == 404:
            raise RuntimeError(f"auth-files 接口不存在: {self.auth_files_endpoint}")
        resp.raise_for_status()
        payload = resp.json()
        files = payload.get("files", []) if isinstance(payload, dict) else []
        return files if isinstance(files, list) else []

    def delete_auth_file(self, name):
        resp = curl_requests.delete(
            self.auth_files_endpoint, params={"name": name},
            headers=self._headers, timeout=self.config.management_timeout,
        )
        if 200 <= resp.status_code < 300:
            return True, ""
        detail = ""
        try:
            detail = json.dumps(resp.json(), ensure_ascii=False)
        except Exception:
            detail = resp.text
        return False, f"HTTP {resp.status_code}: {detail}"

    def probe_auth_index(self, auth_index):
        payload = {
            "auth_index": auth_index, "method": "POST",
            "url": _CPA_PROBE_TARGET_URL,
            "header": {
                "Authorization": "Bearer $TOKEN$",
                "Content-Type": "application/json",
                "User-Agent": "codex_cli_rs/0.101.0",
            },
            "data": json.dumps(
                {"model": _CPA_PROBE_MODEL, "input": [{"role": "user", "content": "ping"}]},
                ensure_ascii=False,
            ),
        }
        resp = curl_requests.post(self.api_call_endpoint, headers=self._headers,
                                  json=payload, timeout=self.config.probe_timeout)
        resp.raise_for_status()
        body = resp.json()
        if not isinstance(body, dict):
            return 0, ""
        return int(body.get("status_code", 0) or 0), str(body.get("body", "") or "")


def _cpa_safe_status_message(file_obj):
    return str(file_obj.get("status_message", "") or "")


def _cpa_reason_from_status(file_obj):
    status_message = _cpa_safe_status_message(file_obj)
    if not status_message:
        return ""
    lower_msg = status_message.lower()
    for keyword in _CPA_MESSAGE_KEYWORDS:
        if keyword in lower_msg:
            return keyword
    try:
        parsed = json.loads(status_message)
    except Exception:
        parsed = None
    if isinstance(parsed, dict):
        if int(parsed.get("status", 0) or 0) == 401:
            return "status_401"
        err = parsed.get("error", {})
        if isinstance(err, dict):
            code = str(err.get("code", "") or "")
            if code in _CPA_STATUS_KEYWORDS:
                return code
    return ""


def _cpa_looks_401(file_obj):
    try:
        if int(file_obj.get("status", 0) or 0) == 401:
            return True
    except Exception:
        pass
    text = _cpa_safe_status_message(file_obj).lower()
    return "401" in text or "unauthorized" in text


class _CpaCleanupOrchestrator:
    def __init__(self, config, log=None):
        self.config = config
        self.gateway = _CpaManagementGateway(config)
        self.log = log or (lambda msg: None)

    def _log(self, message):
        self.log(message)

    def _probe_one(self, file_obj):
        name = str(file_obj.get("name", "") or "")
        auth_index = str(file_obj.get("auth_index", "") or "").strip()
        if not auth_index:
            return name, ""
        try:
            status_code, body = self.gateway.probe_auth_index(auth_index)
        except Exception as exc:
            return name, f"probe_error:{exc}"
        body_lower = body.lower()
        if status_code == 401:
            return name, "probe_status_401"
        if "401" in body_lower or "unauthorized" in body_lower:
            return name, "probe_body_401"
        for keyword in _CPA_MESSAGE_KEYWORDS:
            if keyword in body_lower:
                return name, f"probe_{keyword}"
        return name, ""

    def _delete_batch(self, hits):
        deleted = 0
        failures = []
        total = len(hits)

        def task(item):
            ok, err = self.gateway.delete_auth_file(item["name"])
            return item["name"], ok, err

        with ThreadPoolExecutor(max_workers=self.config.delete_workers) as pool:
            future_map = {pool.submit(task, item): item for item in hits}
            done = 0
            for future in as_completed(future_map):
                done += 1
                name, ok, err = future.result()
                if ok:
                    deleted += 1
                    self._log(f"[CPA清理] 删除成功: {name} ({done}/{total})")
                else:
                    failures.append({"name": name, "error": err})
                    self._log(f"[CPA清理] 删除失败: {name} -> {err}")
        return deleted, failures

    def _cleanup_401_only(self, exclude_names):
        try:
            files = self.gateway.list_auth_files()
        except Exception as exc:
            self._log(f"[CPA清理] 401补删列表读取失败: {exc}")
            return 0, [{"name": "<list>", "error": str(exc)}]
        targets = []
        for file_obj in files:
            name = str(file_obj.get("name", "") or "")
            if not name or name in exclude_names:
                continue
            if _cpa_looks_401(file_obj):
                targets.append({"name": name, "keyword": "status_401",
                                "status_message": _cpa_safe_status_message(file_obj)})
        if not targets:
            return 0, []
        deleted, failures = self._delete_batch(targets)
        return deleted, failures

    def run(self):
        self._log("[CPA清理] 开始清理")
        files = self.gateway.list_auth_files()
        self._log(f"[CPA清理] 拉取 auth-files 成功，总数: {len(files)}")

        fixed_hits = []
        probe_candidates = []

        for file_obj in files:
            reason = _cpa_reason_from_status(file_obj)
            name = str(file_obj.get("name", "") or "")
            if not name:
                continue
            if reason:
                fixed_hits.append({"name": name, "keyword": reason,
                                   "status_message": _cpa_safe_status_message(file_obj)})
                continue
            provider = str(file_obj.get("provider", "") or "").strip().lower()
            auth_index = str(file_obj.get("auth_index", "") or "").strip()
            if self.config.active_probe and provider == "codex" and auth_index:
                probe_candidates.append(file_obj)

        if (self.config.active_probe and self.config.max_active_probes > 0
                and len(probe_candidates) > self.config.max_active_probes):
            probe_candidates = probe_candidates[:self.config.max_active_probes]

        probed_hits = []
        if self.config.active_probe and self.config.max_active_probes != 0 and probe_candidates:
            self._log(f"[CPA清理] 开始主动探测，候选 {len(probe_candidates)} 个")
            with ThreadPoolExecutor(max_workers=self.config.probe_workers) as pool:
                future_map = {pool.submit(self._probe_one, item): item for item in probe_candidates}
                done = 0
                total = len(probe_candidates)
                for future in as_completed(future_map):
                    done += 1
                    name, reason = future.result()
                    if reason and not reason.startswith("probe_error"):
                        status_message = _cpa_safe_status_message(future_map[future])
                        probed_hits.append({"name": name, "keyword": reason,
                                            "status_message": status_message})

        merged_by_name = {}
        for item in fixed_hits + probed_hits:
            if item["name"] not in merged_by_name:
                merged_by_name[item["name"]] = item

        matched = list(merged_by_name.values())
        deleted_main = 0
        failures = []
        if matched:
            deleted_main, failures = self._delete_batch(matched)

        deleted_401, failures_401 = self._cleanup_401_only(set(merged_by_name.keys()))
        failures.extend(failures_401)

        result = {
            "scanned_total": len(files), "matched_total": len(matched),
            "deleted_main": deleted_main, "deleted_401": deleted_401,
            "deleted_total": deleted_main + deleted_401, "failures": failures,
        }
        self._log(f"[CPA清理] 完成: scanned={result['scanned_total']}, "
                  f"deleted_total={result['deleted_total']}")
        return result


def _cpa_execute_cleanup(payload, log=None):
    config = _CpaCleanupConfig.from_mapping(payload)
    ok, msg = config.validate()
    if not ok:
        raise ValueError(msg)
    orchestrator = _CpaCleanupOrchestrator(config=config, log=log)
    return orchestrator.run()


def _run_cpa_cleanup_before_register():
    print(f"\n{'='*60}\n  [CPA清理] 注册前清理 CPA 无效号...\n{'='*60}")
    try:
        payload = {
            "management_url": UPLOAD_API_URL,
            "management_token": UPLOAD_API_TOKEN,
            "active_probe": True, "probe_workers": 12,
            "delete_workers": 8, "max_active_probes": 120,
        }
        result = _cpa_execute_cleanup(payload, log=lambda msg: print(f"  {msg}"))
        print(f"\n  [CPA清理] 清理完成: 扫描 {result['scanned_total']} 个, "
              f"删除 {result['deleted_total']} 个")
    except Exception as e:
        print(f"  [CPA清理] ⚠️ 清理失败 (不影响注册): {e}")
    print(f"{'='*60}\n")


def _generate_password(length=14):
    lower = string.ascii_lowercase
    upper = string.ascii_uppercase
    digits = string.digits
    special = "!@#$%&*"
    pwd = [random.choice(lower), random.choice(upper),
           random.choice(digits), random.choice(special)]
    all_chars = lower + upper + digits + special
    pwd += [random.choice(all_chars) for _ in range(length - 4)]
    random.shuffle(pwd)
    return "".join(pwd)


# ================= DuckMail 邮箱函数 =================

def _create_duckmail_session():
    session = curl_requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Content-Type": "application/json",
    })
    return session


def create_temp_email():
    if not DUCKMAIL_BEARER:
        raise Exception("DUCKMAIL_BEARER 未设置，无法创建临时邮箱")
    chars = string.ascii_lowercase + string.digits
    length = random.randint(8, 13)
    email_local = "".join(random.choice(chars) for _ in range(length))
    email = f"{email_local}@duckmail.sbs"
    password = _generate_password()
    api_base = DUCKMAIL_API_BASE.rstrip("/")
    headers = {"Authorization": f"Bearer {DUCKMAIL_BEARER}"}
    session = _create_duckmail_session()
    try:
        res = session.post(f"{api_base}/accounts", json={"address": email, "password": password},
                           headers=headers, timeout=15, impersonate="chrome131")
        if res.status_code not in [200, 201]:
            raise Exception(f"创建邮箱失败: {res.status_code} - {res.text[:200]}")
        time.sleep(0.5)
        token_res = session.post(f"{api_base}/token",
                                  json={"address": email, "password": password},
                                  timeout=15, impersonate="chrome131")
        if token_res.status_code == 200:
            mail_token = token_res.json().get("token")
            if mail_token:
                return email, password, mail_token
        raise Exception(f"获取邮件 Token 失败: {token_res.status_code}")
    except Exception as e:
        raise Exception(f"DuckMail 创建邮箱失败: {e}")


def _fetch_emails_duckmail(mail_token: str):
    try:
        api_base = DUCKMAIL_API_BASE.rstrip("/")
        headers = {"Authorization": f"Bearer {mail_token}"}
        session = _create_duckmail_session()
        res = session.get(f"{api_base}/messages", headers=headers, timeout=15, impersonate="chrome131")
        if res.status_code == 200:
            data = res.json()
            return data.get("hydra:member") or data.get("member") or data.get("data") or []
        return []
    except Exception:
        return []


def _fetch_email_detail_duckmail(mail_token: str, msg_id: str):
    try:
        api_base = DUCKMAIL_API_BASE.rstrip("/")
        headers = {"Authorization": f"Bearer {mail_token}"}
        session = _create_duckmail_session()
        if isinstance(msg_id, str) and msg_id.startswith("/messages/"):
            msg_id = msg_id.split("/")[-1]
        res = session.get(f"{api_base}/messages/{msg_id}", headers=headers, timeout=15, impersonate="chrome131")
        if res.status_code == 200:
            return res.json()
    except Exception:
        pass
    return None


def _extract_verification_code(email_content: str):
    if not email_content:
        return None
    patterns = [
        r"Verification code:?\s*(\d{6})",
        r"code is\s*(\d{6})",
        r"代码为[:：]?\s*(\d{6})",
        r"验证码[:：]?\s*(\d{6})",
        r">\s*(\d{6})\s*<",
        r"(?<![#&])\b(\d{6})\b",
    ]
    for pattern in patterns:
        matches = re.findall(pattern, email_content, re.IGNORECASE)
        for code in matches:
            if code == "177010":
                continue
            return code
    return None


def wait_for_verification_email(mail_token: str, timeout: int = 120):
    start_time = time.time()
    while time.time() - start_time < timeout:
        messages = _fetch_emails_duckmail(mail_token)
        if messages:
            first_msg = messages[0]
            msg_id = first_msg.get("id") or first_msg.get("@id")
            if msg_id:
                detail = _fetch_email_detail_duckmail(mail_token, msg_id)
                if detail:
                    content = detail.get("text") or detail.get("html") or ""
                    code = _extract_verification_code(content)
                    if code:
                        return code
        time.sleep(3)
    return None


def _random_name():
    first = random.choice([
        "James", "Emma", "Liam", "Olivia", "Noah", "Ava", "Ethan", "Sophia",
        "Lucas", "Mia", "Mason", "Isabella", "Logan", "Charlotte", "Alexander",
        "Amelia", "Benjamin", "Harper", "William", "Evelyn", "Henry", "Abigail",
        "Sebastian", "Emily", "Jack", "Elizabeth",
    ])
    last = random.choice([
        "Smith", "Johnson", "Brown", "Davis", "Wilson", "Moore", "Taylor",
        "Clark", "Hall", "Young", "Anderson", "Thomas", "Jackson", "White",
        "Harris", "Martin", "Thompson", "Garcia", "Robinson", "Lewis",
        "Walker", "Allen", "King", "Wright", "Scott", "Green",
    ])
    return f"{first} {last}"


def _random_birthdate():
    y = random.randint(1985, 2002)
    m = random.randint(1, 12)
    d = random.randint(1, 28)
    return f"{y}-{m:02d}-{d:02d}"


def _quick_preflight(proxy: str = None, provider: str = "duckmail") -> bool:
    """启动前连通性检查，避免跑到中途才发现被拦截。"""
    print("\n[Preflight] 开始连通性检查...")
    sess = curl_requests.Session(impersonate="chrome131")
    if proxy:
        sess.proxies = {"http": proxy, "https": proxy}

    checks = []

    def _record(name: str, ok: bool, detail: str):
        checks.append((name, ok, detail))
        mark = "✅" if ok else "❌"
        print(f"  {mark} {name}: {detail}")

    # 1) chatgpt 首页
    try:
        r = sess.get("https://chatgpt.com/", timeout=20, allow_redirects=True)
        ok = r.status_code != 403
        _record("chatgpt.com", ok, f"status={r.status_code}")
    except Exception as e:
        _record("chatgpt.com", False, f"异常: {e}")

    # 2) CSRF 接口（必须是 JSON 且有 csrfToken）
    try:
        r = sess.get(
            "https://chatgpt.com/api/auth/csrf",
            headers={"Accept": "application/json", "Referer": "https://chatgpt.com/"},
            timeout=20,
        )
        data = r.json()
        token = data.get("csrfToken", "") if isinstance(data, dict) else ""
        ok = bool(token)
        _record("chatgpt csrf", ok, f"status={r.status_code}, token={'yes' if token else 'no'}")
    except Exception as e:
        _record("chatgpt csrf", False, f"非 JSON 或异常: {e}")

    # 3) auth.openai.com 可达性
    try:
        r = sess.get("https://auth.openai.com/", timeout=20, allow_redirects=True)
        ok = r.status_code < 500
        _record("auth.openai.com", ok, f"status={r.status_code}")
    except Exception as e:
        _record("auth.openai.com", False, f"异常: {e}")

    # 4) TempMail.lol 可达性（仅 tempmail_lol 时检查）
    if provider == "tempmail_lol":
        try:
            r = sess.get(f"{TEMPMAIL_LOL_API_BASE}/", timeout=20, allow_redirects=True)
            ok = r.status_code < 500
            _record("tempmail.lol api", ok, f"status={r.status_code}")
        except Exception as e:
            _record("tempmail.lol api", False, f"异常: {e}")

    # 5) LaMail 可达性（仅 lamail 时检查）
    if provider == "lamail":
        try:
            r = sess.get(f"{LAMAIL_API_BASE}/domains", timeout=20, allow_redirects=True)
            ok = r.status_code < 500
            _record("lamail api", ok, f"status={r.status_code}")
        except Exception as e:
            _record("lamail api", False, f"异常: {e}")

    all_ok = all(ok for _, ok, _ in checks)
    if all_ok:
        print("[Preflight] 通过，开始注册。")
    else:
        print("[Preflight] 未通过，建议先更换代理或降低并发后再试。")
    return all_ok


# ================= ChatGPTRegister 主类 =================

class ChatGPTRegister:
    BASE = "https://chatgpt.com"
    AUTH = "https://auth.openai.com"

    def __init__(self, proxy: str = None, tag: str = ""):
        self.tag = tag
        self.device_id = str(uuid.uuid4())
        self.auth_session_logging_id = str(uuid.uuid4())
        self.impersonate, self.chrome_major, self.chrome_full, self.ua, self.sec_ch_ua = _random_chrome_version()

        self.session = curl_requests.Session(impersonate=self.impersonate)
        self.proxy = proxy
        if self.proxy:
            self.session.proxies = {"http": self.proxy, "https": self.proxy}

        self.session.headers.update({
            "User-Agent": self.ua,
            "Accept-Language": random.choice([
                "en-US,en;q=0.9", "en-US,en;q=0.9,zh-CN;q=0.8",
                "en,en-US;q=0.9", "en-US,en;q=0.8",
            ]),
            "sec-ch-ua": self.sec_ch_ua, "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"', "sec-ch-ua-arch": '"x86"',
            "sec-ch-ua-bitness": '"64"',
            "sec-ch-ua-full-version": f'"{self.chrome_full}"',
            "sec-ch-ua-platform-version": f'"{random.randint(10, 15)}.0.0"',
        })
        self.session.cookies.set("oai-did", self.device_id, domain="chatgpt.com")
        self._callback_url = None

        # cfmail 状态（仅在使用 cfmail 时填充）
        self._cfmail_api_base = ""
        self._cfmail_account_name = ""
        self._cfmail_mail_token = ""

    def _log(self, step, method, url, status, body=None):
        prefix = f"[{self.tag}] " if self.tag else ""
        lines = [f"\n{'='*60}", f"{prefix}[Step] {step}",
                 f"{prefix}[{method}] {url}", f"{prefix}[Status] {status}"]
        if body:
            try:
                lines.append(f"{prefix}[Response] {json.dumps(body, indent=2, ensure_ascii=False)[:1000]}")
            except Exception:
                lines.append(f"{prefix}[Response] {str(body)[:1000]}")
        lines.append(f"{'='*60}")
        with _print_lock:
            print("\n".join(lines))

    def _print(self, msg):
        prefix = f"[{self.tag}] " if self.tag else ""
        with _print_lock:
            print(f"{prefix}{msg}")

    # ==================== DuckMail ====================

    def _create_duckmail_session(self):
        session = curl_requests.Session()
        session.headers.update({
            "User-Agent": self.ua,
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        if self.proxy:
            session.proxies = {"http": self.proxy, "https": self.proxy}
        return session

    def create_temp_email(self):
        """创建 DuckMail 临时邮箱，返回 (email, password, mail_token)"""
        if not DUCKMAIL_BEARER:
            raise Exception("DUCKMAIL_BEARER 未设置，无法创建临时邮箱")
        chars = string.ascii_lowercase + string.digits
        length = random.randint(8, 13)
        email_local = "".join(random.choice(chars) for _ in range(length))
        email = f"{email_local}@duckmail.sbs"
        password = _generate_password()
        api_base = DUCKMAIL_API_BASE.rstrip("/")
        headers = {"Authorization": f"Bearer {DUCKMAIL_BEARER}"}
        session = self._create_duckmail_session()
        try:
            res = session.post(f"{api_base}/accounts",
                               json={"address": email, "password": password},
                               headers=headers, timeout=15, impersonate=self.impersonate)
            if res.status_code not in [200, 201]:
                raise Exception(f"创建邮箱失败: {res.status_code} - {res.text[:200]}")
            time.sleep(0.5)
            token_res = session.post(f"{api_base}/token",
                                     json={"address": email, "password": password},
                                     timeout=15, impersonate=self.impersonate)
            if token_res.status_code == 200:
                mail_token = token_res.json().get("token")
                if mail_token:
                    return email, password, mail_token
            raise Exception(f"获取邮件 Token 失败: {token_res.status_code}")
        except Exception as e:
            raise Exception(f"DuckMail 创建邮箱失败: {e}")

    def _fetch_emails_duckmail(self, mail_token: str):
        try:
            api_base = DUCKMAIL_API_BASE.rstrip("/")
            headers = {"Authorization": f"Bearer {mail_token}"}
            session = self._create_duckmail_session()
            res = session.get(f"{api_base}/messages", headers=headers,
                              timeout=15, impersonate=self.impersonate)
            if res.status_code == 200:
                data = res.json()
                return data.get("hydra:member") or data.get("member") or data.get("data") or []
            return []
        except Exception:
            return []

    def _fetch_email_detail_duckmail(self, mail_token: str, msg_id: str):
        try:
            api_base = DUCKMAIL_API_BASE.rstrip("/")
            headers = {"Authorization": f"Bearer {mail_token}"}
            session = self._create_duckmail_session()
            if isinstance(msg_id, str) and msg_id.startswith("/messages/"):
                msg_id = msg_id.split("/")[-1]
            res = session.get(f"{api_base}/messages/{msg_id}", headers=headers,
                              timeout=15, impersonate=self.impersonate)
            if res.status_code == 200:
                return res.json()
        except Exception:
            pass
        return None

    def _extract_verification_code(self, email_content: str):
        if not email_content:
            return None
        patterns = [
            r"Verification code:?\s*(\d{6})",
            r"code is\s*(\d{6})",
            r"代码为[:：]?\s*(\d{6})",
            r"验证码[:：]?\s*(\d{6})",
            r">\s*(\d{6})\s*<",
            r"(?<![#&])\b(\d{6})\b",
        ]
        for pattern in patterns:
            matches = re.findall(pattern, email_content, re.IGNORECASE)
            for code in matches:
                if code == "177010":
                    continue
                return code
        return None

    # ==================== LaMail ====================

    def create_lamail_email(self):
        """创建 LaMail 临时邮箱，返回 (email, password, mail_token)"""
        api_base = LAMAIL_API_BASE.rstrip("/")
        proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None

        payload = {}
        selected_domain = random.choice(LAMAIL_DOMAINS) if LAMAIL_DOMAINS else ""
        if selected_domain:
            payload["domain"] = selected_domain

        headers = _lamail_headers(use_json=True, api_key=LAMAIL_API_KEY)
        try:
            resp = curl_requests.post(
                f"{api_base}/accounts",
                json=payload,
                headers=headers,
                proxies=proxies,
                timeout=15,
                impersonate=self.impersonate,
            )
        except Exception as e:
            raise Exception(f"LaMail 请求异常: {e}")

        if resp.status_code not in (200, 201):
            raise Exception(f"LaMail 创建失败: {resp.status_code} - {resp.text[:200]}")

        data = _lamail_unwrap_json(resp, action="创建邮箱") or {}
        if not isinstance(data, dict):
            raise Exception("LaMail 创建邮箱返回格式异常")

        email = str(data.get("address") or data.get("email") or "").strip()
        token = str(data.get("token") or "").strip()
        if not email or not token:
            raise Exception("LaMail 返回数据不完整（address/email 或 token 为空）")

        source = str(data.get("source") or "").strip()
        extra_parts = []
        if selected_domain:
            extra_parts.append(f"domain={selected_domain}")
        if source:
            extra_parts.append(f"source={source}")
        extra_text = f", {', '.join(extra_parts)}" if extra_parts else ""
        self._print(f"[lamail] 创建邮箱成功: {email}{extra_text}")
        return email, "", token

    def _fetch_emails_lamail(self, mail_token: str, email: str):
        api_base = LAMAIL_API_BASE.rstrip("/")
        proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
        if not email:
            return []
        try:
            resp = curl_requests.get(
                f"{api_base}/messages",
                params={"address": email},
                headers=_lamail_headers(bearer=mail_token),
                proxies=proxies,
                timeout=15,
                impersonate=self.impersonate,
            )
            if resp.status_code != 200:
                return []
            data = _lamail_unwrap_json(resp, action="拉取邮件列表") or {}
            if not isinstance(data, dict):
                return []
            messages = data.get("messages") or []
            return messages if isinstance(messages, list) else []
        except Exception:
            return []

    def _fetch_email_detail_lamail(self, mail_token: str, msg_id: str):
        api_base = LAMAIL_API_BASE.rstrip("/")
        proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
        if not msg_id:
            return None
        try:
            resp = curl_requests.get(
                f"{api_base}/messages/{msg_id}",
                headers=_lamail_headers(bearer=mail_token),
                proxies=proxies,
                timeout=15,
                impersonate=self.impersonate,
            )
            if resp.status_code != 200:
                return None
            data = _lamail_unwrap_json(resp, action="拉取邮件详情")
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _extract_lamail_code(self, messages: list, mail_token: str) -> Optional[str]:
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            msg_id = str(msg.get("id") or "").strip()
            content = "\n".join([
                str(msg.get("subject") or ""),
                str(msg.get("text") or ""),
                str(msg.get("html") or ""),
                str(msg.get("from") or ""),
            ])
            if "openai" not in content.lower() and "chatgpt" not in content.lower():
                detail = self._fetch_email_detail_lamail(mail_token, msg_id) if msg_id else None
                if detail:
                    content = "\n".join([
                        str(detail.get("subject") or ""),
                        str(detail.get("text") or ""),
                        str(detail.get("html") or ""),
                        str(detail.get("from") or ""),
                    ])
            if not content:
                continue
            if "openai" not in content.lower() and "chatgpt" not in content.lower():
                continue
            code = self._extract_verification_code(content)
            if code:
                return code
        return None

    # ==================== TempMail.lol ====================

    def create_tempmail_lol_email(self):
        """创建 TempMail.lol 临时邮箱，返回 (email, password, mail_token)"""
        api_base = TEMPMAIL_LOL_API_BASE.rstrip("/")
        payload = {}
        proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
        try:
            resp = curl_requests.post(
                f"{api_base}/inbox/create",
                json=payload,
                proxies=proxies,
                timeout=15,
                impersonate=self.impersonate,
            )
        except Exception as e:
            raise Exception(f"TempMail.lol 请求异常: {e}")

        if resp.status_code not in (200, 201):
            raise Exception(f"TempMail.lol 创建失败: {resp.status_code} - {resp.text[:200]}")

        data = resp.json() if resp.content else {}
        email = str(data.get("address") or data.get("email") or "").strip()
        token = str(data.get("token") or "").strip()
        if not email or not token:
            raise Exception("TempMail.lol 返回数据不完整（address/email 或 token 为空）")

        self._print(f"[tempmail_lol] 创建邮箱成功: {email}")
        return email, "", token

    def _fetch_emails_tempmail_lol(self, mail_token: str):
        api_base = TEMPMAIL_LOL_API_BASE.rstrip("/")
        proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
        try:
            resp = curl_requests.get(
                f"{api_base}/inbox",
                params={"token": mail_token},
                proxies=proxies,
                timeout=15,
                impersonate=self.impersonate,
            )
            if resp.status_code != 200:
                return []
            data = resp.json() if resp.content else {}
            emails = data.get("emails") if isinstance(data, dict) else []
            return emails if isinstance(emails, list) else []
        except Exception:
            return []

    def _extract_tempmail_lol_code(self, messages: list) -> Optional[str]:
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            content = " ".join([
                str(msg.get("subject") or ""),
                str(msg.get("body") or ""),
                str(msg.get("html") or ""),
                str(msg.get("from") or ""),
            ])
            if "openai" not in content.lower() and "chatgpt" not in content.lower():
                continue
            code = self._extract_verification_code(content)
            if code:
                return code
        return None

    # ==================== CF 自建邮箱 (cfmail) ====================

    def create_cfmail_email(self):
        """创建 CF 自建邮箱，返回 (email, password, mail_token)"""
        _reload_cfmail_accounts_if_needed()
        account = _select_cfmail_account(CFMAIL_PROFILE_MODE)
        if not account:
            raise Exception(
                f"没有可用的 cfmail 配置，请检查 {_CFMAIL_CONFIG_PATH}；"
                f"当前已加载配置数: {len(CFMAIL_ACCOUNTS)}"
            )

        proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
        local = f"oc{secrets.token_hex(5)}"

        try:
            resp = curl_requests.post(
                f"https://{account.worker_domain}/admin/new_address",
                headers={
                    "x-admin-auth": account.admin_password,
                    **_cfmail_headers(use_json=True),
                },
                json={"enablePrefix": True, "name": local, "domain": account.email_domain},
                proxies=proxies,
                impersonate=self.impersonate,
                timeout=15,
            )
        except Exception as e:
            _record_cfmail_failure(account.name, f"new_address exception: {e}")
            raise Exception(f"cfmail 请求异常: {e}")

        if resp.status_code != 200:
            _record_cfmail_failure(account.name, f"new_address status={resp.status_code}")
            raise Exception(f"cfmail 创建失败: {resp.status_code} - {resp.text[:200]}")

        data = resp.json()
        email = str(data.get("address") or "").strip()
        jwt = str(data.get("jwt") or "").strip()

        if not email or not jwt:
            _record_cfmail_failure(account.name, "new_address incomplete data")
            raise Exception("cfmail 返回数据不完整（address 或 jwt 为空）")

        # 保存 cfmail 状态供后续轮询使用
        self._cfmail_api_base = f"https://{account.worker_domain}"
        self._cfmail_account_name = account.name
        self._cfmail_mail_token = jwt

        self._print(f"[cfmail] 创建邮箱成功: {email} (配置: {account.name})")
        # cfmail 没有独立密码概念，返回空串占位
        return email, "", jwt

    def _fetch_emails_cfmail(self, mail_token: str):
        """从 cfmail 拉取邮件列表"""
        if not self._cfmail_api_base:
            return []
        proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
        try:
            resp = curl_requests.get(
                f"{self._cfmail_api_base}/api/mails",
                params={"limit": 10, "offset": 0},
                headers=_cfmail_headers(jwt=mail_token, use_json=True),
                proxies=proxies,
                impersonate=self.impersonate,
                timeout=15,
            )
            if resp.status_code != 200:
                return []
            data = resp.json() if resp.content else {}
            messages = data.get("results", []) if isinstance(data, dict) else []
            return messages if isinstance(messages, list) else []
        except Exception:
            return []

    def _extract_cfmail_code(self, messages: list, email: str) -> Optional[str]:
        """从 cfmail 邮件列表中提取验证码"""
        patterns = [
            r"Subject:\s*Your ChatGPT code is\s*(\d{6})",
            r"Your ChatGPT code is\s*(\d{6})",
            r"temporary verification code to continue:\s*(\d{6})",
            r"(?<![#&])\b(\d{6})\b",
        ]
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            recipient = str(msg.get("address") or "").strip().lower()
            if recipient and recipient != email.strip().lower():
                continue
            raw = str(msg.get("raw") or "")
            metadata = msg.get("metadata") or {}
            metadata_text = json.dumps(metadata, ensure_ascii=False)
            content = "\n".join([recipient, raw, metadata_text])
            if "openai" not in content.lower():
                continue
            for pattern in patterns:
                m = re.search(pattern, content, re.I | re.S)
                if m:
                    return m.group(1)
        return None

    # ==================== 统一等待验证码接口 ====================

    def wait_for_verification_email(self, mail_token: str, timeout: int = 120,
                                     email: str = "", provider: str = ""):
        """等待并提取 OpenAI 验证码，自动根据 provider 选择轮询方式"""
        effective_provider = provider or MAIL_PROVIDER
        poll_interval = _get_otp_poll_interval(effective_provider)
        self._print(f"[OTP] 等待验证码邮件 (最多 {timeout}s, provider={effective_provider}, poll={poll_interval:.1f}s)...")
        start_time = time.time()
        seen_ids: set = set()

        while time.time() - start_time < timeout:
            code = None

            if effective_provider == "cfmail":
                messages = self._fetch_emails_cfmail(mail_token)
                # 过滤已见过的消息
                new_messages = []
                for msg in messages:
                    msg_id = str(msg.get("id") or msg.get("createdAt") or "").strip()
                    if msg_id and msg_id not in seen_ids:
                        seen_ids.add(msg_id)
                        new_messages.append(msg)
                if new_messages:
                    code = self._extract_cfmail_code(new_messages, email)
            elif effective_provider == "tempmail_lol":
                messages = self._fetch_emails_tempmail_lol(mail_token)
                new_messages = []
                for msg in sorted(messages, key=lambda x: x.get("date", 0), reverse=True):
                    msg_id = str(msg.get("id") or msg.get("date") or msg.get("createdAt") or "").strip()
                    if msg_id and msg_id not in seen_ids:
                        seen_ids.add(msg_id)
                        new_messages.append(msg)
                if new_messages:
                    code = self._extract_tempmail_lol_code(new_messages)
            elif effective_provider == "lamail":
                messages = self._fetch_emails_lamail(mail_token, email)
                new_messages = []
                for msg in reversed(messages):
                    msg_id = str(msg.get("id") or msg.get("createdAt") or msg.get("receivedAt") or "").strip()
                    if msg_id and msg_id not in seen_ids:
                        seen_ids.add(msg_id)
                        new_messages.append(msg)
                if new_messages:
                    code = self._extract_lamail_code(new_messages, mail_token)
            else:
                # DuckMail
                messages = self._fetch_emails_duckmail(mail_token)
                if messages:
                    first_msg = messages[0]
                    msg_id = first_msg.get("id") or first_msg.get("@id")
                    if msg_id:
                        detail = self._fetch_email_detail_duckmail(mail_token, msg_id)
                        if detail:
                            content = detail.get("text") or detail.get("html") or ""
                            code = self._extract_verification_code(content)

            if code:
                self._print(f"[OTP] 验证码: {code}")
                return code

            elapsed = int(time.time() - start_time)
            self._print(f"[OTP] 等待中... ({elapsed}s/{timeout}s, poll={poll_interval:.1f}s)")
            time.sleep(poll_interval)

        self._print(f"[OTP] 超时 ({timeout}s)")
        return None

    # ==================== 注册流程 ====================

    def visit_homepage(self):
        url = f"{self.BASE}/"
        r = self.session.get(url, headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Upgrade-Insecure-Requests": "1",
        }, allow_redirects=True)
        self._log("0. Visit homepage", "GET", url, r.status_code,
                   {"cookies_count": len(self.session.cookies)})

    def get_csrf(self) -> str:
        url = f"{self.BASE}/api/auth/csrf"
        headers = {"Accept": "application/json", "Referer": f"{self.BASE}/"}

        for attempt in range(2):
            r = self.session.get(url, headers=headers)
            try:
                data = r.json()
            except Exception:
                body = (r.text or "")[:220].replace("\n", " ")
                ctype = r.headers.get("content-type", "")
                if attempt == 0:
                    self._print(f"[CSRF] 非 JSON 响应，准备重试一次: status={r.status_code}, content-type={ctype}")
                    _random_delay(0.4, 1.0)
                    self.visit_homepage()
                    continue
                raise Exception(
                    f"获取 CSRF 失败: 非 JSON 响应 (status={r.status_code}, content-type={ctype}, body={body})"
                )

            token = data.get("csrfToken", "") if isinstance(data, dict) else ""
            self._log("1. Get CSRF", "GET", url, r.status_code, data)
            if token:
                return token

            if attempt == 0:
                self._print("[CSRF] 响应中缺少 csrfToken，准备重试一次")
                _random_delay(0.4, 1.0)
                self.visit_homepage()
                continue
            raise Exception(f"获取 CSRF 失败: 响应缺少 csrfToken, data={data}")

        raise Exception("获取 CSRF 失败: 未知错误")

    def signin(self, email: str, csrf: str) -> str:
        url = f"{self.BASE}/api/auth/signin/openai"
        params = {
            "prompt": "login", "ext-oai-did": self.device_id,
            "auth_session_logging_id": self.auth_session_logging_id,
            "screen_hint": "login_or_signup", "login_hint": email,
        }
        form_data = {"callbackUrl": f"{self.BASE}/", "csrfToken": csrf, "json": "true"}
        r = self.session.post(url, params=params, data=form_data, headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json", "Referer": f"{self.BASE}/", "Origin": self.BASE,
        })
        try:
            data = r.json()
        except Exception:
            body = (r.text or "")[:220].replace("\n", " ")
            ctype = r.headers.get("content-type", "")
            raise Exception(
                f"Signin 返回非 JSON (status={r.status_code}, content-type={ctype}, body={body})"
            )
        authorize_url = data.get("url", "") if isinstance(data, dict) else ""
        self._log("2. Signin", "POST", url, r.status_code, data)
        if not authorize_url:
            raise Exception(f"Failed to get authorize URL: {data}")
        return authorize_url

    def authorize(self, url: str) -> str:
        r = self.session.get(url, headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": f"{self.BASE}/", "Upgrade-Insecure-Requests": "1",
        }, allow_redirects=True)
        final_url = str(r.url)
        self._log("3. Authorize", "GET", url, r.status_code, {"final_url": final_url})
        return final_url

    def _auth_json_headers(self, referer: str, *, include_sentinel: bool = False,
                           flow: str = "authorize_continue") -> dict:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Referer": referer,
            "Origin": self.AUTH,
            "User-Agent": self.ua,
            "oai-device-id": self.device_id,
        }
        headers.update(_make_trace_headers())
        if include_sentinel:
            sentinel = build_sentinel_token(
                self.session,
                self.device_id,
                flow=flow,
                user_agent=self.ua,
                sec_ch_ua=self.sec_ch_ua,
                impersonate=self.impersonate,
            )
            if sentinel:
                headers["openai-sentinel-token"] = sentinel
        return headers

    def _ensure_auth_session(self) -> None:
        self.session.cookies.set("oai-did", self.device_id, domain=".auth.openai.com")
        self.session.cookies.set("oai-did", self.device_id, domain="auth.openai.com")

    def bootstrap_signup(self, email: str):
        self._ensure_auth_session()

        code_verifier, code_challenge = _generate_pkce()
        state = secrets.token_urlsafe(24)
        authorize_params = {
            "response_type": "code",
            "client_id": OAUTH_CLIENT_ID,
            "redirect_uri": OAUTH_REDIRECT_URI,
            "scope": "openid profile email offline_access",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": state,
            "prompt": "login",
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
        }
        bootstrap_url = f"{OAUTH_ISSUER}/oauth/authorize?{urlencode(authorize_params)}"
        r = self.session.get(
            bootstrap_url,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": f"{self.BASE}/",
                "Upgrade-Insecure-Requests": "1",
                "User-Agent": self.ua,
            },
            allow_redirects=True,
            timeout=30,
            impersonate=self.impersonate,
        )
        self._log("0. Bootstrap oauth/authorize", "GET", bootstrap_url, r.status_code, {"final_url": str(r.url)})

        continue_url = f"{self.AUTH}/create-account"
        payload = {
            "username": {"value": email, "kind": "email"},
            "screen_hint": "signup",
        }
        headers = self._auth_json_headers(continue_url, include_sentinel=True, flow="authorize_continue")
        resp = self.session.post(
            f"{self.AUTH}/api/accounts/authorize/continue",
            json=payload,
            headers=headers,
            timeout=30,
            allow_redirects=False,
            impersonate=self.impersonate,
        )
        try:
            data = resp.json()
        except Exception:
            data = {"text": resp.text[:500]}
        self._log(
            "1. Signup authorize/continue",
            "POST",
            f"{self.AUTH}/api/accounts/authorize/continue",
            resp.status_code,
            data,
        )
        return resp.status_code, data

    def register(self, email: str, password: str):
        url = f"{self.AUTH}/api/accounts/user/register"
        headers = self._auth_json_headers(f"{self.AUTH}/create-account/password")
        r = self.session.post(
            url,
            json={"username": email, "password": password},
            headers=headers,
            timeout=30,
            impersonate=self.impersonate,
        )
        try:
            data = r.json()
        except Exception:
            data = {"text": r.text[:500]}
        self._log("2. Register password", "POST", url, r.status_code, data)
        return r.status_code, data

    def send_otp(self):
        preload_url = f"{self.AUTH}/create-account/password"
        self.session.get(preload_url, headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": self.ua,
        }, allow_redirects=True, timeout=30, impersonate=self.impersonate)

        url = f"{self.AUTH}/api/accounts/email-otp/send"
        r = self.session.get(url, headers={
            "Accept": "application/json",
            "Referer": preload_url,
            "User-Agent": self.ua,
        }, allow_redirects=True, timeout=30, impersonate=self.impersonate)
        try:
            data = r.json()
        except Exception:
            data = {"final_url": str(r.url), "status": r.status_code, "text": r.text[:300]}
        self._log("3. Send OTP", "GET", url, r.status_code, data)
        return r.status_code, data

    def validate_otp(self, code: str):
        url = f"{self.AUTH}/api/accounts/email-otp/validate"
        headers = self._auth_json_headers(f"{self.AUTH}/email-verification")
        r = self.session.post(
            url,
            json={"code": code},
            headers=headers,
            timeout=30,
            impersonate=self.impersonate,
        )
        try:
            data = r.json()
        except Exception:
            data = {"text": r.text[:500]}
        self._log("4. Validate OTP", "POST", url, r.status_code, data)
        return r.status_code, data

    def create_account(self, name: str, birthdate: str):
        url = f"{self.AUTH}/api/accounts/create_account"
        headers = self._auth_json_headers(
            f"{self.AUTH}/about-you",
            include_sentinel=True,
            flow="authorize_continue",
        )
        r = self.session.post(
            url,
            json={"name": name, "birthdate": birthdate},
            headers=headers,
            timeout=30,
            impersonate=self.impersonate,
        )
        try:
            data = r.json()
        except Exception:
            data = {"text": r.text[:500]}
        self._log("5. Create Account", "POST", url, r.status_code, data)
        if isinstance(data, dict):
            cb = data.get("continue_url") or data.get("url") or data.get("redirect_url")
            if cb:
                self._callback_url = cb
        return r.status_code, data

    def callback(self, url: str = None):
        if not url:
            url = self._callback_url
        if not url:
            self._print("[!] No callback URL, skipping.")
            return None, None
        r = self.session.get(url, headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": self.ua,
        }, allow_redirects=True)
        self._log("8. Callback", "GET", url, r.status_code, {"final_url": str(r.url)})
        return r.status_code, {"final_url": str(r.url)}

    def complete_chatgpt_callback(self, auth_code: str, state: str) -> tuple[int | None, dict[str, Any] | None]:
        auth_code = str(auth_code or "").strip()
        state = str(state or "").strip()
        if not auth_code or not state:
            self._print("[Session] 缺少 auth_code/state，无法显式完成 ChatGPT callback")
            return None, None
        callback_url = f"{self.BASE}/api/auth/callback/openai?code={auth_code}&state={state}"
        r = self.session.get(
            callback_url,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": f"{self.BASE}/",
                "Upgrade-Insecure-Requests": "1",
                "User-Agent": self.ua,
            },
            allow_redirects=True,
            timeout=30,
            impersonate=self.impersonate,
        )
        payload = {
            "final_url": str(r.url),
            "session_token": "yes" if _cookie_value(self.session, "__Secure-next-auth.session-token") else "no",
        }
        self._log("8.1 ChatGPT next-auth callback", "GET", callback_url, r.status_code, payload)
        return r.status_code, payload

    def ensure_chatgpt_session(self) -> dict[str, Any]:
        """补齐 chatgpt 登录态，尽量拿到 session-token / csrf / accessToken。"""
        info: dict[str, Any] = {
            "session_token": "",
            "csrf_token": "",
            "access_token": "",
            "final_url": "",
            "cookies": {},
        }

        try:
            homepage = self.session.get(
                f"{self.BASE}/",
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Upgrade-Insecure-Requests": "1",
                    "User-Agent": self.ua,
                },
                allow_redirects=True,
                timeout=30,
                impersonate=self.impersonate,
            )
            info["final_url"] = str(homepage.url)
            self._log("9. ChatGPT homepage bootstrap", "GET", f"{self.BASE}/", homepage.status_code, {"final_url": info["final_url"]})
        except Exception as exc:
            self._print(f"[Session] ChatGPT 首页预热异常: {exc}")

        session_token = _cookie_value(self.session, "__Secure-next-auth.session-token")
        csrf_token = _cookie_value(self.session, "__Host-next-auth.csrf-token")

        if not csrf_token:
            try:
                csrf_resp = self.session.get(
                    f"{self.BASE}/api/auth/csrf",
                    headers={
                        "Accept": "application/json",
                        "Referer": f"{self.BASE}/",
                        "User-Agent": self.ua,
                    },
                    timeout=30,
                    impersonate=self.impersonate,
                )
                csrf_data = csrf_resp.json() if csrf_resp.content else {}
                self._log("10. ChatGPT csrf", "GET", f"{self.BASE}/api/auth/csrf", csrf_resp.status_code, csrf_data)
                csrf_token = _cookie_value(self.session, "__Host-next-auth.csrf-token")
                if not csrf_token and isinstance(csrf_data, dict):
                    csrf_token = str(csrf_data.get("csrfToken") or "")
            except Exception as exc:
                self._print(f"[Session] 获取 csrf 失败: {exc}")

        access_token = ""
        try:
            session_resp = self.session.get(
                f"{self.BASE}/api/auth/session",
                headers={
                    "Accept": "application/json",
                    "Referer": f"{self.BASE}/",
                    "User-Agent": self.ua,
                },
                timeout=30,
                impersonate=self.impersonate,
            )
            session_data = session_resp.json() if session_resp.content else {}
            self._log("11. ChatGPT auth session", "GET", f"{self.BASE}/api/auth/session", session_resp.status_code, session_data)
            if isinstance(session_data, dict):
                access_token = str(session_data.get("accessToken") or session_data.get("access_token") or "")
        except Exception as exc:
            self._print(f"[Session] 获取 /api/auth/session 失败: {exc}")

        cookies: dict[str, str] = {}
        try:
            for cookie in self.session.cookies.jar:
                name = str(getattr(cookie, "name", "") or "")
                value = str(getattr(cookie, "value", "") or "")
                if name and value:
                    cookies[name] = value
        except Exception:
            pass

        info.update(
            {
                "session_token": _cookie_value(self.session, "__Secure-next-auth.session-token") or session_token,
                "csrf_token": _cookie_value(self.session, "__Host-next-auth.csrf-token") or csrf_token,
                "access_token": access_token,
                "cookies": cookies,
            }
        )
        self._print(
            f"[Session] 补齐结果: session_token={'yes' if info['session_token'] else 'no'}, "
            f"csrf_token={'yes' if info['csrf_token'] else 'no'}, access_token={'yes' if info['access_token'] else 'no'}, cookies={len(cookies)}"
        )
        return info

    def establish_chatgpt_session_via_authenticated_auth(self, email: str) -> dict[str, Any]:
        """复用已认证的 auth.openai.com 会话，补做 ChatGPT signin/openai -> callback。"""
        self._print("[Session] 尝试复用已认证 auth 会话补做 ChatGPT callback...")
        self.visit_homepage()
        _random_delay(0.8, 1.8)
        csrf = self.get_csrf()
        _random_delay(0.8, 1.8)
        auth_url = self.signin(email, csrf)
        parsed = urlparse(auth_url)
        chatgpt_state = parse_qs(parsed.query).get("state", [""])[0]
        if not chatgpt_state:
            raise RuntimeError("ChatGPT signin/openai 返回缺少 state")
        _random_delay(1.0, 2.0)
        auth_code = self._oauth_allow_redirect_extract_code(auth_url, referer=f"{self.BASE}/")
        if not auth_code:
            auth_code, _ = self._oauth_follow_for_code(auth_url, referer=f"{self.BASE}/")
        if not auth_code:
            raise RuntimeError("复用已认证 auth 会话仍未获取到 chatgpt callback code")
        _random_delay(0.8, 1.6)
        _, payload = self.complete_chatgpt_callback(auth_code, chatgpt_state)
        _random_delay(0.6, 1.4)
        session_info = self.ensure_chatgpt_session()
        if not session_info.get("session_token"):
            raise RuntimeError(
                f"ChatGPT callback 仍未建立成功: final_url={str((payload or {}).get('final_url') or '')}"
            )
        self._print("[Session] 已通过已认证 auth 会话补齐 ChatGPT next-auth session")
        return session_info

    def establish_chatgpt_web_session(self, email: str, password: str, mail_token: str | None = None,
                                      provider: str = "duckmail") -> dict[str, Any]:
        """通过 chatgpt.com 自身的 signin/openai 链建立 next-auth 登录态。"""
        self._print("[Session] 开始通过 ChatGPT 登录链建立 next-auth 会话...")
        web_session = curl_requests.Session(impersonate=self.impersonate)
        if self.proxy:
            web_session.proxies = {"http": self.proxy, "https": self.proxy}
        web_session.headers.update(dict(self.session.headers))
        web_session.cookies.set("oai-did", self.device_id, domain="chatgpt.com")
        web_session.cookies.set("oai-did", self.device_id, domain=".auth.openai.com")
        web_session.cookies.set("oai-did", self.device_id, domain="auth.openai.com")

        original_session = self.session
        self.session = web_session
        try:
            self.visit_homepage()
            _random_delay(1.6, 3.2)
            csrf = self.get_csrf()
            _random_delay(1.8, 3.6)
            auth_url = self.signin(email, csrf)
            parsed = urlparse(auth_url)
            chatgpt_state = parse_qs(parsed.query).get("state", [""])[0]
            _random_delay(2.0, 4.2)
            final_url = self.authorize(auth_url)
            continue_referer = final_url if final_url.startswith(OAUTH_ISSUER) else f"{OAUTH_ISSUER}/log-in"

            sentinel_authorize = build_sentinel_token(
                self.session,
                self.device_id,
                flow="authorize_continue",
                user_agent=self.ua,
                sec_ch_ua=self.sec_ch_ua,
                impersonate=self.impersonate,
            )
            if not sentinel_authorize:
                raise RuntimeError("ChatGPT 登录链 sentinel authorize token 生成失败")

            _random_delay(2.5, 5.0)
            resp_continue = self.session.post(
                f"{OAUTH_ISSUER}/api/accounts/authorize/continue",
                json={
                    "username": {"kind": "email", "value": email},
                    "screen_hint": "login",
                },
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Origin": OAUTH_ISSUER,
                    "Referer": continue_referer,
                    "User-Agent": self.ua,
                    "oai-device-id": self.device_id,
                    "openai-sentinel-token": sentinel_authorize,
                    **_make_trace_headers(),
                },
                timeout=30,
                allow_redirects=False,
                impersonate=self.impersonate,
            )
            if resp_continue.status_code != 200:
                raise RuntimeError(f"ChatGPT 登录链 authorize/continue 失败: {resp_continue.status_code} {resp_continue.text[:220]}")
            continue_data = resp_continue.json()
            continue_url = str(continue_data.get("continue_url") or "").strip()
            if continue_url.startswith("/"):
                continue_url = f"{OAUTH_ISSUER}{continue_url}"
            if continue_url:
                try:
                    _random_delay(1.8, 3.6)
                    self.session.get(
                        continue_url,
                        headers={
                            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                            "Referer": continue_referer,
                            "Upgrade-Insecure-Requests": "1",
                            "User-Agent": self.ua,
                        },
                        allow_redirects=True,
                        timeout=30,
                        impersonate=self.impersonate,
                    )
                except Exception:
                    pass

            sentinel_pwd = build_sentinel_token(
                self.session,
                self.device_id,
                flow="password_verify",
                user_agent=self.ua,
                sec_ch_ua=self.sec_ch_ua,
                impersonate=self.impersonate,
            )
            if not sentinel_pwd:
                raise RuntimeError("ChatGPT 登录链 sentinel password token 生成失败")

            _random_delay(2.2, 4.5)
            resp_verify = self.session.post(
                f"{OAUTH_ISSUER}/api/accounts/password/verify",
                json={"password": password},
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Origin": OAUTH_ISSUER,
                    "Referer": f"{OAUTH_ISSUER}/log-in/password",
                    "User-Agent": self.ua,
                    "oai-device-id": self.device_id,
                    "openai-sentinel-token": sentinel_pwd,
                    **_make_trace_headers(),
                },
                timeout=30,
                allow_redirects=False,
                impersonate=self.impersonate,
            )
            if resp_verify.status_code != 200:
                raise RuntimeError(f"ChatGPT 登录链 password/verify 失败: {resp_verify.status_code} {resp_verify.text[:220]}")
            verify_data = resp_verify.json()
            continue_url = str(verify_data.get("continue_url") or continue_url or "").strip()
            page_type = (verify_data.get("page") or {}).get("type", "")

            need_oauth_otp = (
                page_type == "email_otp_verification"
                or "email-verification" in continue_url
                or "email-otp" in continue_url
            )
            if need_oauth_otp:
                if not mail_token:
                    raise RuntimeError("ChatGPT 登录链需要邮箱 OTP，但缺少 mail_token")
                tried_codes: set[str] = set()
                otp_success = False
                otp_deadline = time.time() + 120
                while time.time() < otp_deadline and not otp_success:
                    candidate_codes: list[str] = []
                    if provider == "cfmail":
                        messages = self._fetch_emails_cfmail(mail_token)
                        for msg in messages[:12]:
                            content = "\n".join([
                                str(msg.get("address") or ""),
                                str(msg.get("raw") or ""),
                                json.dumps(msg.get("metadata") or {}, ensure_ascii=False),
                            ])
                            for pattern in [r"(\d{6})"]:
                                m = re.search(pattern, content, re.I | re.S)
                                if m and m.group(1) not in tried_codes:
                                    candidate_codes.append(m.group(1))
                                    break
                    elif provider == "tempmail_lol":
                        messages = self._fetch_emails_tempmail_lol(mail_token) or []
                        for msg in messages[:20]:
                            content = " ".join([
                                str(msg.get("subject") or ""),
                                str(msg.get("body") or ""),
                                str(msg.get("html") or ""),
                                str(msg.get("from") or ""),
                            ])
                            code = self._extract_verification_code(content)
                            if code and code not in tried_codes:
                                candidate_codes.append(code)
                    elif provider == "lamail":
                        messages = self._fetch_emails_lamail(mail_token, email) or []
                        for msg in messages[:20]:
                            msg_id = str(msg.get("id") or "").strip()
                            content = "\n".join([
                                str(msg.get("subject") or ""),
                                str(msg.get("text") or ""),
                                str(msg.get("html") or ""),
                                str(msg.get("from") or ""),
                            ])
                            if "openai" not in content.lower() and "chatgpt" not in content.lower():
                                detail = self._fetch_email_detail_lamail(mail_token, msg_id)
                                if detail:
                                    content = "\n".join([
                                        str(detail.get("subject") or ""),
                                        str(detail.get("text") or ""),
                                        str(detail.get("html") or ""),
                                        str(detail.get("from") or ""),
                                    ])
                            code = self._extract_verification_code(content)
                            if code and code not in tried_codes:
                                candidate_codes.append(code)
                    else:
                        messages = self._fetch_emails_duckmail(mail_token) or []
                        for msg in messages[:12]:
                            msg_id = msg.get("id") or msg.get("@id")
                            if not msg_id:
                                continue
                            detail = self._fetch_email_detail_duckmail(mail_token, msg_id)
                            if not detail:
                                continue
                            code = self._extract_verification_code(detail.get("text") or detail.get("html") or "")
                            if code and code not in tried_codes:
                                candidate_codes.append(code)

                    if not candidate_codes:
                        time.sleep(2)
                        continue

                    for otp_code in candidate_codes:
                        tried_codes.add(otp_code)
                        resp_otp = self.session.post(
                            f"{OAUTH_ISSUER}/api/accounts/email-otp/validate",
                            json={"code": otp_code},
                            headers={
                                "Accept": "application/json",
                                "Content-Type": "application/json",
                                "Origin": OAUTH_ISSUER,
                                "Referer": f"{OAUTH_ISSUER}/email-verification",
                                "User-Agent": self.ua,
                                "oai-device-id": self.device_id,
                                **_make_trace_headers(),
                            },
                            timeout=30,
                            allow_redirects=False,
                            impersonate=self.impersonate,
                        )
                        if resp_otp.status_code != 200:
                            continue
                        otp_data = resp_otp.json()
                        continue_url = str(otp_data.get("continue_url") or continue_url or "").strip()
                        page_type = (otp_data.get("page") or {}).get("type", "") or page_type
                        otp_success = True
                        break
                    if not otp_success:
                        time.sleep(2)
                if not otp_success:
                    raise RuntimeError("ChatGPT 登录链 OTP 验证失败")

            callback_code = None
            callback_hint = continue_url
            if callback_hint and callback_hint.startswith("/"):
                callback_hint = f"{OAUTH_ISSUER}{callback_hint}"
            if callback_hint:
                callback_code, _ = self._oauth_follow_for_code(callback_hint, referer=f"{OAUTH_ISSUER}/log-in/password")
            if not callback_code:
                fallback_consent = f"{OAUTH_ISSUER}/sign-in-with-chatgpt/codex/consent"
                callback_code = self._oauth_submit_workspace_and_org(fallback_consent)
                if not callback_code:
                    callback_code, _ = self._oauth_follow_for_code(fallback_consent, referer=f"{OAUTH_ISSUER}/log-in/password")
            if not callback_code:
                raise RuntimeError("ChatGPT 登录链未获取到 callback code")

            _random_delay(1.8, 3.6)
            _, payload = self.complete_chatgpt_callback(callback_code, chatgpt_state)
            _random_delay(1.4, 2.8)
            session_info = self.ensure_chatgpt_session()
            if not session_info.get("session_token"):
                raise RuntimeError(
                    f"ChatGPT next-auth callback 未建立成功: final_url={str((payload or {}).get('final_url') or '')}"
                )
            self._print("[Session] ChatGPT next-auth 会话建立成功")
            return session_info
        finally:
            if _cookie_value(web_session, "__Secure-next-auth.session-token"):
                self.session = web_session
            else:
                self.session = original_session

    # ==================== 自动注册主流程 ====================

    def run_register(self, email, password, name, birthdate, mail_token, provider="duckmail"):
        """基于 auth.openai.com 直连链路执行后端注册，避免 chatgpt csrf/signin 403 干扰。"""
        self._print("切换到 auth.openai.com 直连注册链路")
        _random_delay(0.2, 0.6)

        status, data = self.bootstrap_signup(email)
        if status != 200:
            raise Exception(f"Signup authorize/continue 失败 ({status}): {data}")

        _random_delay(0.3, 0.8)
        status, data = self.register(email, password)
        if status != 200:
            raise Exception(f"Register 失败 ({status}): {data}")

        _random_delay(0.3, 0.8)
        otp_send_status, otp_send_data = self.send_otp()
        if otp_send_status != 200:
            raise Exception(f"OTP 发送失败 ({otp_send_status}): {otp_send_data}")

        otp_code = self.wait_for_verification_email(
            mail_token, timeout=180, email=email, provider=provider
        )
        if not otp_code:
            raise Exception("未能获取验证码")

        _random_delay(0.3, 0.8)
        status, data = self.validate_otp(otp_code)
        if status != 200:
            self._print("验证码失败，准备补发并重试一次...")
            self.send_otp()
            _random_delay(1.0, 2.0)
            otp_code = self.wait_for_verification_email(
                mail_token, timeout=90, email=email, provider=provider
            )
            if not otp_code:
                raise Exception("重试后仍未获取验证码")
            _random_delay(0.3, 0.8)
            status, data = self.validate_otp(otp_code)
            if status != 200:
                raise Exception(f"验证码失败 ({status}): {data}")

        if provider == "cfmail" and self._cfmail_account_name:
            _record_cfmail_success(self._cfmail_account_name)

        _random_delay(0.5, 1.2)
        status, data = self.create_account(name, birthdate)
        if status != 200:
            raise Exception(f"Create account 失败 ({status}): {data}")

        _random_delay(0.2, 0.5)
        self.callback()
        return True

    def _decode_oauth_session_cookie(self):
        jar = getattr(self.session.cookies, "jar", None)
        if jar is not None:
            cookie_items = list(jar)
        else:
            cookie_items = []

        for c in cookie_items:
            name = getattr(c, "name", "") or ""
            if "oai-client-auth-session" not in name:
                continue
            raw_val = (getattr(c, "value", "") or "").strip()
            if not raw_val:
                continue
            candidates = [raw_val]
            try:
                from urllib.parse import unquote
                decoded = unquote(raw_val)
                if decoded != raw_val:
                    candidates.append(decoded)
            except Exception:
                pass
            for val in candidates:
                try:
                    if (val.startswith('"') and val.endswith('"')) or \
                       (val.startswith("'") and val.endswith("'")):
                        val = val[1:-1]
                    part = val.split(".")[0] if "." in val else val
                    pad = 4 - len(part) % 4
                    if pad != 4:
                        part += "=" * pad
                    raw = base64.urlsafe_b64decode(part)
                    data = json.loads(raw.decode("utf-8"))
                    if isinstance(data, dict):
                        return data
                except Exception:
                    continue
        return None

    def _oauth_allow_redirect_extract_code(self, url: str, referer: str = None):
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Upgrade-Insecure-Requests": "1", "User-Agent": self.ua,
        }
        if referer:
            headers["Referer"] = referer
        try:
            resp = self.session.get(url, headers=headers, allow_redirects=True,
                                    timeout=30, impersonate=self.impersonate)
            final_url = str(resp.url)
            code = _extract_code_from_url(final_url)
            if code:
                return code
            for r in getattr(resp, "history", []) or []:
                loc = r.headers.get("Location", "")
                code = _extract_code_from_url(loc)
                if code:
                    return code
                code = _extract_code_from_url(str(r.url))
                if code:
                    return code
        except Exception as e:
            maybe_localhost = re.search(r'(https?://localhost[^\s\'\"]+)', str(e))
            if maybe_localhost:
                code = _extract_code_from_url(maybe_localhost.group(1))
                if code:
                    return code
        return None

    def _oauth_follow_for_code(self, start_url: str, referer: str = None, max_hops: int = 16):
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Upgrade-Insecure-Requests": "1", "User-Agent": self.ua,
        }
        if referer:
            headers["Referer"] = referer
        current_url = start_url
        last_url = start_url
        for hop in range(max_hops):
            try:
                resp = self.session.get(current_url, headers=headers, allow_redirects=False,
                                        timeout=30, impersonate=self.impersonate)
            except Exception as e:
                maybe_localhost = re.search(r'(https?://localhost[^\s\'\"]+)', str(e))
                if maybe_localhost:
                    code = _extract_code_from_url(maybe_localhost.group(1))
                    if code:
                        return code, maybe_localhost.group(1)
                return None, last_url
            last_url = str(resp.url)
            code = _extract_code_from_url(last_url)
            if code:
                return code, last_url
            if resp.status_code in (301, 302, 303, 307, 308):
                loc = resp.headers.get("Location", "")
                if not loc:
                    return None, last_url
                if loc.startswith("/"):
                    loc = f"{OAUTH_ISSUER}{loc}"
                code = _extract_code_from_url(loc)
                if code:
                    return code, loc
                current_url = loc
                headers["Referer"] = last_url
                continue
            return None, last_url
        return None, last_url

    def _oauth_submit_workspace_and_org(self, consent_url: str):
        session_data = self._decode_oauth_session_cookie()
        if not session_data:
            return None
        workspaces = session_data.get("workspaces", [])
        if not workspaces:
            return None
        workspace_id = (workspaces[0] or {}).get("id")
        if not workspace_id:
            return None

        h = {
            "Accept": "application/json", "Content-Type": "application/json",
            "Origin": OAUTH_ISSUER, "Referer": consent_url,
            "User-Agent": self.ua, "oai-device-id": self.device_id,
        }
        h.update(_make_trace_headers())

        resp = self.session.post(
            f"{OAUTH_ISSUER}/api/accounts/workspace/select",
            json={"workspace_id": workspace_id}, headers=h,
            allow_redirects=False, timeout=30, impersonate=self.impersonate,
        )
        self._print(f"[OAuth] workspace/select -> {resp.status_code}")

        if resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("Location", "")
            if loc.startswith("/"):
                loc = f"{OAUTH_ISSUER}{loc}"
            code = _extract_code_from_url(loc)
            if code:
                return code
            code, _ = self._oauth_follow_for_code(loc, referer=consent_url)
            if not code:
                code = self._oauth_allow_redirect_extract_code(loc, referer=consent_url)
            return code

        if resp.status_code != 200:
            return None

        try:
            ws_data = resp.json()
        except Exception:
            return None

        ws_next = ws_data.get("continue_url", "")
        orgs = ws_data.get("data", {}).get("orgs", [])

        org_id = None
        project_id = None
        if orgs:
            org_id = (orgs[0] or {}).get("id")
            projects = (orgs[0] or {}).get("projects", [])
            if projects:
                project_id = (projects[0] or {}).get("id")

        if org_id:
            org_body = {"org_id": org_id}
            if project_id:
                org_body["project_id"] = project_id
            h_org = dict(h)
            if ws_next:
                h_org["Referer"] = ws_next if ws_next.startswith("http") else f"{OAUTH_ISSUER}{ws_next}"
            resp_org = self.session.post(
                f"{OAUTH_ISSUER}/api/accounts/organization/select",
                json=org_body, headers=h_org, allow_redirects=False,
                timeout=30, impersonate=self.impersonate,
            )
            self._print(f"[OAuth] organization/select -> {resp_org.status_code}")
            if resp_org.status_code in (301, 302, 303, 307, 308):
                loc = resp_org.headers.get("Location", "")
                if loc.startswith("/"):
                    loc = f"{OAUTH_ISSUER}{loc}"
                code = _extract_code_from_url(loc)
                if code:
                    return code
                code, _ = self._oauth_follow_for_code(loc, referer=h_org.get("Referer"))
                if not code:
                    code = self._oauth_allow_redirect_extract_code(loc, referer=h_org.get("Referer"))
                return code
            if resp_org.status_code == 200:
                try:
                    org_data = resp_org.json()
                except Exception:
                    return None
                org_next = org_data.get("continue_url", "")
                if org_next:
                    if org_next.startswith("/"):
                        org_next = f"{OAUTH_ISSUER}{org_next}"
                    code, _ = self._oauth_follow_for_code(org_next, referer=h_org.get("Referer"))
                    if not code:
                        code = self._oauth_allow_redirect_extract_code(org_next, referer=h_org.get("Referer"))
                    return code

        if ws_next:
            if ws_next.startswith("/"):
                ws_next = f"{OAUTH_ISSUER}{ws_next}"
            code, _ = self._oauth_follow_for_code(ws_next, referer=consent_url)
            if not code:
                code = self._oauth_allow_redirect_extract_code(ws_next, referer=consent_url)
            return code

        return None

    def perform_codex_oauth_login_http(self, email: str, password: str, mail_token: str = None,
                                        provider: str = "duckmail"):
        self._print("[OAuth] 开始执行 Codex OAuth 纯协议流程...")

        oauth_session = curl_requests.Session(impersonate=self.impersonate)
        if self.proxy:
            oauth_session.proxies = {"http": self.proxy, "https": self.proxy}
        oauth_session.headers.update(dict(self.session.headers))
        oauth_session.cookies.set("oai-did", self.device_id, domain=".auth.openai.com")
        oauth_session.cookies.set("oai-did", self.device_id, domain="auth.openai.com")

        code_verifier, code_challenge = _generate_pkce()
        state = secrets.token_urlsafe(24)

        authorize_params = {
            "response_type": "code",
            "client_id": OAUTH_CLIENT_ID,
            "redirect_uri": OAUTH_REDIRECT_URI,
            "scope": "openid profile email offline_access",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": state,
            "prompt": "login",
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
        }
        authorize_url = f"{OAUTH_ISSUER}/oauth/authorize?{urlencode(authorize_params)}"

        def _oauth_json_headers(referer: str, *, include_sentinel: bool = False,
                                flow: str = "authorize_continue"):
            h = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Origin": OAUTH_ISSUER,
                "Referer": referer,
                "User-Agent": self.ua,
                "oai-device-id": self.device_id,
            }
            h.update(_make_trace_headers())
            if include_sentinel:
                sentinel = build_sentinel_token(
                    oauth_session,
                    self.device_id,
                    flow=flow,
                    user_agent=self.ua,
                    sec_ch_ua=self.sec_ch_ua,
                    impersonate=self.impersonate,
                )
                if sentinel:
                    h["openai-sentinel-token"] = sentinel
            return h

        self._print("[OAuth] 1/7 GET /oauth/authorize")
        try:
            r = oauth_session.get(
                authorize_url,
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Referer": f"{self.BASE}/",
                    "Upgrade-Insecure-Requests": "1",
                    "User-Agent": self.ua,
                },
                allow_redirects=True,
                timeout=30,
                impersonate=self.impersonate,
            )
        except Exception as e:
            self._print(f"[OAuth] /oauth/authorize 异常: {e}")
            return None

        authorize_final_url = str(r.url)
        self._print("[OAuth] 2/7 POST /api/accounts/authorize/continue")
        try:
            resp_continue = oauth_session.post(
                f"{OAUTH_ISSUER}/api/accounts/authorize/continue",
                json={
                    "username": {"kind": "email", "value": email},
                    "screen_hint": "login",
                },
                headers=_oauth_json_headers(authorize_final_url, include_sentinel=True, flow="authorize_continue"),
                timeout=30,
                allow_redirects=False,
                impersonate=self.impersonate,
            )
        except Exception as e:
            self._print(f"[OAuth] authorize/continue 异常: {e}")
            return None

        self._print(f"[OAuth] /authorize/continue -> {resp_continue.status_code}")
        if resp_continue.status_code != 200:
            self._print(f"[OAuth] authorize/continue 非200: {resp_continue.status_code}, body={resp_continue.text[:220]}")
            return None

        try:
            continue_data = resp_continue.json()
        except Exception:
            self._print(f"[OAuth] authorize/continue JSON 解析失败: {resp_continue.text[:220]}")
            return None

        continue_url = str(continue_data.get("continue_url") or "").strip()
        if continue_url:
            if continue_url.startswith("/"):
                continue_url = f"{OAUTH_ISSUER}{continue_url}"
            try:
                oauth_session.get(
                    continue_url,
                    headers={
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Referer": authorize_final_url,
                        "Upgrade-Insecure-Requests": "1",
                        "User-Agent": self.ua,
                    },
                    allow_redirects=True,
                    timeout=30,
                    impersonate=self.impersonate,
                )
            except Exception as e:
                self._print(f"[OAuth] continue_url 预热异常: {e}")

        self._print("[OAuth] 3/7 POST /api/accounts/password/verify")
        try:
            resp_verify = oauth_session.post(
                f"{OAUTH_ISSUER}/api/accounts/password/verify",
                json={"password": password},
                headers=_oauth_json_headers(f"{OAUTH_ISSUER}/log-in/password", include_sentinel=True, flow="password_verify"),
                timeout=30,
                allow_redirects=False,
                impersonate=self.impersonate,
            )
        except Exception as e:
            self._print(f"[OAuth] password/verify 异常: {e}")
            return None

        if resp_verify.status_code != 200:
            self._print(f"[OAuth] password/verify 非200: {resp_verify.status_code}, body={resp_verify.text[:220]}")
            return None

        try:
            verify_data = resp_verify.json()
        except Exception:
            self._print(f"[OAuth] password/verify JSON 解析失败: {resp_verify.text[:220]}")
            return None

        continue_url = str(verify_data.get("continue_url") or continue_url or "").strip()
        page_type = (verify_data.get("page") or {}).get("type", "")

        need_oauth_otp = (
            page_type == "email_otp_verification"
            or "email-verification" in continue_url
            or "email-otp" in continue_url
        )

        if need_oauth_otp:
            self._print("[OAuth] 4/7 检测到邮箱 OTP 验证")
            if not mail_token:
                self._print("[OAuth] OAuth 阶段需要邮箱 OTP，但未提供 mail_token")
                return None

            try:
                oauth_session.get(
                    f"{OAUTH_ISSUER}/email-verification",
                    headers={
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Referer": f"{OAUTH_ISSUER}/log-in/password",
                        "Upgrade-Insecure-Requests": "1",
                        "User-Agent": self.ua,
                    },
                    allow_redirects=True,
                    timeout=30,
                    impersonate=self.impersonate,
                )
            except Exception:
                pass

            headers_otp = _oauth_json_headers(f"{OAUTH_ISSUER}/email-verification")
            tried_codes: set = set()
            otp_success = False
            otp_deadline = time.time() + 120

            while time.time() < otp_deadline and not otp_success:
                candidate_codes = []
                if provider == "cfmail":
                    messages = self._fetch_emails_cfmail(mail_token)
                    for msg in messages[:12]:
                        msg_id = str(msg.get("id") or msg.get("createdAt") or "").strip()
                        if not msg_id:
                            continue
                        recipient = str(msg.get("address") or "").strip().lower()
                        if recipient and recipient != email.strip().lower():
                            continue
                        raw = str(msg.get("raw") or "")
                        metadata = msg.get("metadata") or {}
                        content = "\n".join([recipient, raw, json.dumps(metadata, ensure_ascii=False)])
                        if "openai" not in content.lower():
                            continue
                        for pattern in [
                            r"Subject:\s*Your ChatGPT code is\s*(\d{6})",
                            r"Your ChatGPT code is\s*(\d{6})",
                            r"temporary verification code to continue:\s*(\d{6})",
                            r"(?<![#&])\b(\d{6})\b",
                        ]:
                            m = re.search(pattern, content, re.I | re.S)
                            if m and m.group(1) not in tried_codes:
                                candidate_codes.append(m.group(1))
                                break
                elif provider == "tempmail_lol":
                    messages = self._fetch_emails_tempmail_lol(mail_token) or []
                    for msg in messages[:20]:
                        content = " ".join([
                            str(msg.get("subject") or ""),
                            str(msg.get("body") or ""),
                            str(msg.get("html") or ""),
                            str(msg.get("from") or ""),
                        ])
                        if "openai" not in content.lower() and "chatgpt" not in content.lower():
                            continue
                        code = self._extract_verification_code(content)
                        if code and code not in tried_codes:
                            candidate_codes.append(code)
                elif provider == "lamail":
                    messages = self._fetch_emails_lamail(mail_token, email) or []
                    for msg in messages[:20]:
                        msg_id = str(msg.get("id") or "").strip()
                        content = "\n".join([
                            str(msg.get("subject") or ""),
                            str(msg.get("text") or ""),
                            str(msg.get("html") or ""),
                            str(msg.get("from") or ""),
                        ])
                        if "openai" not in content.lower() and "chatgpt" not in content.lower():
                            detail = self._fetch_email_detail_lamail(mail_token, msg_id)
                            if detail:
                                content = "\n".join([
                                    str(detail.get("subject") or ""),
                                    str(detail.get("text") or ""),
                                    str(detail.get("html") or ""),
                                    str(detail.get("from") or ""),
                                ])
                        code = self._extract_verification_code(content)
                        if code and code not in tried_codes:
                            candidate_codes.append(code)
                else:
                    messages = self._fetch_emails_duckmail(mail_token) or []
                    for msg in messages[:12]:
                        msg_id = msg.get("id") or msg.get("@id")
                        if not msg_id:
                            continue
                        detail = self._fetch_email_detail_duckmail(mail_token, msg_id)
                        if not detail:
                            continue
                        content = detail.get("text") or detail.get("html") or ""
                        code = self._extract_verification_code(content)
                        if code and code not in tried_codes:
                            candidate_codes.append(code)

                if not candidate_codes:
                    elapsed = int(120 - max(0, otp_deadline - time.time()))
                    self._print(f"[OAuth] OTP 等待中... ({elapsed}s/120s)")
                    time.sleep(2)
                    continue

                for otp_code in candidate_codes:
                    tried_codes.add(otp_code)
                    self._print(f"[OAuth] 尝试 OTP: {otp_code}")
                    try:
                        resp_otp = oauth_session.post(
                            f"{OAUTH_ISSUER}/api/accounts/email-otp/validate",
                            json={"code": otp_code},
                            headers=headers_otp,
                            timeout=30,
                            allow_redirects=False,
                            impersonate=self.impersonate,
                        )
                    except Exception as e:
                        self._print(f"[OAuth] email-otp/validate 异常: {e}")
                        continue
                    if resp_otp.status_code != 200:
                        continue
                    try:
                        otp_data = resp_otp.json()
                    except Exception:
                        continue
                    continue_url = str(otp_data.get("continue_url") or continue_url or "").strip()
                    page_type = (otp_data.get("page") or {}).get("type", "") or page_type
                    otp_success = True
                    break

                if not otp_success:
                    time.sleep(2)

            if not otp_success:
                self._print("[OAuth] OAuth 阶段 OTP 验证失败")
                return None

        code = None
        consent_url = continue_url
        if consent_url and consent_url.startswith("/"):
            consent_url = f"{OAUTH_ISSUER}{consent_url}"
        if not consent_url and "consent" in page_type:
            consent_url = f"{OAUTH_ISSUER}/sign-in-with-chatgpt/codex/consent"
        if consent_url:
            code = _extract_code_from_url(consent_url)

        original_session = self.session
        self.session = oauth_session
        try:
            if not code and consent_url:
                self._print("[OAuth] 5/7 跟随 continue_url 提取 code")
                code, _ = self._oauth_follow_for_code(consent_url, referer=f"{OAUTH_ISSUER}/log-in/password")

            consent_hint = (
                ("consent" in (consent_url or ""))
                or ("sign-in-with-chatgpt" in (consent_url or ""))
                or ("workspace" in (consent_url or ""))
                or ("organization" in (consent_url or ""))
                or ("consent" in page_type)
                or ("organization" in page_type)
            )

            if not code and consent_hint:
                if not consent_url:
                    consent_url = f"{OAUTH_ISSUER}/sign-in-with-chatgpt/codex/consent"
                self._print("[OAuth] 6/7 执行 workspace/org 选择")
                code = self._oauth_submit_workspace_and_org(consent_url)

            if not code:
                fallback_consent = f"{OAUTH_ISSUER}/sign-in-with-chatgpt/codex/consent"
                self._print("[OAuth] 6/7 回退 consent 路径重试")
                code = self._oauth_submit_workspace_and_org(fallback_consent)
                if not code:
                    code, _ = self._oauth_follow_for_code(fallback_consent, referer=f"{OAUTH_ISSUER}/log-in/password")
        finally:
            self.session = original_session

        if not code:
            self._print("[OAuth] 未获取到 authorization code")
            return None

        self.complete_chatgpt_callback(code, state)

        self._print("[OAuth] 7/7 POST /oauth/token")
        token_resp = oauth_session.post(
            f"{OAUTH_ISSUER}/oauth/token",
            headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": self.ua},
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": OAUTH_REDIRECT_URI,
                "client_id": OAUTH_CLIENT_ID,
                "code_verifier": code_verifier,
            },
            timeout=60,
            impersonate=self.impersonate,
        )
        self._print(f"[OAuth] /oauth/token -> {token_resp.status_code}")

        if token_resp.status_code != 200:
            self._print(f"[OAuth] token 交换失败: {token_resp.status_code} {token_resp.text[:200]}")
            return None

        try:
            data = token_resp.json()
        except Exception:
            self._print("[OAuth] token 响应解析失败")
            return None

        if not data.get("access_token"):
            self._print("[OAuth] token 响应缺少 access_token")
            return None

        self.session = oauth_session
        self._print("[OAuth] Codex Token 获取成功")
        return data


# ==================== 并发批量注册 ====================

def _register_one(idx, total, proxy, output_file):
    """单个注册任务，根据 MAIL_PROVIDER 选择邮箱服务"""
    reg = None
    try:
        reg = ChatGPTRegister(proxy=proxy, tag=f"{idx}")
        provider = MAIL_PROVIDER

        # 根据邮箱服务创建临时邮箱
        if provider == "cfmail":
            reg._print("[cfmail] 创建 CF 自建邮箱...")
            email, email_pwd, mail_token = reg.create_cfmail_email()
        elif provider == "tempmail_lol":
            reg._print("[tempmail_lol] 创建临时邮箱...")
            email, email_pwd, mail_token = reg.create_tempmail_lol_email()
        elif provider == "lamail":
            reg._print("[lamail] 创建临时邮箱...")
            email, email_pwd, mail_token = reg.create_lamail_email()
        else:
            reg._print("[DuckMail] 创建临时邮箱...")
            if not DUCKMAIL_BEARER:
                raise Exception("DUCKMAIL_BEARER 未设置，请在 config.json 中配置或改用 cfmail/lamail/tempmail_lol")
            email, email_pwd, mail_token = reg.create_temp_email()

        tag = email.split("@")[0]
        reg.tag = tag

        chatgpt_password = _generate_password()
        name = _random_name()
        birthdate = _random_birthdate()

        with _print_lock:
            print(f"\n{'=' * 60}")
            print(f"  [{idx}/{total}] 注册: {email}")
            print(f"  邮箱服务: {provider}")
            print(f"  ChatGPT密码: {chatgpt_password}")
            if email_pwd:
                print(f"  邮箱密码: {email_pwd}")
            print(f"  姓名: {name} | 生日: {birthdate}")
            print(f"{'=' * 60}")

        # 执行注册流程
        reg.run_register(email, chatgpt_password, name, birthdate, mail_token, provider=provider)

        # OAuth（可选）
        oauth_ok = True
        if ENABLE_OAUTH:
            reg._print("[OAuth] 开始获取 Codex Token...")
            tokens = reg.perform_codex_oauth_login_http(
                email, chatgpt_password, mail_token=mail_token, provider=provider
            )
            oauth_ok = bool(tokens and tokens.get("access_token"))
            if oauth_ok:
                _save_codex_tokens(email, tokens, register=reg)
                reg._print("[OAuth] Token 已保存")
            else:
                msg = "OAuth 获取失败"
                if OAUTH_REQUIRED:
                    raise Exception(f"{msg}（oauth_required=true）")
                reg._print(f"[OAuth] {msg}（按配置继续）")

        # 线程安全写入结果
        with _file_lock:
            with open(output_file, "a", encoding="utf-8") as out:
                line = f"{email}----{chatgpt_password}"
                if email_pwd:
                    line += f"----{email_pwd}"
                line += f"----oauth={'ok' if oauth_ok else 'fail'}\n"
                out.write(line)

        with _print_lock:
            print(f"\n[OK] [{tag}] {email} 注册成功!")
        return True, email, None

    except Exception as e:
        error_msg = str(e)
        with _print_lock:
            print(f"\n[FAIL] [{idx}] 注册失败: {error_msg}")
            traceback.print_exc()
        return False, None, error_msg

def request_stop_register() -> None:
    global _STOP_REQUESTED
    _STOP_REQUESTED = True


def clear_stop_register() -> None:
    global _STOP_REQUESTED
    _STOP_REQUESTED = False


def is_stop_requested() -> bool:
    return bool(_STOP_REQUESTED)


def run_batch(total_accounts: int = 3, output_file="registered_accounts.txt",
              max_workers=3, proxy=None, cpa_cleanup=None, cpa_upload_every_n: int = 3):
    """并发批量注册"""
    provider = MAIL_PROVIDER

    # 检查邮箱服务配置
    if provider == "cfmail":
        if not CFMAIL_ACCOUNTS:
            print(f"❌ 错误: mail_provider=cfmail 但未找到可用的 cfmail 配置")
            print(f"   请检查配置文件: {_CFMAIL_CONFIG_PATH}")
            return
    elif provider == "duckmail":
        if not DUCKMAIL_BEARER:
            print("❌ 错误: mail_provider=duckmail 但未设置 DUCKMAIL_BEARER")
            print("   请在 config.json 中设置 duckmail_bearer，或将 mail_provider 改为 cfmail/lamail/tempmail_lol")
            return
    elif provider not in {"tempmail_lol", "lamail"}:
        print(f"❌ 错误: 不支持的 mail_provider={provider}")
        print("   可选值: duckmail / cfmail / lamail / tempmail_lol")
        return

    actual_workers = min(max_workers, total_accounts)
    print(f"\n{'#' * 60}")
    print(f"  ChatGPT 批量自动注册")
    print(f"  注册数量: {total_accounts} | 并发数: {actual_workers}")
    print(f"  邮箱服务: {provider}")
    if provider == "cfmail":
        cfmail_names = ", ".join(a.name for a in CFMAIL_ACCOUNTS)
        print(f"  cfmail 配置: {cfmail_names}")
        print(f"  cfmail 模式: {CFMAIL_PROFILE_MODE}")
    elif provider == "duckmail":
        print(f"  DuckMail: {DUCKMAIL_API_BASE}")
    elif provider == "tempmail_lol":
        print(f"  TempMail.lol: {TEMPMAIL_LOL_API_BASE}")
    elif provider == "lamail":
        print(f"  LaMail: {LAMAIL_API_BASE}")
        if LAMAIL_DOMAIN_TEXT:
            print(f"  LaMail 域名池: {LAMAIL_DOMAIN_TEXT}")
    print(f"  OAuth: {'开启' if ENABLE_OAUTH else '关闭'} | required: {'是' if OAUTH_REQUIRED else '否'}")
    if ENABLE_OAUTH:
        print(f"  Token输出: {TOKEN_JSON_DIR}/, {AK_FILE}, {RK_FILE}")
    print(f"  CPA分批上传: 每 {max(1, int(cpa_upload_every_n))} 个成功账号触发一次（异步入队）")
    print(f"  OTP轮询: default={OTP_POLL_INTERVAL_DEFAULT:.1f}s, lamail={OTP_POLL_INTERVAL_BY_PROVIDER.get('lamail', OTP_POLL_INTERVAL_DEFAULT):.1f}s")
    print(f"  Delay系数: {REGISTER_DELAY_FACTOR:.2f}")
    print(f"  输出文件: {output_file}")
    print(f"{'#' * 60}\n")

    # 注册前清理 CPA 无效号
    do_cleanup = cpa_cleanup if cpa_cleanup is not None else CPA_CLEANUP_ENABLED
    if do_cleanup and UPLOAD_API_URL:
        _run_cpa_cleanup_before_register()

    success_count = 0
    fail_count = 0
    completed_count = 0
    start_time = time.time()
    upload_every_n = max(1, int(cpa_upload_every_n or 1))
    since_last_upload = 0

    # 初始化底部进度条
    _render_apt_like_progress(completed_count, total_accounts, success_count, fail_count, start_time)

    clear_stop_register()

    with ThreadPoolExecutor(max_workers=actual_workers) as executor:
        futures = {}
        for idx in range(1, total_accounts + 1):
            if is_stop_requested():
                print(f"\n[STOP] 已收到暂停请求，停止继续创建新注册任务（已提交 {len(futures)} 个）")
                break
            future = executor.submit(_register_one, idx, total_accounts, proxy, output_file)
            futures[future] = idx

        for future in as_completed(futures):
            idx = futures[future]
            try:
                ok, email, err = future.result()
                if ok:
                    success_count += 1
                    since_last_upload += 1
                    if UPLOAD_API_URL and since_last_upload >= upload_every_n:
                        queued = _enqueue_tokens_for_cpa(limit=since_last_upload)
                        print(f"\n[CPA] 达到分批上传阈值: {since_last_upload}/{upload_every_n}，已异步入队 {queued} 个文件")
                        since_last_upload = 0
                else:
                    fail_count += 1
                    print(f"  [账号 {idx}] 失败: {err}")
            except Exception as e:
                fail_count += 1
                with _print_lock:
                    print(f"[FAIL] 账号 {idx} 线程异常: {e}")
            finally:
                completed_count += 1
                _render_apt_like_progress(completed_count, total_accounts, success_count, fail_count, start_time)

    # 结束进度条占用行
    with _print_lock:
        print()

    elapsed = time.time() - start_time
    avg = elapsed / total_accounts if total_accounts else 0
    print(f"\n{'#' * 60}")
    print(f"  注册完成! 耗时 {elapsed:.1f} 秒")
    print(f"  总数: {total_accounts} | 成功: {success_count} | 失败: {fail_count}")
    print(f"  平均速度: {avg:.1f} 秒/个")
    if success_count > 0:
        print(f"  结果文件: {output_file}")
    print(f"{'#' * 60}")

    if is_stop_requested():
        print("[STOP] 当前批次已按请求停止继续创建新账号，已提交线程已自然结束")

    if success_count > 0 and UPLOAD_API_URL:
        if since_last_upload > 0:
            queued = _enqueue_tokens_for_cpa(limit=since_last_upload)
            print(f"\n[CPA] 收尾阶段已补充入队 {queued} 个成功账号对应 token...")
        _flush_cpa_upload_queue(force=True)

def main():
    print("=" * 60)
    print("  ChatGPT 批量自动注册工具")
    print(f"  邮箱服务: {MAIL_PROVIDER}")
    print("=" * 60)

    provider = MAIL_PROVIDER

    # 检查配置
    if provider == "cfmail":
        if not CFMAIL_ACCOUNTS:
            print(f"\n⚠️  警告: mail_provider=cfmail 但未找到可用配置")
            print(f"   配置文件: {_CFMAIL_CONFIG_PATH}")
            print(f"   请参考 zhuce5_cfmail_accounts.json 格式补充配置")
            print("\n   按 Enter 继续尝试运行 (可能会失败)...")
            input()
        else:
            cfmail_names = ", ".join(a.name for a in CFMAIL_ACCOUNTS)
            print(f"\n[Info] cfmail 配置已加载: {cfmail_names}")
    elif provider == "duckmail":
        if not DUCKMAIL_BEARER:
            print("\n⚠️  警告: 未设置 DUCKMAIL_BEARER")
            print("   请编辑 config.json 设置 duckmail_bearer，或将 mail_provider 改为 cfmail/lamail/tempmail_lol")
            print("\n   按 Enter 继续尝试运行 (可能会失败)...")
            input()
    elif provider == "tempmail_lol":
        print(f"\n[Info] TempMail.lol 已启用: {TEMPMAIL_LOL_API_BASE}")
    elif provider == "lamail":
        print(f"\n[Info] LaMail 已启用: {LAMAIL_API_BASE}")
        if LAMAIL_DOMAIN_TEXT:
            print(f"[Info] LaMail 域名池: {LAMAIL_DOMAIN_TEXT} (创建时随机选择)")
        if LAMAIL_API_KEY:
            print("[Info] LaMail API Key: 已配置")
        else:
            print("[Info] LaMail API Key: 未配置（将使用匿名临时邮箱能力）")
    else:
        print(f"\n❌ 错误: 不支持的 mail_provider={provider}")
        print("   可选值: duckmail / cfmail / lamail / tempmail_lol")
        return

    # 代理配置
    proxy = DEFAULT_PROXY
    if proxy:
        print(f"[Info] 检测到默认代理: {proxy}")
        use_default = input("使用此代理? (Y/n): ").strip().lower()
        if use_default == "n":
            proxy = input("输入代理地址 (留空=不使用代理): ").strip() or None
    else:
        proxy = input("输入代理地址 (如 http://127.0.0.1:7890，留空=不使用代理): ").strip() or None

    print(f"[Info] {'使用代理: ' + proxy if proxy else '不使用代理'}")

    # 启动前网络预检（可选）
    preflight_input = input("\n执行启动前连通性预检? (Y/n): ").strip().lower()
    if preflight_input != "n":
        if not _quick_preflight(proxy=proxy, provider=provider):
            print("\n⚠️  预检失败，按 Enter 退出；输入 c 可继续强制运行")
            action = input("继续? (c/Enter): ").strip().lower()
            if action != "c":
                return

    # CPA 清理
    cpa_cleanup = False
    if UPLOAD_API_URL:
        cleanup_input = input("\n注册前清理 CPA 无效号? (Y/n): ").strip().lower()
        cpa_cleanup = cleanup_input != "n"

    # 注册数量
    count_input = input(f"\n注册账号数量 (默认 {DEFAULT_TOTAL_ACCOUNTS}): ").strip()
    total_accounts = (int(count_input) if count_input.isdigit() and int(count_input) > 0
                      else DEFAULT_TOTAL_ACCOUNTS)

    workers_input = input("并发数 (默认 3): ").strip()
    max_workers = int(workers_input) if workers_input.isdigit() and int(workers_input) > 0 else 3

    upload_n_input = input(f"每成功多少个账号触发 CPA 上传 (默认 {CPA_UPLOAD_EVERY_N}): ").strip()
    cpa_upload_every_n = (int(upload_n_input)
                          if upload_n_input.isdigit() and int(upload_n_input) > 0
                          else CPA_UPLOAD_EVERY_N)

    run_batch(total_accounts=total_accounts, output_file=DEFAULT_OUTPUT_FILE,
              max_workers=max_workers, proxy=proxy, cpa_cleanup=cpa_cleanup,
              cpa_upload_every_n=cpa_upload_every_n)

if __name__ == "__main__":
    main()
