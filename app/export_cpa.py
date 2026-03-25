from __future__ import annotations

import argparse
from pathlib import Path

from app.account_store import AccountStore, EXPORT_DIR


def main() -> int:
    parser = argparse.ArgumentParser(description="从 SQLite 账号库导出 CPA JSON / ZIP")
    parser.add_argument("--email", action="append", help="导出指定 email，可重复传入多个")
    parser.add_argument("--all", action="store_true", help="导出所有账号为 zip")
    parser.add_argument("--zip-name", default="accounts_export.zip", help="批量导出的 zip 文件名")
    parser.add_argument("--output-dir", default=str(EXPORT_DIR), help="导出目录")
    args = parser.parse_args()

    store = AccountStore()
    output_dir = Path(args.output_dir)

    if args.all:
        emails = [row["email"] for row in store.list_accounts()]
        zip_path = store.export_accounts_zip(emails, export_dir=output_dir, zip_name=args.zip_name)
        print(f"[OK] 批量导出完成: {zip_path}")
        return 0

    if args.email:
        if len(args.email) == 1:
            path = store.export_account_json(args.email[0], export_dir=output_dir)
            print(f"[OK] 单个账号导出完成: {path}")
            return 0
        zip_path = store.export_accounts_zip(args.email, export_dir=output_dir, zip_name=args.zip_name)
        print(f"[OK] 多账号导出完成: {zip_path}")
        return 0

    parser.error("请提供 --email 或 --all")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
