#!/usr/bin/env python3
"""
安全版浏览器启动脚本。

只做两件事：
1. 打开浏览器
2. 访问目标 URL（默认 https://chatgpt.com）

不会读取、解析或注入任何 token / cookie / localStorage。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path


DEFAULT_URL = "https://chatgpt.com"


def _candidate_paths() -> dict[str, list[str]]:
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
    program_files_x86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")

    return {
        "chrome": [
            shutil.which("chrome.exe") or "",
            shutil.which("chrome") or "",
            str(Path(program_files) / "Google/Chrome/Application/chrome.exe"),
            str(Path(program_files_x86) / "Google/Chrome/Application/chrome.exe"),
            str(Path(local_app_data) / "Google/Chrome/Application/chrome.exe"),
        ],
        "edge": [
            shutil.which("msedge.exe") or "",
            shutil.which("msedge") or "",
            str(Path(program_files) / "Microsoft/Edge/Application/msedge.exe"),
            str(Path(program_files_x86) / "Microsoft/Edge/Application/msedge.exe"),
            str(Path(local_app_data) / "Microsoft/Edge/Application/msedge.exe"),
        ],
    }


def find_browser_executable(browser: str) -> str | None:
    for candidate in _candidate_paths().get(browser, []):
        if candidate and Path(candidate).exists():
            return candidate
    return None


def launch_specific_browser(browser: str, url: str, private: bool) -> bool:
    executable = find_browser_executable(browser)
    if not executable:
        return False

    cmd = [executable]
    if private:
        if browser == "chrome":
            cmd.append("--incognito")
        elif browser == "edge":
            cmd.append("--inprivate")
    cmd.append(url)

    subprocess.Popen(cmd)
    print(f"[OK] 已启动 {browser}: {url}")
    return True


def launch_default_browser(url: str) -> bool:
    opened = webbrowser.open(url)
    if opened:
        print(f"[OK] 已用默认浏览器打开: {url}")
    else:
        print(f"[FAIL] 默认浏览器打开失败: {url}")
    return bool(opened)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="打开浏览器访问 ChatGPT，不注入任何认证信息。"
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help=f"要打开的地址，默认 {DEFAULT_URL}",
    )
    parser.add_argument(
        "--browser",
        choices=["auto", "default", "chrome", "edge"],
        default="auto",
        help="浏览器类型，默认 auto",
    )
    parser.add_argument(
        "--private",
        action="store_true",
        help="尝试以无痕/隐私模式启动（仅 chrome/edge 生效）",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    url = args.url.strip() or DEFAULT_URL

    if args.browser == "default":
        if args.private:
            print("[WARN] default 模式不支持强制无痕，改为普通窗口打开。")
        return 0 if launch_default_browser(url) else 1

    if args.browser in {"chrome", "edge"}:
        if launch_specific_browser(args.browser, url, args.private):
            return 0
        print(f"[WARN] 未找到 {args.browser}，回退到默认浏览器。")
        return 0 if launch_default_browser(url) else 1

    if args.private:
        for browser in ("chrome", "edge"):
            if launch_specific_browser(browser, url, private=True):
                return 0
        print("[WARN] 未找到可用的 Chrome/Edge 无痕浏览器，回退到默认浏览器普通窗口。")
        return 0 if launch_default_browser(url) else 1

    return 0 if launch_default_browser(url) else 1


if __name__ == "__main__":
    sys.exit(main())
