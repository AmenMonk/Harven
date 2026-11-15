# -*- coding: utf-8 -*-
"""哈文版本号唯一户口（★ 2026-10-07 方案B：版本号单一来源）

全系统（/health、/api/version/check、SPA 关于页、打包器、云端清单）
一律从这里取版本号。发新版只改这一个数字。
打包器 pack_release.py 会校验本文件与云端 version.json 一致。
"""
APP_VERSION = '0.3.2'
