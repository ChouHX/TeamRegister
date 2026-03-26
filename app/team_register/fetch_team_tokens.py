#!/usr/bin/env python3
"""
支付完成后，重新 OAuth 登录获取 Team tokens。

读取 accounts.txt（格式: email:password），
对每个账号重新执行 Codex OAuth 登录，
此时 team workspace 已存在，workspace/select 响应里会有 team org_id，
写入 tokens_team.txt（格式: email:org_id:client_id:refresh_token:access_token:id_token）

用法:
    python fetch_team_tokens.py                  # 读 accounts.txt
    python fetch_team_tokens.py paid.txt         # 指定账号文件
"""

import json
import os
import re
import sys
import time
import uuid
import random
import secrets
import hashlib
import base64
import threading
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs, urlencode

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── 配置 ──────────────────────────────────────────────
_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
with open(_CONFIG_FILE, encoding="utf-8") as _f:
    _cfg = json.load(_f)

PROXY            = _cfg.get("proxy", "")
OAUTH_ISSUER     = _cfg.get("oauth_issuer", "https://auth.openai.com")
OAUTH_CLIENT_ID  = _cfg.get("oauth_client_id", "")
OAUTH_REDIRECT_URI = _cfg.get("oauth_redirect_uri", "http://localhost:1455/auth/callback")
ACCOUNTS_FILE    = _cfg.get("accounts_file", "accounts.txt")
TOKENS_TEAM_FILE = _cfg.get("tokens_team_file", "tokens_team.txt")

OPENAI_AUTH_BASE = "https://auth.openai.com"
_file_lock = threading.Lock()

# ── HTTP Headers ───────────────────────────────────────
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Safari/537.36"
)

COMMON_HEADERS = {
    "accept": "application/json",
    "accept-language": "en-US,en;q=0.9",
    "content-type": "application/json",
    "origin": OPENAI_AUTH_BASE,
    "user-agent": USER_AGENT,
    "sec-ch-ua": '"Google Chrome";v="145", "Not?A_Brand";v="8", "Chromium";v="145"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}

NAVIGATE_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "user-agent": USER_AGENT,
    "sec-ch-ua": '"Google Chrome";v="145", "Not?A_Brand";v="8", "Chromium";v="145"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "same-origin",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
}


# ── 工具函数 ───────────────────────────────────────────

def create_session():
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    if PROXY:
        session.proxies = {"http": PROXY, "https": PROXY}
    return session


def generate_device_id():
    return str(uuid.uuid4())


def generate_pkce():
    code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


def generate_datadog_trace():
    trace_id = str(random.getrandbits(64))
    parent_id = str(random.getrandbits(64))
    trace_hex = format(int(trace_id), '016x')
    parent_hex = format(int(parent_id), '016x')
    return {
        "traceparent": f"00-0000000000000000{trace_hex}-{parent_hex}-01",
        "tracestate": "dd=s:1;o:rum",
        "x-datadog-origin": "rum",
        "x-datadog-parent-id": parent_id,
        "x-datadog-sampling-priority": "1",
        "x-datadog-trace-id": trace_id,
    }


def decode_jwt_payload(token):
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


# ── Sentinel Token PoW ─────────────────────────────────

