from __future__ import annotations

import json
import sqlite3
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "accounts.db"
EXPORT_DIR = DATA_DIR / "exports"


@dataclass
class AccountRecord:
    email: str
    account_id: str
    access_token: str
    refresh_token: str
    id_token: str
    session_token: str
    csrf_token: str
    device_id: str
    user_agent: str
    sec_ch_ua: str
    cookies: dict[str, Any]
    expired: str
    last_refresh: str
    password: str = ""
    type: str = "codex"
    tags: list[str] | None = None
    payment_profile: dict[str, Any] | None = None


class AccountStore:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    email TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL DEFAULT '',
                    access_token TEXT NOT NULL DEFAULT '',
                    refresh_token TEXT NOT NULL DEFAULT '',
                    id_token TEXT NOT NULL DEFAULT '',
                    session_token TEXT NOT NULL DEFAULT '',
                    csrf_token TEXT NOT NULL DEFAULT '',
                    device_id TEXT NOT NULL DEFAULT '',
                    user_agent TEXT NOT NULL DEFAULT '',
                    sec_ch_ua TEXT NOT NULL DEFAULT '',
                    cookies_json TEXT NOT NULL DEFAULT '{}',
                    expired TEXT NOT NULL DEFAULT '',
                    last_refresh TEXT NOT NULL DEFAULT '',
                    password TEXT NOT NULL DEFAULT '',
                    type TEXT NOT NULL DEFAULT 'codex',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    payment_profile_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(accounts)").fetchall()}
            if "password" not in columns:
                conn.execute("ALTER TABLE accounts ADD COLUMN password TEXT NOT NULL DEFAULT ''")
            if "tags_json" not in columns:
                conn.execute("ALTER TABLE accounts ADD COLUMN tags_json TEXT NOT NULL DEFAULT '[]'")
            if "payment_profile_json" not in columns:
                conn.execute("ALTER TABLE accounts ADD COLUMN payment_profile_json TEXT NOT NULL DEFAULT '{}' ")
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS trg_accounts_updated_at
                AFTER UPDATE ON accounts
                BEGIN
                    UPDATE accounts SET updated_at = CURRENT_TIMESTAMP WHERE email = NEW.email;
                END;
                """
            )
            conn.commit()

    def upsert_account(self, payload: dict[str, Any]) -> None:
        record = self._normalize_payload(payload)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO accounts (
                    email, account_id, access_token, refresh_token, id_token,
                    session_token, csrf_token, device_id, user_agent, sec_ch_ua,
                    cookies_json, expired, last_refresh, password, type, tags_json, payment_profile_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(email) DO UPDATE SET
                    account_id=excluded.account_id,
                    access_token=excluded.access_token,
                    refresh_token=excluded.refresh_token,
                    id_token=excluded.id_token,
                    session_token=excluded.session_token,
                    csrf_token=excluded.csrf_token,
                    device_id=excluded.device_id,
                    user_agent=excluded.user_agent,
                    sec_ch_ua=excluded.sec_ch_ua,
                    cookies_json=excluded.cookies_json,
                    expired=excluded.expired,
                    last_refresh=excluded.last_refresh,
                    password=CASE WHEN excluded.password <> '' THEN excluded.password ELSE accounts.password END,
                    type=excluded.type,
                    tags_json=excluded.tags_json,
                    payment_profile_json=CASE WHEN excluded.payment_profile_json <> '{}' THEN excluded.payment_profile_json ELSE accounts.payment_profile_json END
                """,
                (
                    record.email,
                    record.account_id,
                    record.access_token,
                    record.refresh_token,
                    record.id_token,
                    record.session_token,
                    record.csrf_token,
                    record.device_id,
                    record.user_agent,
                    record.sec_ch_ua,
                    json.dumps(record.cookies, ensure_ascii=False),
                    record.expired,
                    record.last_refresh,
                    record.password,
                    record.type,
                    json.dumps(record.tags or [], ensure_ascii=False),
                    json.dumps(record.payment_profile or {}, ensure_ascii=False),
                ),
            )
            conn.commit()

    def get_account(self, email: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM accounts WHERE email = ?", (email,)).fetchone()
        return self._row_to_dict(row) if row else None

    def list_accounts(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM accounts ORDER BY updated_at DESC, email ASC"
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def find_account_by_access_token(self, access_token: str) -> Optional[dict[str, Any]]:
        token = str(access_token or "").strip()
        if not token:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM accounts WHERE access_token = ? ORDER BY updated_at DESC LIMIT 1",
                (token,),
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def save_payment_profile(self, email: str, payment_profile: dict[str, Any]) -> bool:
        target = str(email or "").strip()
        if not target:
            return False
        normalized_profile = self._normalize_payment_profile(payment_profile)
        if not normalized_profile:
            return False
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE accounts SET payment_profile_json = ? WHERE email = ?",
                (json.dumps(normalized_profile, ensure_ascii=False), target),
            )
            conn.commit()
            return cursor.rowcount > 0

    def delete_account(self, email: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM accounts WHERE email = ?", (email,))
            conn.commit()
            return cursor.rowcount > 0

    def delete_accounts(self, emails: Iterable[str]) -> int:
        email_list = list(dict.fromkeys(str(email).strip() for email in emails if str(email).strip()))
        if not email_list:
            return 0
        placeholders = ", ".join("?" for _ in email_list)
        with self._connect() as conn:
            cursor = conn.execute(f"DELETE FROM accounts WHERE email IN ({placeholders})", tuple(email_list))
            conn.commit()
            return int(cursor.rowcount or 0)

    def export_account_json(self, email: str, export_dir: Path = EXPORT_DIR) -> Path:
        account = self.get_account(email)
        if not account:
            raise FileNotFoundError(f"账号不存在: {email}")
        export_dir.mkdir(parents=True, exist_ok=True)
        output_path = export_dir / f"{email}.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(account, f, ensure_ascii=False, indent=2)
        return output_path

    def export_accounts_zip(self, emails: Iterable[str], export_dir: Path = EXPORT_DIR, zip_name: str = "accounts_export.zip") -> Path:
        export_dir.mkdir(parents=True, exist_ok=True)
        email_list = list(dict.fromkeys(str(email).strip() for email in emails if str(email).strip()))
        if not email_list:
            raise ValueError("没有可导出的账号")
        zip_path = export_dir / zip_name
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for email in email_list:
                account = self.get_account(email)
                if not account:
                    continue
                payload = json.dumps(account, ensure_ascii=False, indent=2)
                zf.writestr(f"{email}.json", payload)
        return zip_path

    def mark_account_team_enabled(self, email: str) -> bool:
        target = str(email or "").strip()
        if not target:
            return False
        account = self.get_account(target)
        if not account:
            return False
        tags = account.get("tags") if isinstance(account.get("tags"), list) else []
        normalized = []
        seen: set[str] = set()
        for item in [*tags, "TEAM"]:
            value = str(item or "").strip().upper()
            if not value or value in seen:
                continue
            seen.add(value)
            normalized.append(value)
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE accounts SET tags_json = ? WHERE email = ?",
                (json.dumps(normalized, ensure_ascii=False), target),
            )
            conn.commit()
            return cursor.rowcount > 0

    def _normalize_payment_profile(self, profile: Any) -> dict[str, Any]:
        if not isinstance(profile, dict):
            return {}
        allowed_fields = (
            "payment_billing_name",
            "payment_billing_email",
            "payment_billing_line1",
            "payment_billing_city",
            "payment_billing_state",
            "payment_billing_postal_code",
            "payment_billing_country",
            "payment_card_number",
            "payment_card_exp_month",
            "payment_card_exp_year",
            "payment_card_cvc",
        )
        normalized: dict[str, Any] = {}
        for field in allowed_fields:
            value = str(profile.get(field) or "").strip()
            if value:
                normalized[field] = value
        return normalized

    def _normalize_payload(self, payload: dict[str, Any]) -> AccountRecord:
        cookies = payload.get("cookies") or {}
        if not isinstance(cookies, dict):
            cookies = {}
        tags = payload.get("tags") or []
        if not isinstance(tags, list):
            tags = []
        normalized_tags: list[str] = []
        seen_tags: set[str] = set()
        for item in tags:
            value = str(item or "").strip().upper()
            if not value or value in seen_tags:
                continue
            seen_tags.add(value)
            normalized_tags.append(value)
        payment_profile = self._normalize_payment_profile(payload.get("payment_profile") or {})
        return AccountRecord(
            email=str(payload.get("email") or "").strip(),
            account_id=str(payload.get("account_id") or "").strip(),
            access_token=str(payload.get("access_token") or "").strip(),
            refresh_token=str(payload.get("refresh_token") or "").strip(),
            id_token=str(payload.get("id_token") or "").strip(),
            session_token=str(payload.get("session_token") or "").strip(),
            csrf_token=str(payload.get("csrf_token") or "").strip(),
            device_id=str(payload.get("device_id") or "").strip(),
            user_agent=str(payload.get("user_agent") or "").strip(),
            sec_ch_ua=str(payload.get("sec_ch_ua") or "").strip(),
            cookies=cookies,
            expired=str(payload.get("expired") or "").strip(),
            last_refresh=str(payload.get("last_refresh") or "").strip(),
            password=str(payload.get("password") or "").strip(),
            type=str(payload.get("type") or "codex").strip() or "codex",
            tags=normalized_tags,
            payment_profile=payment_profile,
        )

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        cookies_json = str(row["cookies_json"] or "{}")
        try:
            cookies = json.loads(cookies_json)
            if not isinstance(cookies, dict):
                cookies = {}
        except Exception:
            cookies = {}
        tags_json = str(row["tags_json"] or "[]") if "tags_json" in row.keys() else "[]"
        try:
            tags = json.loads(tags_json)
            if not isinstance(tags, list):
                tags = []
        except Exception:
            tags = []
        payment_profile_json = str(row["payment_profile_json"] or "{}") if "payment_profile_json" in row.keys() else "{}"
        try:
            payment_profile = json.loads(payment_profile_json)
            if not isinstance(payment_profile, dict):
                payment_profile = {}
        except Exception:
            payment_profile = {}
        normalized_tags: list[str] = []
        seen_tags: set[str] = set()
        for item in tags:
            value = str(item or "").strip().upper()
            if not value or value in seen_tags:
                continue
            seen_tags.add(value)
            normalized_tags.append(value)
        return {
            "type": row["type"],
            "email": row["email"],
            "expired": row["expired"],
            "id_token": row["id_token"],
            "account_id": row["account_id"],
            "access_token": row["access_token"],
            "last_refresh": row["last_refresh"],
            "refresh_token": row["refresh_token"],
            "session_token": row["session_token"],
            "csrf_token": row["csrf_token"],
            "device_id": row["device_id"],
            "user_agent": row["user_agent"],
            "sec_ch_ua": row["sec_ch_ua"],
            "password": row["password"],
            "cookies": cookies,
            "tags": normalized_tags,
            "payment_profile": self._normalize_payment_profile(payment_profile),
        }
