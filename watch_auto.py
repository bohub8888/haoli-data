#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""会议日期全自动核对入库（云端 CI 运行；零用户参与）。

设计红线（用户 2026-11-19 定档）
--------------------------------
  · 用户判断：重要会议日期一旦官方确定即稳定，官网发布的就可信 → 自动采纳，不设人工闸门。
  · 机器只读「官网结构化数据 JSON-LD Event.startDate/endDate」这一机器可读信号，
    不读新闻正文、不做语义猜测——中文网页通用抽取在反爬/代理环境下不可靠，硬读会误入库。
  · 拿不准就静默放弃：抓取失败 / 无 JSON-LD Event / 日期歧义 / 闸门不过 → 一律不动。
  · 命中 = 官网挂出「比正式池更新的一年」的确切日期 → 自动生成 ENTRY，从 WATCHLIST 移除，
    重建 → 全量校验 → 写信号 → 交给 CI 分发。全端一致，不发 APK。

为什么不读新闻搜索 / 官网可见文本（实测 6 轮结论，2026-11-19）
----------------------------------------------------------------
  官网首页 = 历史日期与导航噪音；Bing/Google News = 中文分词错乱或代理拦截；
  新华社/政府网 = 只有政策文件库。均无法稳定读到确切会期。唯 JSON-LD 是主办方
  自己写的机器可读结构化日期，可信且无歧义，故只认这一种。覆盖不到的中国会议
  继续留 WATCHLIST，维持现状（不误入库优先于覆盖率）。

用法
----
    python watch_auto.py               # 在线核对 + 自动更新
    python watch_auto.py --dry-run     # 只核对解析，不改任何文件
    python watch_auto.py --offline     # 用缓存（调试解析器）
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
CONF = os.path.join(HERE, "conferences.py")
CACHE = os.path.join(HERE, "data", "conference-watch-cache")
REPORT = os.path.join(HERE, "data", "conference-watch-report.json")
SIG = os.path.join(HERE, ".auto_update_changed")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120"
WINDOW_DAYS = 240          # 解析日期须落在 date_hint 前后这个窗口内
AUTO_MARK = "自动核源"      # 本脚本入库条目的 note 标记，用于改期复检与防重

# 只有这些官网确认有稳定的 JSON-LD Event 日期（2026-11-19 实测），其余一律不碰。
# key = WATCHLIST 中的精确会议名；value = 抓取 URL + 生成条目的 tag。
STRUCTURED_SOURCES: dict[str, dict] = {
    # 只列 WATCHLIST 里「待定档」且官网有结构化日期的会议；名字须与 WATCHLIST 精确一致。
    "Web Summit":   {"url": "https://websummit.com/", "tag": "会议", "scope": "world", "place": "葡萄牙里斯本"},
    "GITEX Global": {"url": "https://www.gitex.com/", "tag": "展会", "scope": "world", "place": "阿联酋迪拜"},
    "世界能源大会":   {"url": "https://www.worldenergy.org/", "tag": "会议", "scope": "world", "place": "主办地待官网公布"},
}


def log(msg: str) -> None:
    print(f"[watch_auto] {msg}", flush=True)


# ---------------------------------------------------------------- 抓取