class SentinelTokenGenerator:
    MAX_ATTEMPTS = 500000
    ERROR_PREFIX = "wQ8Lk5FbGpA2NcR9dShT6gYjU7VxZ4D"

    def __init__(self, device_id=None):
        self.device_id = device_id or generate_device_id()
        self.requirements_seed = str(random.random())
        self.sid = str(uuid.uuid4())

    @staticmethod
    def _fnv1a_32(text):
        h = 2166136261
        for ch in text:
            h ^= ord(ch)
            h = ((h * 16777619) & 0xFFFFFFFF)
        h ^= (h >> 16)
        h = ((h * 2246822507) & 0xFFFFFFFF)
        h ^= (h >> 13)
        h = ((h * 3266489909) & 0xFFFFFFFF)
        h ^= (h >> 16)
        return format(h & 0xFFFFFFFF, '08x')

    def _get_config(self):
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%a %b %d %Y %H:%M:%S GMT+0000 (Coordinated Universal Time)")
        nav_props = ["vendorSub", "productSub", "vendor", "maxTouchPoints",
                     "hardwareConcurrency", "cookieEnabled", "credentials"]
        nav_prop = random.choice(nav_props)
        perf_now = random.uniform(1000, 50000)
        return [
            "1920x1080", date_str, 4294705152, random.random(), USER_AGENT,
            "https://sentinel.openai.com/sentinel/20260124ceb8/sdk.js",
            None, None, "en-US", "en-US,en", random.random(),
            f"{nav_prop}\u2212undefined",
            random.choice(["location", "URL", "documentURI"]),
            random.choice(["Object", "Function", "Array"]),
            perf_now, self.sid, "", random.choice([4, 8, 12, 16]),
            time.time() * 1000 - perf_now,
        ]

    @staticmethod
    def _base64_encode(data):
        json_str = json.dumps(data, separators=(',', ':'), ensure_ascii=False)
        return base64.b64encode(json_str.encode('utf-8')).decode('ascii')

    def _run_check(self, start_time, seed, difficulty, config, nonce):
        config[3] = nonce
        config[9] = round((time.time() - start_time) * 1000)
        data = self._base64_encode(config)
        hash_hex = self._fnv1a_32(seed + data)
        if hash_hex[:len(difficulty)] <= difficulty:
            return data + "~S"
        return None

    def generate_token(self, seed=None, difficulty=None):
        if seed is None:
            seed = self.requirements_seed
            difficulty = difficulty or "0"
        start_time = time.time()
        config = self._get_config()
        for i in range(self.MAX_ATTEMPTS):
            result = self._run_check(start_time, seed, difficulty, config, i)
            if result:
                print(f"  ✅ PoW 完成: {i+1} 次迭代, 耗时 {time.time()-start_time:.2f}s")
                return "gAAAAAB" + result
        return "gAAAAAB" + self.ERROR_PREFIX + self._base64_encode(str(None))

    def generate_requirements_token(self):
        config = self._get_config()
        config[3] = 1
        config[9] = round(random.uniform(5, 50))
        return "gAAAAAC" + self._base64_encode(config)


def fetch_sentinel_challenge(session, device_id, flow="authorize_continue"):
    gen = SentinelTokenGenerator(device_id=device_id)
    p_token = gen.generate_requirements_token()
    req_body = {"p": p_token, "id": device_id, "flow": flow}
    headers = {
        "Content-Type": "text/plain;charset=UTF-8",
        "Referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html",
        "User-Agent": USER_AGENT,
        "Origin": "https://sentinel.openai.com",
    }
    try:
        resp = session.post(
            "https://sentinel.openai.com/backend-api/sentinel/req",
            data=json.dumps(req_body), headers=headers, timeout=15, verify=False,
        )
        if resp.status_code != 200:
            print(f"  ❌ sentinel {resp.status_code}: {resp.text[:200]}")
            return None
        return resp.json()
    except Exception as e:
        print(f"  ❌ sentinel 异常: {e}")
        return None


def build_sentinel_token(session, device_id, flow="authorize_continue"):
    challenge = fetch_sentinel_challenge(session, device_id, flow)
    if not challenge:
        return None
    c_value = challenge.get("token", "")
    pow_data = challenge.get("proofofwork", {})
    gen = SentinelTokenGenerator(device_id=device_id)
    if pow_data.get("required") and pow_data.get("seed"):
        p_value = gen.generate_token(seed=pow_data["seed"], difficulty=pow_data.get("difficulty", "0"))
    else:
        p_value = gen.generate_requirements_token()
    return json.dumps({"p": p_value, "t": "", "c": c_value, "id": device_id, "flow": flow})


# ── OAuth 登录 ─────────────────────────────────────────

def wait_for_verification_code(ori_email):
    """需要 OTP 时提示用户手动输入"""
    print(f"\n  📬 登录触发了邮箱验证，请查收 {ori_email.email} 的邮件")
    code = input("  请输入6位验证码: ").strip()
    return code if code else None


def codex_exchange_code(code, code_verifier):
    print("  🔄 换取 Codex Token...")
    session = create_session()
    for attempt in range(2):
        try:
            resp = session.post(
                f"{OAUTH_ISSUER}/oauth/token",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": OAUTH_REDIRECT_URI,
                    "client_id": OAUTH_CLIENT_ID,
                    "code_verifier": code_verifier,
                },
                verify=False, timeout=60,
            )
            break
        except Exception as e:
            if attempt == 0:
                time.sleep(2)
                continue
            print(f"  ❌ Token 交换失败: {e}")
            return None
    if resp.status_code == 200:
        data = resp.json()
        print(f"  ✅ Token 获取成功！access_token 长度: {len(data.get('access_token', ''))}")
        return data
    else:
        print(f"  ❌ Token 交换失败: {resp.status_code} {resp.text[:200]}")
        return None


