#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""新节日候选通知器：把「发现但未入库」的候选推给 Bo，且只推一次。

与 notify_ingest.py 的分工：
  · notify_ingest.py —— 真入库（会议定档 / 放假安排）后通知，按 commit diff 提取条目。
  · notify_discovery.py —— 候选发现后通知，按候选报告提取条目，用基线去重。

设计红线：
  · 候选报告是 CI 本地产物、不入库；**去重基线** data/discovery-notified.json 入库。
  · 只有「首次出现的候选」才推送；同一候选次日仍在报告里也不重复打扰。
  · 只在通知**发送成功**后才写基线；发送失败不写，下次跑会重试。
  · 通道沿用 notify_ingest.send()：邮件 → Server酱 → PushPlus，任一成功即止。
  · 令牌只从环境变量读；未配置 / 网络失败一律静默退出 0，不阻断发现主流程。
  · 候选不等于入库：文案必须写明「需人工核源，机器不直接改源表」。

用法：
  python notify_discovery.py                 # 正常：读报告，推新候选
  python notify_discovery.py --dry-run       # 只打印将要推送的文案
  python notify_discovery.py --test          # 发一条测试消息，验证通道（手动触发用）
  python notify_discovery.py --self-test     # 内置样例验证解析 / 去重逻辑，不发信
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

from notify_ingest import send

HERE = os.path.dirname(os.path.abspath(__file__))
REPORT = os.path.join(HERE, "data", "discovered-holidays-report.json")
STATE = os.path.join(HERE, "data", "discovery-notified.json")


def candidate_key(c: dict) -> str:
    """候选唯一键：中文名（没译名用英文名）+ 月日。与报告里的去重口径一致。"""
    name = (c.get("name_zh") or c.get("name_en") or "").strip()
    return f"{name}|{c.get('mmdd', '').strip()}"


def load_state(path: str = STATE) -> set[str]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return set(data.get("notified", []))
    except (OSError, ValueError):
        return set()


def load_report(path: str = REPORT) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        print(f"[discovery-notify] 候选报告读取失败：{e}，跳过")
        return {}


def select_new(report: dict, notified: set[str]) -> list[dict]:
    out, seen = [], set()
    for c in report.get("candidates", []):
        k = candidate_key(c)
        if k in notified or k in seen:
            continue
        seen.add(k)
        out.append(c)
    return out


def _line(c: dict) -> str:
    name = c.get("name_zh") or c.get("name_en") or "（未命名）"
    head = f"- **{name}** · {c.get('mmdd', '')} · {c.get('status', '候选')}"
    bits = []
    if c.get("channel"):
        bits.append(f"来源通道：{c['channel']}")
    if c.get("source"):
        bits.append(c["source"])
    if c.get("resolution"):
        bits.append(f"决议号：{c['resolution']}")
    if c.get("url"):
        bits.append(f"链接：{c['url']}")
    detail = "；".join(bits)
    return head + (f"\n  {detail}" if detail else "")


def build_message(new: list[dict]) -> tuple[str, str]:
    n = len(new)
    title = f"蚝历 · 发现 {n} 个新节日候选"
    needs_name = sum(1 for c in new if c.get("status") == "需人工译名")
    body = (
        f"今天扫描发现 **{n} 条尚未入库的节日候选**（需人工核源，机器不直接改源表）。\n"
    )
    if needs_name:
        body += f"其中 {needs_name} 条需要你给中文通行名。\n"
    body += "\n" + "\n".join(_line(c) for c in new)
    body += (
        "\n\n---\n"
        "处理入口：haoli-data 仓 → data/discovered-holidays-report.json；"
        "确认后按 curated / observances 闸门入库（固定日期 + 中文通行名 + 可点开的设立方页面）。\n"
        "本通知由节日发现任务自动发出，同一候选只通知一次。"
    )
    return title, body


def save_state(new: list[dict], path: str = STATE) -> None:
    """只在推送成功后调用：把本次候选写进去重基线。"""
    notified = sorted(load_state(path) | {candidate_key(c) for c in new})
    payload = {
        "notified": notified,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"[discovery-notify] 去重基线已更新（累计 {len(notified)} 条候选）")


def write_github_output(new: list[dict]) -> None:
    """告诉 CI 基线有变化、需要提交；本地运行无此环境变量时静默跳过。"""
    path = os.environ.get("GITHUB_OUTPUT", "")
    if not path or not new:
        return
    keys = json.dumps([candidate_key(c) for c in new], ensure_ascii=False)
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"notified_count={len(new)}\n")
        f.write(f"notified_keys={keys}\n")


def self_test() -> int:
    sample = {
        "candidates": [
            {"name_zh": "世界测试日", "name_en": "World Test Day", "mmdd": "05-20",
             "status": "待核源入库", "channel": "UN官方列表",
             "resolution": "A/RES/99/1", "url": "https://example.org/t",
             "source": "联合国官方国际日/周列表（A/RES 决议）"},
            {"name_zh": "", "name_en": "World Unknown Day", "mmdd": "08-08",
             "status": "需人工译名", "channel": "UN官方列表", "url": "https://example.org/u"},
            {"name_zh": "世界测试日", "name_en": "World Test Day", "mmdd": "05-20",
             "status": "待核源入库"},  # 同键重复，应被合并
        ]
    }
    notified = {candidate_key(sample["candidates"][0])}
    new = select_new(sample, notified)
    assert len(new) == 1, new
    assert candidate_key(new[0]) == "World Unknown Day|08-08"
    title, body = build_message(new)
    assert "1 个新节日候选" in title and "需要你给中文通行名" in body
    assert "World Unknown Day" in body and "https://example.org/u" in body
    print("[self-test] OK 候选解析 / 去重 / 文案均正确（未发信）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--test", action="store_true", help="发一条测试消息验证通道")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    if args.test:
        title = "蚝历 · 通知通道测试"
        body = (
            "这是一条来自「节日自动发现」任务的测试通知。\n\n"
            f"发送时间：{datetime.now().astimezone().isoformat(timespec='seconds')}\n"
            "收到即表示邮件 / PushPlus 通道已接通；真正发现新候选时会只通知一次。"
        )
        if args.dry_run:
            print(f"=== 标题 ===\n{title}\n=== 正文 ===\n{body}")
            return 0
        send(title, body)
        return 0

    report = load_report()
    if not report:
        return 0
    new = select_new(report, load_state())
    if not new:
        print(f"[discovery-notify] 无新候选（报告 {report.get('candidate_count', 0)} 条，均已通知过），跳过")
        return 0

    title, body = build_message(new)
    if args.dry_run:
        print(f"=== 标题 ===\n{title}\n=== 正文 ===\n{body}")
        return 0

    if send(title, body):
        save_state(new)
        write_github_output(new)
    else:
        print("[discovery-notify] 所有通道推送失败，未写去重基线；下次运行会重试")
    return 0


if __name__ == "__main__":
    sys.exit(main())
