#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""入库通知器：真有数据入库后，把「入库了什么」推送到微信（PushPlus）。

设计红线：
  · 只在 CI 判定 changed=true、完成提交后调用；无变化 / 静默放弃时根本不会跑到本脚本。
  · 推送令牌只从环境变量 PUSHPLUS_TOKEN 读，绝不写进仓库。
  · 未配置令牌 / 网络失败一律静默退出 0，绝不阻断入库主流程、绝不刷屏。
  · 内容从本次 commit 的 diff 里提取真实新增条目，不编造、不概括。

用法：
  python notify_ingest.py --kind conference        # 会议定档入库（conferences.py）
  python notify_ingest.py --kind holiday           # 放假安排入库（cn_official.py）
  python notify_ingest.py --kind conference --dry-run   # 只打印将要推送的文案，不发送
  python notify_ingest.py --self-test              # 用内置样例验证解析逻辑
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PUSH_URL = "http://www.pushplus.plus/send"

# 每种入库链路对应的源表文件与条目解析规则
SPEC = {
    "conference": {
        "file": "conferences.py",
        "label": "会议定档",
        # +    C("2026-05-01", "2026-05-03", "某某会议",
        "patterns": [
            re.compile(r'^\+\s*C\("(\d{4}-\d{2}-\d{2})",\s*"(\d{4}-\d{2}-\d{2})",\s*"([^"]+)"'),
        ],
    },
    "holiday": {
        "file": "cn_official.py",
        "label": "放假安排",
        # +        (D(2027, 1, 1), D(2027, 1, 3), "元旦假期", "..."),
        "patterns": [
            re.compile(
                r'^\+\s*\(D\((\d{4}),\s*(\d+),\s*(\d+)\),\s*'
                r'D\((\d{4}),\s*(\d+),\s*(\d+)\),\s*"([^"]+)"'
            ),
        ],
    },
}


def git(*args: str) -> str:
    r = subprocess.run(["git", *args], cwd=HERE, text=True, capture_output=True)
    return r.stdout or ""


def changed_diff(kind: str, ref: str) -> str:
    """取指定提交里、该入库链路源表的改动 diff。"""
    path = SPEC[kind]["file"]
    if ref == "WORKTREE":
        return git("diff", "--", path)
    return git("show", "--format=", "--unified=0", ref, "--", path)


def parse_entries(kind: str, diff: str) -> list[str]:
    """从 diff 的新增行里提取真实入库条目，人话化。"""
    rows: list[str] = []
    for line in diff.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        for pat in SPEC[kind]["patterns"]:
            m = pat.match(line)
            if not m:
                continue
            g = m.groups()
            if kind == "conference":
                start, end, name = g[0], g[1], g[2]
                span = f"{start}" if start == end else f"{start} ~ {end}"
                rows.append(f"- **{name}**：{span}")
            else:  # holiday
                y1, m1, d1, y2, m2, d2, name = g
                span = (
                    f"{y1}年{m1}月{d1}日"
                    if (y1, m1, d1) == (y2, m2, d2)
                    else f"{y1}年{m1}月{d1}日—{m2}月{d2}日"
                )
                rows.append(f"- **{name}**：{span}")
            break
    # 去重保序
    seen: set[str] = set()
    out = []
    for r in rows:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def build_message(kind: str, entries: list[str], commit_subject: str) -> tuple[str, str]:
    label = SPEC[kind]["label"]
    title = f"蚝历 · {label}已入库"
    date = git("log", "-1", "--format=%ad", "--date=short").strip()
    body = (
        f"新数据已自动核对并入主库，全端（网页 / App / 日历订阅）已同步。\n\n"
        f"**本次入库 {len(entries)} 条：**\n"
        + "\n".join(entries)
        + f"\n\n---\n提交：{commit_subject}\n日期：{date}"
    )
    return title, body


def send(title: str, body: str) -> bool:
    token = os.environ.get("PUSHPLUS_TOKEN", "").strip()
    if not token:
        print("[notify] 未配置 PUSHPLUS_TOKEN，跳过推送（不影响入库）")
        return False
    payload = json.dumps(
        {"token": token, "title": title, "content": body, "template": "markdown"}
    ).encode("utf-8")
    req = urllib.request.Request(
        PUSH_URL, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            res = json.loads(r.read().decode("utf-8"))
        ok = res.get("code") == 200
        print(f"[notify] 推送结果：{res}")
        return ok
    except Exception as e:  # 网络抖动不阻断入库
        print(f"[notify] 推送失败（已忽略，不影响入库）：{e}")
        return False


def self_test() -> int:
    conf = (
        '+++ b/conferences.py\n'
        '@@ -1,0 +2,4 @@\n'
        '+    C("2026-05-01", "2026-05-03", "测试会议A",\n'
        '+      "world", "北京",\n'
        '+    C("2026-06-10", "2026-06-10", "测试会议B",\n'
    )
    holi = (
        '+++ b/cn_official.py\n'
        '@@ -1,0 +2,2 @@\n'
        '+        (D(2027, 1, 1), D(2027, 1, 3), "元旦假期", "放假3天。"),\n'
        '+        (D(2027, 2, 5), D(2027, 2, 5), "春节假期", "放假1天。"),\n'
    )
    c = parse_entries("conference", conf)
    h = parse_entries("holiday", holi)
    assert c == ["- **测试会议A**：2026-05-01 ~ 2026-05-03",
                 "- **测试会议B**：2026-06-10"], c
    assert h == ["- **元旦假期**：2027年1月1日—1月3日",
                 "- **春节假期**：2027年2月5日"], h
    print("[self-test] OK 会议/放假条目解析均正确")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=list(SPEC))
    ap.add_argument("--ref", default="HEAD", help="提交引用；WORKTREE 表示工作区 diff")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    diff = changed_diff(args.kind, args.ref)
    entries = parse_entries(args.kind, diff)
    if not entries:
        print(f"[notify] {args.kind} 本次 diff 无新增条目，跳过推送")
        return 0

    subject = (
        git("log", "-1", "--format=%s", args.ref).strip()
        if args.ref != "WORKTREE" else "工作区改动"
    )
    title, body = build_message(args.kind, entries, subject)

    if args.dry_run:
        print(f"=== 标题 ===\n{title}\n=== 正文 ===\n{body}")
        return 0

    send(title, body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