def perform_oauth_login(ori_email, email, password):
    print(f"\n🔐 OAuth 登录: {email}")
    session = create_session()
    device_id = generate_device_id()
    session.cookies.set("oai-did", device_id, domain=".auth.openai.com")
    session.cookies.set("oai-did", device_id, domain="auth.openai.com")

    code_verifier, code_challenge = generate_pkce()
    state = secrets.token_urlsafe(32)

    authorize_url = f"{OAUTH_ISSUER}/oauth/authorize?" + urlencode({
        "response_type": "code",
        "client_id": OAUTH_CLIENT_ID,
        "redirect_uri": OAUTH_REDIRECT_URI,
        "scope": "openid profile email offline_access",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    })

    # 步骤1: GET /oauth/authorize
    try:
        resp = session.get(authorize_url, headers=NAVIGATE_HEADERS,
                           allow_redirects=True, verify=False, timeout=30)
        print(f"  [1] authorize: {resp.status_code} → {resp.url[:80]}")
    except Exception as e:
        print(f"  ❌ authorize 失败: {e}")
        return None

    # 步骤2: POST authorize/continue（邮箱）
    headers = dict(COMMON_HEADERS)
    headers["referer"] = f"{OAUTH_ISSUER}/log-in"
    headers["oai-device-id"] = device_id
    headers.update(generate_datadog_trace())
    sentinel = build_sentinel_token(session, device_id, flow="authorize_continue")
    if not sentinel:
        return None
    headers["openai-sentinel-token"] = sentinel

    try:
        resp = session.post(
            f"{OAUTH_ISSUER}/api/accounts/authorize/continue",
            json={"username": {"kind": "email", "value": email}},
            headers=headers, verify=False, timeout=30,
        )
        print(f"  [2] authorize/continue: {resp.status_code}")
        if resp.status_code != 200:
            print(f"  响应: {resp.text[:200]}")
            return None
    except Exception as e:
        print(f"  ❌ authorize/continue 失败: {e}")
        return None

    # 步骤3: POST password/verify
    headers["referer"] = f"{OAUTH_ISSUER}/log-in/password"
    headers.update(generate_datadog_trace())
    sentinel = build_sentinel_token(session, device_id, flow="password_verify")
    if not sentinel:
        return None
    headers["openai-sentinel-token"] = sentinel

    try:
        resp = session.post(
            f"{OAUTH_ISSUER}/api/accounts/password/verify",
            json={"password": password},
            headers=headers, verify=False, timeout=30, allow_redirects=False,
        )
        print(f"  [3] password/verify: {resp.status_code}")
        if resp.status_code != 200:
            print(f"  响应: {resp.text[:200]}")
            return None
    except Exception as e:
        print(f"  ❌ password/verify 失败: {e}")
        return None

    try:
        data = resp.json()
        continue_url = data.get("continue_url", "")
        page_type = data.get("page", {}).get("type", "")
    except Exception:
        continue_url = ""
        page_type = ""

    if not continue_url:
        print("  ❌ 未获取到 continue_url")
        return None

    # 步骤3.5: 邮箱 OTP（通常不触发，触发时让用户手动输入）
    if page_type == "email_otp_verification" or "email-verification" in continue_url:
        print("  [3.5] 需要邮箱验证...")
        h_val = dict(COMMON_HEADERS)
        h_val["referer"] = f"{OAUTH_ISSUER}/email-verification"
        h_val["oai-device-id"] = device_id
        h_val.update(generate_datadog_trace())

        code = wait_for_verification_code(ori_email)
        if not code:
            print("  ❌ 未输入验证码")
            return None

        resp = session.post(
            f"{OAUTH_ISSUER}/api/accounts/email-otp/validate",
            json={"code": code},
            headers=h_val, verify=False, timeout=30,
        )
        if resp.status_code != 200:
            print(f"  ❌ OTP 验证失败: {resp.status_code}")
            return None
        try:
            data = resp.json()
            continue_url = data.get("continue_url", "")
            page_type = data.get("page", {}).get("type", "")
        except Exception:
            pass

        if not continue_url or "email-verification" in continue_url:
            print("  ❌ OTP 后未获取到 consent URL")
            return None

    # 步骤4: consent → workspace/select → organization/select → code
    if continue_url.startswith("/"):
        consent_url = f"{OAUTH_ISSUER}{continue_url}"
    else:
        consent_url = continue_url
    print(f"  [4] consent: {consent_url[:80]}")

    def _extract_code(url):
        if not url or "code=" not in url:
            return None
        try:
            return parse_qs(urlparse(url).query).get("code", [None])[0]
        except Exception:
            return None

    def _decode_auth_session():
        for c in session.cookies:
            if c.name == "oai-client-auth-session":
                val = c.value
                first_part = val.split(".")[0] if "." in val else val
                pad = 4 - len(first_part) % 4
                if pad != 4:
                    first_part += "=" * pad
                try:
                    raw = base64.urlsafe_b64decode(first_part)
                    return json.loads(raw.decode("utf-8"))
                except Exception:
                    pass
        return None

    def _follow(url, depth=10):
        if depth <= 0:
            return None
        try:
            r = session.get(url, headers=NAVIGATE_HEADERS, verify=False,
                            timeout=15, allow_redirects=False)
            if r.status_code in (301, 302, 303, 307, 308):
                loc = r.headers.get("Location", "")
                code = _extract_code(loc)
                if code:
                    return code
                if loc.startswith("/"):
                    loc = f"{OAUTH_ISSUER}{loc}"
                return _follow(loc, depth - 1)
            elif r.status_code == 200:
                return _extract_code(r.url)
        except requests.exceptions.ConnectionError as e:
            m = re.search(r'(https?://localhost[^\s\'"]+)', str(e))
            if m:
                return _extract_code(m.group(1))
        except Exception:
            pass
        return None

    auth_code   = None
    team_org_id = []

    # 4a: GET consent
    try:
        resp = session.get(consent_url, headers=NAVIGATE_HEADERS,
                           verify=False, timeout=30, allow_redirects=False)
        if resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("Location", "")
            auth_code = _extract_code(loc) or _follow(loc)
        elif resp.status_code == 200:
            print(f"  [4a] consent HTML {len(resp.text)} 字节")
    except requests.exceptions.ConnectionError as e:
        m = re.search(r'(https?://localhost[^\s\'"]+)', str(e))
        if m:
            auth_code = _extract_code(m.group(1))
    except Exception as e:
        print(f"  ⚠️ consent 异常: {e}")

    # 4b: workspace/select
    if not auth_code:
        session_data = _decode_auth_session()
        workspace_id = None
        if session_data:
            workspaces = session_data.get("workspaces", [])
            if workspaces:
                workspace_id = workspaces[0].get("id")
                print(f"  [4b] workspace_id: {workspace_id} (kind: {workspaces[0].get('kind', '?')})")
            else:
                print(f"  ⚠️ session 中无 workspaces")
        else:
            print(f"  ⚠️ 无法解码 oai-client-auth-session")

        if workspace_id:
            h_ws = dict(COMMON_HEADERS)
            h_ws["referer"] = consent_url
            h_ws["oai-device-id"] = device_id
            h_ws.update(generate_datadog_trace())
            try:
                resp = session.post(
                    f"{OAUTH_ISSUER}/api/accounts/workspace/select",
                    json={"workspace_id": workspace_id},
                    headers=h_ws, verify=False, timeout=30, allow_redirects=False,
                )
                print(f"  [4b] workspace/select: {resp.status_code}")
                if resp.status_code in (301, 302, 303, 307, 308):
                    loc = resp.headers.get("Location", "")
                    auth_code = _extract_code(loc) or _follow(loc)
                elif resp.status_code == 200:
                    ws_data = resp.json()
                    ws_next = ws_data.get("continue_url", "")
                    ws_page = ws_data.get("page", {}).get("type", "")
                    ws_orgs = ws_data.get("data", {}).get("orgs", [])

                    # 提取 org_id（team 的 account_id）
                    org_id = None
                    project_id = None
                    if ws_orgs:
                        org_id = ws_orgs[0].get("id")
                        projects = ws_orgs[0].get("projects", [])
                        if projects:
                            project_id = projects[0].get("id")
                        if org_id:
                            team_org_id.append(org_id)
                            print(f"  ✅ org_id (team): {org_id}")

                    # 4c: organization/select
                    if org_id and ("organization" in ws_next or "organization" in ws_page):
                        org_url = ws_next if ws_next.startswith("http") else f"{OAUTH_ISSUER}{ws_next}"
                        h_org = dict(COMMON_HEADERS)
                        h_org["referer"] = org_url
                        h_org["oai-device-id"] = device_id
                        h_org.update(generate_datadog_trace())
                        body = {"org_id": org_id}
                        if project_id:
                            body["project_id"] = project_id
                        resp = session.post(
                            f"{OAUTH_ISSUER}/api/accounts/organization/select",
                            json=body, headers=h_org, verify=False, timeout=30,
                            allow_redirects=False,
                        )
                        print(f"  [4c] organization/select: {resp.status_code}")
                        if resp.status_code in (301, 302, 303, 307, 308):
                            loc = resp.headers.get("Location", "")
                            auth_code = _extract_code(loc) or _follow(loc)
                        elif resp.status_code == 200:
                            org_next = resp.json().get("continue_url", "")
                            if org_next:
                                full = org_next if org_next.startswith("http") else f"{OAUTH_ISSUER}{org_next}"
                                auth_code = _follow(full)
                    elif ws_next:
                        full = ws_next if ws_next.startswith("http") else f"{OAUTH_ISSUER}{ws_next}"
                        auth_code = _follow(full)
            except Exception as e:
                print(f"  ⚠️ workspace/select 异常: {e}")

    # 4d: 备用
    if not auth_code:
        print("  [4d] 备用: allow_redirects=True ...")
        try:
            resp = session.get(consent_url, headers=NAVIGATE_HEADERS,
                               verify=False, timeout=30, allow_redirects=True)
            auth_code = _extract_code(resp.url)
            if not auth_code:
                for r in resp.history:
                    auth_code = _extract_code(r.headers.get("Location", ""))
                    if auth_code:
                        break
        except requests.exceptions.ConnectionError as e:
            m = re.search(r'(https?://localhost[^\s\'"]+)', str(e))
            if m:
                auth_code = _extract_code(m.group(1))
        except Exception as e:
            print(f"  ⚠️ 备用异常: {e}")

    if not auth_code:
        print("  ❌ 未获取到 authorization code")
        return None

    tokens = codex_exchange_code(auth_code, code_verifier)
    if tokens and team_org_id:
        tokens["org_id"] = team_org_id[0]
    return tokens


