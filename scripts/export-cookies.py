#!/usr/bin/env python3
"""从本机浏览器导出头条 cookie，供服务器上的纯 HTTP 客户端注入。

## 为什么需要它

服务器上没有 Chrome（`chromium.load("chrome", ...)` 直接抛 `CookieError`），
而 Playwright 自己的 profile 解不开（`Local State` 里 `os_crypt` 是空 dict）。
所以服务器上必须**注入**：本机导出 → 服务器加载。

## 用法

    python3 scripts/export-cookies.py --browser chrome --out .newsagg/toutiao-cookies.json

然后把文件传到服务器（**用 scp，不要用 git**——这是账号凭证）：

    scp .newsagg/toutiao-cookies.json root@<ip>:/root/.newsagg/toutiao-cookies.json
    ssh root@<ip> 'chmod 600 /root/.newsagg/toutiao-cookies.json'

服务器上加载时用 `toutiao_api.Client.from_cookie_file(path)`，
或 `python3 .agents/skills/newsagg-publish/scripts/toutiao_api.py
--cookie-file <path> list`。

## 有效期

实测（2026-10-27）导出 25 条：主体 cookie（`sessionid`/`uid_tt`/`sso_uid_tt`/
`sid_ucp_v1` 等 18 条）到 **2026-11-19**，约 23 天；`ttwid` 到 2027-10-25、
`odin_tt` 到 2027-10-15、`sid_guard` 到 2027-09-15。

→ **每月重新导出一次**。这是阶段 1 必须新增的运维项。

## 安全

- 输出文件默认 `0600`，且拒绝写到组/其他可读的既有文件旁边。
- **不打印任何 cookie 值**——只打印条数、域、有效期摘要。
- 输出文件已在 `.gitignore` 里（`.newsagg/` 整个目录被忽略）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LIB_DIR = REPO_ROOT / ".agents" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from browserless.chromium import CookieError, load  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="导出头条 cookie 供服务器注入")
    ap.add_argument("--browser", default="chrome",
                    help="从哪个浏览器读（chrome/chromium/edge/brave）")
    ap.add_argument("--out", required=True, help="输出 JSON 路径")
    ap.add_argument("--domains", default="toutiao.com",
                    help="逗号分隔的域；只要这些域（含子域）的 cookie")
    args = ap.parse_args(argv)

    domains = [d.strip() for d in args.domains.split(",") if d.strip()]
    try:
        jar = load(args.browser, domains=domains)
    except CookieError as exc:
        print(f"读不到 {args.browser} 的 cookie：{exc}", file=sys.stderr)
        print("→ 在浏览器里登录一次 mp.toutiao.com，然后重试。", file=sys.stderr)
        return 1

    cookies = [c.to_dict() for c in jar.cookies]
    if not cookies:
        print(f"{args.browser} 里没有 {domains} 的 cookie。", file=sys.stderr)
        print("→ 在浏览器里登录一次 mp.toutiao.com，然后重试。", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    now_ts = datetime.now(timezone.utc).timestamp()
    dated = [(c["name"], c.get("expires_at") or 0) for c in cookies if c.get("expires_at")]
    payload = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "browser": args.browser,
        "domains": domains,
        "count": len(cookies),
        # 顶层也记一份最早/最晚过期时间：服务器上的简报要读这个，
        # 不该去逐条遍历 cookies 数组（ADR-0071 残余风险 69）。
        "earliest_expires_at": int(min((e for _, e in dated), default=0)),
        "latest_expires_at": int(max((e for _, e in dated), default=0)),
        "session_cookie_count": sum(1 for c in cookies if not c.get("expires_at")),
        "cookies": cookies,
    }
    # 先写再 chmod：这样中途失败不会留下一个 0644 的半成品。
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(out, 0o600)

    # 只报条数与域，绝不输出值。
    print(f"已导出 {len(cookies)} 条 cookie → {out}")
    print(f"权限 {oct(out.stat().st_mode & 0o777)}")
    print("域：" + "、".join(sorted({c["domain"] for c in cookies})))

    # ── 有效期摘要（ADR-0071 残余风险 69）──────────────────────────
    #
    # 这份文件会被 scp 到服务器、存上几周然后没人再看它。如果它在到期后
    # 才被发现过期，症状是「某天起纯 HTTP 全部 401，而浏览器后端还好」——
    # 那个现象看起来像代码坏了，不像凭证过期。所以有效期必须**写进文件**
    # 并且**在导出时就报出来**。
    now = datetime.now(timezone.utc).timestamp()
    dated = [(c["name"], c["expires_at"]) for c in cookies if c.get("expires_at")]
    sessions = [c for c in cookies if not c.get("expires_at")]
    if dated:
        soonest = min(exp for _, exp in dated)
        days = (soonest - now) / 86400
        latest = max(exp for _, exp in dated)
        print(f"最早到期：{datetime.fromtimestamp(soonest, timezone.utc):%Y-%m-%d}"
              f"（约 {days:.0f} 天后）｜最晚到期：{datetime.fromtimestamp(latest, timezone.utc):%Y-%m-%d}")
    if sessions:
        print(f"注意：{len(sessions)} 条是会话 cookie（无过期时间），浏览器退出后可能失效："
              + "、".join(c["name"] for c in sessions))
    if dated and soonest <= now:
        print("⚠️ 已经有 cookie 过期了——先在浏览器里重新登录 mp.toutiao.com 再导出。",
              file=sys.stderr)
        return 1
    if dated and days < 7:
        print(f"⚠️ 最早的 cookie 只剩 {days:.0f} 天——尽快重新导出并替换服务器上的旧文件。",
              file=sys.stderr)

    print("提醒：这是账号凭证，用 scp 传，不要进 git。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