def fetch(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def cache_path(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "_", name)
    return os.path.join(CACHE, safe + ".html")


def get_page(name: str, url: str, offline: bool = False) -> tuple[str, str]:
    """返回 (正文, 来源说明)。在线成功则刷新缓存；失败回落缓存；无缓存返回空。"""
    cp = cache_path(name)
    if offline:
        return (open(cp, encoding="utf-8", errors="replace").read(), "缓存（离线）") if os.path.exists(cp) else ("", "无缓存（离线）")
    try:
        raw = fetch(url)
        os.makedirs(CACHE, exist_ok=True)
        with open(cp, "w", encoding="utf-8") as f:
            f.write(raw)
        return raw, "官网在线"
    except Exception as e:
        if os.path.exists(cp):
            log(f"  ! {name} 抓取失败({type(e).__name__})，回落缓存")
            return open(cp, encoding="utf-8", errors="replace").read(), "缓存（抓取失败）"
        return "", f"抓取失败({type(e).__name__})，无缓存"


# ---------------------------------------------------------------- 解析（纯函数，可单测）

LD_BLOCK = re.compile(r'application/ld\+json[^>]*>(.*?)</script>', re.S | re.I)
DATE_FIELD = re.compile(r'"(startDate|endDate)"\s*:\s*"([^"]+)"')
TYPE_EVENT = re.compile(r'"@type"\s*:\s*"Event"', re.I)


def _parse_date(value: str) -> dt.date | None:
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", value.strip())
    if not m:
        return None
    try:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def extract_event_dates(html: str) -> tuple[dt.date | None, dt.date | None]:
    """取出 JSON-LD 里「最新年份」那组 Event 的 (start, end)。

    多组日期（如 Interpack 同时有 2026 与 2021）按年份取最新，避免过期届次。
    同一年份出现多个不同开幕日 → 歧义，放弃。
    """
    candidates: list[tuple[int, dt.date | None, dt.date | None]] = []
    for block in LD_BLOCK.findall(html):
        if not TYPE_EVENT.search(block):
            continue
        fields = DATE_FIELD.findall(block)
        if not fields:
            continue
        d = {k: _parse_date(v) for k, v in fields}
        start, end = d.get("startDate"), d.get("endDate")
        if start or end:
            candidates.append((start.year if start else (end.year if end else 0), start, end))
    if not candidates:
        return None, None
    top_year = max(c[0] for c in candidates)
    same = [c for c in candidates if c[0] == top_year]
    starts = {c[1] for c in same if c[1]}
    if len(starts) > 1:
        log(f"  ! 同一年 {top_year} 出现 {len(starts)} 个不同开幕日 → 歧义放弃")
        return None, None
    start = next(iter(starts)) if starts else None
    ends = {c[2] for c in same if c[2]}
    end = sorted(ends)[-1] if ends else None
    return start, end


def gate(start: dt.date | None, end: dt.date | None, hint: str) -> tuple[bool, str]:
    """强闸门：日期合法 / 落在预期窗口 / 跨度合理。"""
    if not start:
        return False, "无确切开幕日"
    h = dt.date.fromisoformat(hint)
    if abs((start - h).days) > WINDOW_DAYS:
        return False, f"开幕日 {start} 距预期 {h} 超过 {WINDOW_DAYS} 天"
    if end:
        if end < start:
            return False, f"结束日 {end} 早于开幕 {start}"
        if (end - start).days > 30:
            return False, f"会期跨度 {(end - start).days} 天异常"
    return True, "通过"


# ---------------------------------------------------------------- 源码改写（精确、幂等）

def watch_hint(src: str, name: str) -> str | None:
    m = re.search(r'W\("(\d{4}-\d{2}-\d{2})",\s*"' + re.escape(name) + r'"', src)
    return m.group(1) if m else None


def entry_years(src: str, name: str) -> set[int]:
    """正式池里同名条目已有的年份集合。"""
    years = set()
    for m in re.finditer(r'C\("(\d{4})-\d{2}-\d{2}",\s*"\d{4}-\d{2}-\d{2}",\s*"' + re.escape(name) + r'"', src):
        years.add(int(m.group(1)))
    return years


def make_entry_line(name: str, start: dt.date, end: dt.date | None, cfg: dict) -> str:
    d0 = start.isoformat()
    d1 = (end or start).isoformat()
    place = cfg["place"]
    where = f"在{place}" if place and "待" not in place and "公布" not in place else ""
    if end and end > start:
        pub = f"{name}于 {start.month} 月 {start.day} 日至 {end.day} 日{where}举行。"
    else:
        pub = f"{name}于 {start.month} 月 {start.day} 日{where}举行。"
    note = f"S9 行业旗舰；官网结构化日期，{AUTO_MARK}自动入库。"
    return (
        f'    C("{d0}", "{d1}", "{name}",\n'
        f'      "{cfg["scope"]}", "{cfg["place"]}",\n'
        f'      "{name}官网·结构化事件数据（JSON-LD startDate）",\n'
        f'      "{cfg["url"]}", "A",\n'
        f'      "{pub}",\n'
        f'      "{note}", tag="{cfg["tag"]}"),'
    )


def apply_entry(src: str, name: str, line: str) -> str:
    """追加 ENTRY 到 ENTRIES 末尾，并从 WATCHLIST 删除该条（仅删精确匹配名）。"""
    # 1) 删 WATCHLIST 条目：从 W("date", "name" 起到其后第一个 ),
    wl = re.compile(r'\n[ \t]*W\("\d{4}-\d{2}-\d{2}",\s*"' + re.escape(name) + r'".*?\),(?=\n)', re.S)
    src2, n = wl.subn("", src, count=1)
    if n != 1:
        raise RuntimeError(f"WATCHLIST 中未找到唯一条目：{name}")
    # 2) 追加到 ENTRIES 末尾
    wpos = src2.find("WATCHLIST:")
    epos = src2.rfind("\n]", 0, wpos)
    if epos < 0:
        raise RuntimeError("找不到 ENTRIES 结束符")
    return src2[:epos] + "\n" + line + src2[epos:]


def update_existing(src: str, name: str, start: dt.date, end: dt.date | None) -> str:
    """改期复检：只更新带 AUTO_MARK 的自动条目。"""
    d0, d1 = start.isoformat(), (end or start).isoformat()
    pat = re.compile(
        r'C\("(\d{4}-\d{2}-\d{2})",\s*"(\d{4}-\d{2}-\d{2})",\s*"' + re.escape(name) +
        r'".*?' + AUTO_MARK + r'.*?\)', re.S)
    def repl(m):
        return re.sub(r'C\("\d{4}-\d{2}-\d{2}",\s*"\d{4}-\d{2}-\d{2}"',
                      f'C("{d0}", "{d1}"', m.group(0), count=1)
    new, n = pat.subn(repl, src, count=1)
    return new if n else src


# ---------------------------------------------------------------- 构建与校验

def run(cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, shell=True, cwd=HERE, text=True, capture_output=True, timeout=900)
    if check and r.returncode != 0:
        raise RuntimeError(f"命令失败: {cmd}\n{r.stdout}\n{r.stderr}")
    return r


def rebuild_and_verify() -> bool:
    log("重建（build.py）…")
    r = run(f'"{PY}" build.py', check=False)
    if r.returncode != 0:
        log("✗ 构建失败：" + (r.stdout or "")[-500:])
        return False
    log("全量校验…")
    for script in ("check_consistency.py", "check_key_dates.py", "check_translations.py"):
        rr = run(f'"{PY}" {script}', check=False)
        log(f"  {script}: {'OK' if rr.returncode == 0 else 'FAIL'}")
        if rr.returncode != 0:
            return False
    return True


# ---------------------------------------------------------------- 主流程

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()

    if os.path.exists(SIG):
        os.remove(SIG)

    src = load = open(CONF, encoding="utf-8").read()
    results: list[dict] = []
    new_src = src
    changed = False

    for name, cfg in STRUCTURED_SOURCES.items():
        row = {"name": name, "url": cfg["url"]}
        hint = watch_hint(new_src, name)
        row["watch_hint"] = hint

        raw, how = get_page(name, cfg["url"], offline=args.offline)
        row["source"] = how
        if not raw:
            row["status"] = "no_page"
            log(f"{name}: 抓不到页面（{how}）→ 放弃")
            results.append(row); continue
        start, end = extract_event_dates(raw)
        row["dates"] = [d.isoformat() for d in (start, end) if d]
        if not start:
            row["status"] = "no_structured_date"
            log(f"{name}: 无 JSON-LD Event 日期 → 放弃")
            results.append(row); continue
        if hint:
            ok, why = gate(start, end, hint)
            if not ok:
                row["status"] = "gate_reject"; row["why"] = why
                log(f"{name}: 闸门未过（{why}）→ 放弃")
                results.append(row); continue

        have = entry_years(new_src, name)
        if start.year in have:
            # 那年已在库：只在带 AUTO_MARK 时才尝试改期复检，人工条目一律不动
            row["status"] = "already_in_pool"
            if AUTO_MARK in new_src and f'"{name}"' in new_src:
                updated = update_existing(new_src, name, start, end)
                if updated != new_src:
                    new_src = updated; changed = True
                    row["status"] = "updated"
                    log(f"{name}: 自动条目检测到改期 → {start}~{end}")
            else:
                log(f"{name}: {start.year} 已在库（人工条目，不动）")
            results.append(row); continue

        # 新年份定档 → 生成条目 + 移出 WATCHLIST
        line = make_entry_line(name, start, end, cfg)
        row["status"] = "new_entry"
        log(f"{name}: 命中新档期 {start}~{end} → 生成 {start.year} 条目")
        if not args.dry_run:
            new_src = apply_entry(new_src, name, line)
            changed = True
        results.append(row)

    report = {
        "checked_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "policy": "只读官网 JSON-LD Event 结构化日期；拿不准静默放弃；零用户参与",
        "results": results,
        "changed": changed,
    }
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    if args.dry_run:
        log("[dry-run] 未改动任何文件")
        return 0
    if not changed:
        log("✓ 无新定档 / 无改期，未改动（静默结束）")
        return 0

    bak = CONF + ".autobak"
    shutil.copy2(CONF, bak)
    with open(CONF, "w", encoding="utf-8") as f:
        f.write(new_src)
    r = run(f'"{PY}" conferences.py', check=False)
    if r.returncode != 0:
        log("✗ conferences 闸门失败，回滚：" + (r.stdout or "")[-400:])
        shutil.move(bak, CONF); return 2
    if not rebuild_and_verify():
        log("✗ 重建/校验失败，回滚")
        shutil.move(bak, CONF); return 2
    os.remove(bak)
    with open(SIG, "w", encoding="utf-8") as f:
        f.write(dt.datetime.now().astimezone().isoformat(timespec="seconds"))
    log("✓ 会议日期已更新，全端重建完成 → 交给 CI 分发")
    return 0


if __name__ == "__main__":
    sys.exit(main())