# ── 文件处理 ───────────────────────────────────────────

def load_accounts(path):
    accounts = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(":", 1)
                if len(parts) == 2:
                    accounts.append((parts[0].strip(), parts[1].strip()))
                else:
                    print(f"  ⚠️ 格式错误，跳过: {line[:60]}")
    except FileNotFoundError:
        print(f"❌ 找不到账号文件: {path}")
    return accounts


def already_done(email):
    try:
        with open(TOKENS_TEAM_FILE, encoding="utf-8") as f:
            for line in f:
                if line.startswith(email + ":"):
                    return True
    except FileNotFoundError:
        pass
    return False


# ── 主流程 ─────────────────────────────────────────────

class _Email:
    """简单邮箱对象，OTP 触发时让用户手动输入"""
    def __init__(self, addr):
        self.email = addr


def fetch_one(email, password, index, total):
    tag = f"[{index}/{total}]"
    print(f"\n{tag} {email}")

    if already_done(email):
        print(f"{tag} ⏭️  已有记录，跳过")
        return True

    ori_email = _Email(email)
    tokens = perform_oauth_login(ori_email, email, password)

    if not tokens:
        print(f"{tag} ❌ 登录失败")
        return False

    access_token  = tokens.get("access_token", "")
    refresh_token = tokens.get("refresh_token", "")
    id_token      = tokens.get("id_token", "")

    # account_id 从 access_token JWT 里取（选了 team workspace 后是 team 的 account-xxx）
    payload    = decode_jwt_payload(access_token)
    auth_info  = payload.get("https://api.openai.com/auth", {})
    account_id = auth_info.get("chatgpt_account_id", "")

    if not account_id:
        print(f"{tag} ⚠️  未获取到 account_id")
        return False
    
    with _file_lock:
        with open(TOKENS_TEAM_FILE, "a", encoding="utf-8") as f:
            f.write(f"{email}:{account_id}:{OAUTH_CLIENT_ID}:{refresh_token}:{access_token}:{id_token}\n")

    print(f"{tag} ✅ 写入 {TOKENS_TEAM_FILE} — account_id: {account_id}")
    return True


def main():
    accounts_file = sys.argv[1] if len(sys.argv) > 1 else ACCOUNTS_FILE
    accounts = load_accounts(accounts_file)
    if not accounts:
        print("没有账号可处理")
        return

    total = len(accounts)
    print(f"📋 共 {total} 个账号，开始获取 Team tokens...\n")

    ok = fail = 0
    for i, (email, password) in enumerate(accounts, 1):
        success = fetch_one(email, password, i, total)
        if success:
            ok += 1
        else:
            fail += 1
        if i < total:
            time.sleep(2)

    print(f"\n✅ 完成: {ok} 成功 / {fail} 失败")
    print(f"📄 结果已写入: {TOKENS_TEAM_FILE}")


if __name__ == "__main__":
    main()
