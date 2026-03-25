from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="TeamRegister unified entrypoint")
    parser.add_argument(
        "mode",
        nargs="?",
        default="web",
        choices=["web", "register", "pay"],
        help="启动模式：web / register / pay",
    )
    parser.add_argument("--host", default="0.0.0.0", help="web 模式监听地址")
    parser.add_argument("--port", type=int, default=8000, help="web 模式监听端口")
    parser.add_argument("--reload", action="store_true", help="web 模式启用热重载")
    args = parser.parse_args()

    if args.mode == "web":
        import uvicorn

        uvicorn.run("app.register_web:app", host=args.host, port=args.port, reload=args.reload)
        return 0

    if args.mode == "register":
        from app import ncs_register

        ncs_register.main()
        return 0

    if args.mode == "pay":
        from app import payment_bind_app

        return int(payment_bind_app.main())

    return 0


if __name__ == "__main__":
    sys.exit(main())
