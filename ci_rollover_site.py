#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跨年专用：把已发布站点的「年份窗口」文案改成当前数据窗口。

为什么需要它：
  · 每年 1 月 1 日窗口从「2026–2027」变成「2027（+2028 视公告而定）」，
    数据由 build.py 重建，但站点 HTML 里的标题 / 描述 / 月视图 `var YEARS`
    是写死的字符串，不在 CI 生成范围内（index.html 在数据仓被 gitignore）。
  · 这里只做**最小字符串手术**，不整份覆盖 index.html —— 站点页面的手工内容
    （备案号、指南内链、文案）一个字都不动。

流程：
  1. 读 data/build-state.json 的 versions，得到本次构建的年份列表；
  2. 从 haoli-site 仓取 index.html（GitHub API，HAOLI_SITE_TOKEN）；
  3. 把 `20XX–20XX` 形式的窗口文案统一换成新窗口（单年时只留一个年份）；
     把 `var YEARS = [..]` 换成新年份数组；
  4. PUT 回 haoli-site；写本地 index.html 并复用 ci_publish 推服务器 + 刷 CDN。

只在 GitHub Actions 里跑（需要 HAOLI_SITE_TOKEN、HAOLI_DEPLOY_KEY_FILE）。
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import urllib.request

import ci_publish

HERE = os.path.dirname(os.path.abspath(__file__))
SITE_REPO = "bohub8888/haoli-site"
API = f"https://api.github.com/repos/{SITE_REPO}"


def tok() -> str:
    t = os.environ.get("HAOLI_SITE_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not t:
        print("! 缺少 HAOLI_SITE_TOKEN", file=sys.stderr)
        sys.exit(1)
    return t


def api(path: str, method: str = "GET", body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{API}/{path}", data=data, method=method,
        headers={"Authorization": f"Bearer {tok()}",
                 "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json",
                 "User-Agent": "haoli-ci"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def built_years() -> list[int]:
    """已发布的数据窗口：以 data/manifest.json 为准（build-state 不进仓库）。"""
    m = json.load(open(os.path.join(HERE, "data", "manifest.json"), encoding="utf-8"))
    ys = set()
    for key in (m.get("files") or {}):
        hit = re.match(r"all-(\d{4})$", key)
        if hit:
            ys.add(int(hit.group(1)))
    return sorted(ys)


def patch_html(html: str, years: list[int]) -> tuple[str, int]:
    """只换「窗口」这一种年份对：从 `var YEARS = [...]` 反推旧窗口，
    精确替换该字符串。绝不碰「内置 2026–2030 五年数据」这类无关区间。
    """
    m = re.search(r"var YEARS = \[([^\]]*)\];", html)
    old_years = [int(x) for x in re.findall(r"20\d{2}", m.group(1))] if m else []
    new_range = "–".join(str(y) for y in years)
    n = 0
    if old_years:
        old_range = "–".join(str(y) for y in old_years)
        n += html.count(old_range)
        html = html.replace(old_range, new_range)
        # 兼容历史写法（ASCII 连字符）
        old_ascii = "-".join(str(y) for y in old_years)
        if old_ascii != old_range:
            n += html.count(old_ascii)
            html = html.replace(old_ascii, new_range)
    arr = ", ".join(str(y) for y in years)
    html, k = re.subn(r"var YEARS = \[[^\]]*\];", f"var YEARS = [{arr}];", html)
    n += k
    return html, n


def main() -> int:
    years = built_years()
    if not years:
        print("! build-state.json 没有年份，跳过")
        return 0
    path = "index.html"
    info = api(f"contents/{path}?ref=main")
    html = base64.b64decode(info["content"]).decode("utf-8")
    patched, n = patch_html(html, years)
    if patched == html:
        print("  站点年份窗口已是最新，无需改动")
        return 0
    api(f"contents/{path}", "PUT", {
        "message": f"跨年：站点年份窗口 → {'–'.join(map(str, years))}",
        "content": base64.b64encode(patched.encode()).decode(),
        "sha": info["sha"],
        "branch": "main",
    })
    print(f"  ✓ haoli-site/index.html 已更新（{n} 处年份文案）")
    # 同步服务器 + CDN（index.html 的 CDN 是 301，purge 后不强校验）
    local = os.path.join(HERE, path)
    with open(local, "w", encoding="utf-8") as f:
        f.write(patched)
    ok = ci_publish.push_to_server([path])
    print(f"  服务器同步：{'OK' if ok else '跳过/失败'}")
    try:
        urllib.request.urlopen(
            f"https://purge.jsdelivr.net/gh/{SITE_REPO}@main/{path}", timeout=30).read()
    except Exception as e:  # 刷 CDN 失败不影响主流程
        print(f"  ! CDN purge 失败：{e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
