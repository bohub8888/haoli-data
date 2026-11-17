#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""站点地址单一配置点（v1.67，为域名切换准备）。

开源数据仓默认用占位域名；实际站点由 HAOLI_SITE_ORIGIN 环境变量提供，
build.py 生成的 sitemap / robots / webcal 链接会全部跟着变。

    HAOLI_SITE_ORIGIN=https://www.example.com ../.venv/bin/python build.py

注意：Android App 内置的数据地址在 haoli-apk 的 SiteConfig.kt，
线上热更新地址的切换需要一次 App 发版（见《数据更新与发版流程》）。
"""

from __future__ import annotations

import os

# 站点根地址（无尾斜杠）。保留旧环境变量 SITE 作为兼容入口。
SITE_ORIGIN = (
    os.environ.get("HAOLI_SITE_ORIGIN")
    or os.environ.get("SITE")
    or "https://haoli.example.com"
).rstrip("/")

# GitHub 镜像仓与 jsDelivr CDN：热更新的备用源，与 App 的 BACKUP 对齐。
MIRROR_REPO = os.environ.get("HAOLI_MIRROR_REPO", "OWNER/REPO")
CDN_BASE = f"https://cdn.jsdelivr.net/gh/{MIRROR_REPO}@main"
