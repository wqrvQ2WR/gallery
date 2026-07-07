#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gallery — 이미지 폴더를 넣으면 전시(갤러리) HTML을 만들어주는 CLI.

사용법:
  gallery <폴더> [옵션]

옵션:
  --ui                 브라우저에서 프리셋을 미리보고 고르는 웹 UI 실행
  -p, --preset <이름>  프리셋 선택 (기본: dark, 목록은 --presets)
  --presets            프리셋 목록 보기
  -t, --title <제목>   전시 제목 (기본: 폴더 이름)
  -o, --out <파일>     출력 HTML 경로 (기본: <폴더>/gallery.html, 있으면 gallery_2.html …)
  -r, --recursive      하위 폴더까지 스캔
  --min-wide <px>      풀스크린 전시로 걸 최소 가로 픽셀 (기본 900)
  --port <n>           웹 UI 포트 (기본: 8756부터 빈 포트 탐색)
  --open               만든 뒤(또는 UI를) 브라우저로 열기
  help, 도움말, -h     이 도움말
"""

import json
import mimetypes
import os
import re
import struct
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse, parse_qs, quote

IMG_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg', '.avif'}
HASH_RE = re.compile(r'-[A-Za-z0-9_-]{8}$')  # vite 등 빌드 해시 접미사
SUB_RE = re.compile(r'_(w|m|w2|m2)$', re.I)
SUB_NAMES = {'w': 'WIDE', 'm': 'MOBILE', 'w2': 'WIDE 2', 'm2': 'MOBILE 2'}


# ── 이미지 크기 읽기 (표준 라이브러리만) ──────────────────────────

def _jpeg_size(f):
    f.seek(2)
    while True:
        b = f.read(2)
        if len(b) < 2 or b[0] != 0xFF:
            return None
        m = b[1]
        while m == 0xFF:
            nb = f.read(1)
            if not nb:
                return None
            m = nb[0]
        if m == 0xD8 or m == 0x01 or 0xD0 <= m <= 0xD9:
            continue
        seg = f.read(2)
        if len(seg) < 2:
            return None
        length = struct.unpack('>H', seg)[0]
        if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
            data = f.read(5)
            if len(data) < 5:
                return None
            h, w = struct.unpack('>HH', data[1:5])
            return w, h
        f.seek(length - 2, 1)


def _webp_size(f):
    f.seek(12)
    chunk = f.read(4)
    f.seek(4, 1)  # chunk size
    if chunk == b'VP8X':
        f.seek(4, 1)  # flags + reserved
        d = f.read(6)
        w = 1 + (d[0] | d[1] << 8 | d[2] << 16)
        h = 1 + (d[3] | d[4] << 8 | d[5] << 16)
        return w, h
    if chunk == b'VP8 ':
        d = f.read(10)
        if d[3:6] != b'\x9d\x01\x2a':
            return None
        w = struct.unpack('<H', d[6:8])[0] & 0x3FFF
        h = struct.unpack('<H', d[8:10])[0] & 0x3FFF
        return w, h
    if chunk == b'VP8L':
        d = f.read(5)
        if d[0] != 0x2F:
            return None
        bits = d[1] | d[2] << 8 | d[3] << 16 | d[4] << 24
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1


def read_size(path):
    """(width, height) 또는 None. png/gif/jpeg/webp/bmp 지원."""
    try:
        with open(path, 'rb') as f:
            head = f.read(30)
            if head[:8] == b'\x89PNG\r\n\x1a\n':
                return struct.unpack('>II', head[16:24])
            if head[:6] in (b'GIF87a', b'GIF89a'):
                return struct.unpack('<HH', head[6:10])
            if head[:2] == b'\xff\xd8':
                return _jpeg_size(f)
            if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
                return _webp_size(f)
            if head[:2] == b'BM' and len(head) >= 26:
                w, h = struct.unpack('<ii', head[18:26])
                return w, abs(h)
    except Exception:
        pass
    return None


# ── 파일명 → 라벨 ────────────────────────────────────────────

def parse_name(filename):
    """빌드 해시를 떼고 (그룹라벨, 서브태그)를 뽑는다."""
    stem = os.path.splitext(filename)[0]
    stem = HASH_RE.sub('', stem)
    sub = ''
    m = SUB_RE.search(stem)
    if m:
        sub = SUB_NAMES[m.group(1).lower()]
        stem = stem[:m.start()]
    label = re.sub(r'[_\-]+', ' ', stem).strip().upper() or stem.upper()
    return label, sub


# ── 수집 & 분류 ──────────────────────────────────────────────

def collect(folder, recursive):
    items = []
    if recursive:
        walker = os.walk(folder)
    else:
        walker = [(folder, [], sorted(os.listdir(folder)))]
    for root, dirs, files in walker:
        dirs.sort()
        for name in sorted(files):
            if name.startswith('.'):
                continue
            if os.path.splitext(name)[1].lower() not in IMG_EXTS:
                continue
            path = os.path.join(root, name)
            size = read_size(path)
            label, sub = parse_name(name)
            items.append({
                'path': path, 'label': label, 'sub': sub,
                'w': size[0] if size else 0, 'h': size[1] if size else 0,
                'bytes': os.path.getsize(path),
                'gif': name.lower().endswith('.gif'),
            })
    return items


def classify(items, min_wide):
    """special(GIF) / full(대형 전시) / grid(그리드) 로 나눈다."""
    groups = {}
    for it in items:
        groups.setdefault(it['label'], []).append(it)

    specials, fulls, grid = [], [], []
    for it in items:
        if it['gif']:
            specials.append(it)
        elif len(groups[it['label']]) >= 3 or it['w'] < min_wide:
            grid.append(it)
        else:
            fulls.append(it)
    return specials, fulls, grid


def pick_hero(items):
    """가로가 긴 것 중 파일 용량이 가장 큰 이미지(디테일 많은 그림일 확률이 높다)."""
    landscape = [i for i in items if i['w'] > i['h'] and not i['gif']]
    pool = landscape or [i for i in items if not i['gif']] or items
    return max(pool, key=lambda i: i['bytes'])


# ── 프리셋 ───────────────────────────────────────────────────

PRESETS = {
    'dark': {
        'label': '다크 전시',
        'desc': '어두운 미술관. 은은한 스크롤 리빌과 그라데이션 타이틀.',
        'fonts': 'Noto+Sans+KR:wght@200;300;400;500&family=Inter:wght@200;300;400',
        'vars': {
            '--bg': '#0a0a0a', '--fg': '#d4d0ca', '--muted': '#555', '--faint': '#333',
            '--line': '#1a1a1a', '--cell-bg': '#111', '--label-fg': '#999',
            '--overlay': 'rgba(0,0,0,.88)', '--hover-bg': 'rgba(255,255,255,.04)',
            '--hero-filter': 'saturate(.6) brightness(.5)',
            '--hero-grad': 'linear-gradient(180deg,#e8e4de 40%,#666)',
            '--font-body': "'Noto Sans KR','Inter',sans-serif",
            '--font-display': "'Noto Sans KR','Inter',sans-serif",
        },
        'extra': '',
    },
    'white': {
        'label': '화이트 큐브',
        'desc': '밝은 화이트 갤러리. 세리프 타이포와 흰 매트 액자.',
        'fonts': 'Noto+Serif+KR:wght@300;500;700&family=Noto+Sans+KR:wght@300;400',
        'vars': {
            '--bg': '#f4f2ee', '--fg': '#211d18', '--muted': '#8a8378', '--faint': '#b5aea2',
            '--line': '#d8d2c6', '--cell-bg': '#fff', '--label-fg': '#7a736a',
            '--overlay': 'rgba(244,242,238,.94)', '--hover-bg': 'rgba(0,0,0,.05)',
            '--hero-filter': 'saturate(.85) brightness(.72)',
            '--hero-grad': 'linear-gradient(180deg,#fff 40%,#cfc9bd)',
            '--font-body': "'Noto Sans KR',sans-serif",
            '--font-display': "'Noto Serif KR',serif",
        },
        'extra': '''
.hero .title-overlay h2{text-shadow:0 4px 40px rgba(0,0,0,.3)}
.exhibit.full{padding:70px 48px;min-height:auto}
.exhibit.full .artwork .frame{background:#fff;padding:22px;box-shadow:0 24px 60px rgba(30,22,8,.14)}
.exhibit.full .artwork .frame img{max-height:76vh}
.exhibit.full .caption{position:static;padding:18px 4px 0}
.exhibit.full .caption .title{font-family:var(--font-display);font-size:18px}
.carousel{gap:24px;padding:0 24px}
.carousel .cell{aspect-ratio:auto;background:#fff;padding:12px;box-shadow:0 10px 28px rgba(30,22,8,.10)}
.carousel .cell img{aspect-ratio:16/11}
.carousel .cell .c-label{position:static;opacity:1;background:none;padding:10px 2px 0}
.carousel .cell .c-label span{font-family:var(--font-display);font-style:italic}
''',
    },
    'polaroid': {
        'label': '폴라로이드',
        'desc': '크림색 스크랩북. 삐뚤빼뚤 붙인 폴라로이드와 손글씨 캡션.',
        'fonts': 'Nanum+Pen+Script&family=Noto+Sans+KR:wght@300;400',
        'vars': {
            '--bg': '#ece5d4', '--fg': '#4a4132', '--muted': '#8d8168', '--faint': '#b3a789',
            '--line': '#d6cbb2', '--cell-bg': '#fdfdf8', '--label-fg': '#5a4f3f',
            '--overlay': 'rgba(236,229,212,.95)', '--hover-bg': 'rgba(0,0,0,.05)',
            '--hero-filter': 'sepia(.25) saturate(.8) brightness(.66)',
            '--hero-grad': 'linear-gradient(180deg,#fffbe9 40%,#c9ba93)',
            '--font-body': "'Noto Sans KR',sans-serif",
            '--font-display': "'Nanum Pen Script',cursive",
        },
        'extra': '''
.hero .title-overlay h2{font-size:clamp(64px,14vw,170px);letter-spacing:0;text-shadow:0 4px 40px rgba(0,0,0,.35)}
.exhibit.full{padding:70px 8vw;min-height:auto}
.exhibit.full .artwork .frame{background:#fdfdf8;padding:16px 16px 60px;box-shadow:0 18px 40px rgba(70,52,20,.22)}
.exhibit.full .artwork .frame img{max-height:74vh}
.exhibit.full .caption{position:absolute;bottom:14px;left:0;right:0;text-align:center}
.exhibit.full .caption .num{display:none}
.exhibit.full .caption .title{font-family:var(--font-display);font-size:26px;color:#5a4f3f}
.carousel{gap:34px;padding:0 34px}
.carousel .cell{aspect-ratio:auto;background:#fdfdf8;padding:12px 12px 44px;box-shadow:0 14px 30px rgba(70,52,20,.20)}
.carousel .cell img{aspect-ratio:1/1}
.carousel .cell .c-label{position:absolute;top:auto;bottom:4px;left:0;right:0;opacity:1;background:none;text-align:center;padding:0}
.carousel .cell .c-label span{font-family:var(--font-display);font-size:19px;letter-spacing:0}
.carousel .cell.visible:nth-child(4n+1){transform:rotate(-1.6deg)}
.carousel .cell.visible:nth-child(4n+2){transform:rotate(1.2deg)}
.carousel .cell.visible:nth-child(4n+3){transform:rotate(-.7deg)}
.carousel .cell.visible:nth-child(4n){transform:rotate(1.8deg)}
.carousel .cell:hover{z-index:5}
''',
    },
    'neon': {
        'label': '네온 아케이드',
        'desc': '심야 아케이드. 시안·마젠타 네온 글로우.',
        'fonts': 'Orbitron:wght@500;700&family=Noto+Sans+KR:wght@300;400',
        'vars': {
            '--bg': '#050510', '--fg': '#d8f6ff', '--muted': '#3e6b78', '--faint': '#23414b',
            '--line': '#101827', '--cell-bg': '#0a0f1e', '--label-fg': '#7fdbe8',
            '--overlay': 'rgba(3,5,16,.9)', '--hover-bg': 'rgba(0,255,255,.06)',
            '--hero-filter': 'saturate(1.25) brightness(.45) contrast(1.1)',
            '--hero-grad': 'linear-gradient(180deg,#eafcff 40%,#59c2d6)',
            '--font-body': "'Noto Sans KR',sans-serif",
            '--font-display': "'Orbitron','Noto Sans KR',sans-serif",
        },
        'extra': '''
.hero .title-overlay h2{-webkit-text-fill-color:#eafcff;background:none;letter-spacing:6px;
  text-shadow:0 0 16px rgba(0,255,255,.8),0 0 60px rgba(0,255,255,.35),0 0 120px rgba(255,0,255,.25)}
.hero .title-overlay p{color:#ff7ae8;text-shadow:0 0 12px rgba(255,0,255,.6)}
.exhibit.full .artwork .frame{border:1px solid rgba(0,255,255,.16);box-shadow:0 0 46px rgba(255,0,255,.10)}
.exhibit.full .caption .title{color:#7fdbe8;text-shadow:0 0 10px rgba(0,255,255,.5);font-family:var(--font-display);font-size:13px;letter-spacing:2px}
.carousel{gap:10px;padding:0 10px}
.carousel .cell{border:1px solid rgba(0,255,255,.16)}
.carousel .cell:hover{border-color:rgba(0,255,255,.55);box-shadow:0 0 24px rgba(0,255,255,.28)}
.carousel .cell .c-label span{font-family:var(--font-display);font-size:9px;letter-spacing:2px}
''',
    },
    'film': {
        'label': '필름 시트',
        'desc': '콘택트 시트. 필름 스트립 구멍과 모노스페이스 라벨.',
        'fonts': 'IBM+Plex+Mono:wght@300;400&family=Noto+Sans+KR:wght@300',
        'vars': {
            '--bg': '#0d0c0a', '--fg': '#cfc9bd', '--muted': '#6b6355', '--faint': '#403a30',
            '--line': '#221e18', '--cell-bg': '#000', '--label-fg': '#c9c2b4',
            '--overlay': 'rgba(8,8,6,.93)', '--hover-bg': 'rgba(255,255,255,.05)',
            '--hero-filter': 'grayscale(.35) sepia(.22) brightness(.55)',
            '--hero-grad': 'linear-gradient(180deg,#efe9dc 40%,#7a7263)',
            '--font-body': "'IBM Plex Mono','Noto Sans KR',monospace",
            '--font-display': "'IBM Plex Mono',monospace",
        },
        'extra': '''
.hero .title-overlay h2{letter-spacing:2px}
.hero .title-overlay p{letter-spacing:12px}
.exhibit.full .artwork .frame{padding:16px 0;background:#000}
.exhibit.full .artwork .frame::before,.exhibit.full .artwork .frame::after{content:'';position:absolute;left:0;right:0;height:12px;
  background:radial-gradient(circle at 8px 6px,#cfc9bd 2.6px,transparent 3.1px) 0 0/18px 12px repeat-x}
.exhibit.full .artwork .frame::before{top:2px}
.exhibit.full .artwork .frame::after{bottom:2px}
.exhibit.full .caption .title{font-size:12px;letter-spacing:2px;text-transform:uppercase}
.carousel{gap:0 14px;padding:0 14px;row-gap:14px}
.carousel .cell{aspect-ratio:3/2;padding:16px 0;background:#000}
.carousel .cell::before,.carousel .cell::after{content:'';position:absolute;left:0;right:0;height:11px;z-index:2;
  background:radial-gradient(circle at 7px 5.5px,#cfc9bd 2.3px,transparent 2.8px) 0 0/16px 11px repeat-x}
.carousel .cell::before{top:2px}
.carousel .cell::after{bottom:2px}
.carousel .cell img{filter:saturate(.85) contrast(1.05)}
.carousel .cell .c-label span{font-size:9px;letter-spacing:2px;text-transform:uppercase}
''',
    },
    'magazine': {
        'label': '매거진',
        'desc': '에디토리얼 매거진. 큼직한 세리프 헤드라인과 빨간 포인트.',
        'fonts': 'Noto+Serif+KR:wght@400;700;900&family=Inter:wght@300;400',
        'vars': {
            '--bg': '#ffffff', '--fg': '#111', '--muted': '#777', '--faint': '#bbb',
            '--line': '#111', '--cell-bg': '#fff', '--label-fg': '#111',
            '--overlay': 'rgba(255,255,255,.96)', '--hover-bg': 'rgba(0,0,0,.05)',
            '--hero-filter': 'saturate(.9) brightness(.62)',
            '--hero-grad': 'linear-gradient(180deg,#fff 40%,#ddd)',
            '--font-body': "'Inter','Noto Sans KR',sans-serif",
            '--font-display': "'Noto Serif KR',serif",
        },
        'extra': '''
.hero .title-overlay h2{-webkit-text-fill-color:#fff;background:none;font-weight:900;letter-spacing:-2px;text-shadow:0 6px 60px rgba(0,0,0,.4)}
.hero .title-overlay p{color:#eee;letter-spacing:10px}
.divider{color:#111}
.divider::before,.divider::after{background:#111;height:2px}
.section-title{color:#111;font-weight:700;border-top:4px solid #111;margin:100px 48px 0;padding:14px 0 28px;letter-spacing:4px}
.exhibit.full{padding:80px 48px;min-height:auto}
.exhibit.full .caption{position:static;padding:14px 0 0;border-top:3px solid #111;margin-top:16px}
.exhibit.full .caption .num{color:#d0021b;font-weight:700}
.exhibit.full .caption .title{font-family:var(--font-display);font-size:22px;font-weight:700}
.carousel{gap:40px 24px;padding:0 48px 20px}
.carousel .cell{aspect-ratio:auto;background:none}
.carousel .cell img{aspect-ratio:4/3}
.carousel .cell .c-label{position:static;opacity:1;background:none;padding:10px 0 0;border-top:1px solid #111;margin-top:10px}
.carousel .cell .c-label span{font-size:10px;letter-spacing:2px;font-weight:400}
footer{color:#999}
@media(max-width:768px){.section-title{margin:60px 20px 0}}
''',
    },
}

DEFAULT_PRESET = 'dark'


# ── HTML 생성 ────────────────────────────────────────────────

TEMPLATE = r'''<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__TITLE__ — 전시</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=__FONTS__&display=swap');
:root{__VARS__}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--fg);font-family:var(--font-body);font-weight:300;-webkit-font-smoothing:antialiased;overflow-x:hidden}
#preloader{position:fixed;inset:0;z-index:99999;background:var(--bg);display:flex;align-items:center;justify-content:center;transition:opacity .8s ease}
#preloader.hide{opacity:0;pointer-events:none}
#preloader .spinner{width:20px;height:20px;border:1px solid var(--line);border-top-color:var(--fg);border-radius:50%;animation:spin 1s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
header{position:fixed;top:0;left:0;width:100%;z-index:100;padding:24px 48px;display:flex;justify-content:space-between;align-items:center;mix-blend-mode:difference}
header .logo{font-size:11px;font-weight:400;letter-spacing:3px;color:#fff}
header .logo em{font-style:normal;opacity:.4}
header .info{font-size:10px;color:#888;letter-spacing:2px}
.hero{width:100%;height:100vh;display:flex;align-items:center;justify-content:center;position:relative;overflow:hidden}
.hero::after{content:'';position:absolute;inset:0;background:radial-gradient(ellipse at center,transparent 20%,var(--bg) 88%);pointer-events:none}
.hero img{width:100%;height:100%;object-fit:cover;filter:var(--hero-filter)}
.hero .title-overlay{position:absolute;z-index:10;text-align:center}
.hero .title-overlay h2{font-size:clamp(48px,12vw,140px);font-weight:200;letter-spacing:-3px;font-family:var(--font-display);background:var(--hero-grad);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.hero .title-overlay p{margin-top:12px;font-size:11px;color:var(--muted);letter-spacing:8px}
.hero .scroll-hint{position:absolute;bottom:36px;left:50%;transform:translateX(-50%);font-size:9px;color:var(--faint);letter-spacing:4px;animation:breath 2.4s ease infinite;z-index:10}
@keyframes breath{0%,100%{opacity:.25}50%{opacity:.8}}
.exhibit.full{min-height:100vh;padding:0;display:flex;flex-direction:column;justify-content:center;align-items:stretch;position:relative}
.exhibit.full .artwork{opacity:0;transform:scale(.96);transition:all 1.2s cubic-bezier(.25,.46,.45,.94)}
.exhibit.full .artwork.visible{opacity:1;transform:scale(1)}
.exhibit.full .artwork .frame{position:relative;display:block;width:100%}
.exhibit.full .artwork .frame img{width:100%;max-height:85vh;object-fit:cover;display:block;transition:transform .6s ease}
.exhibit.full .artwork .frame:hover img{transform:scale(1.01)}
.exhibit.full .artwork .frame .glint{position:absolute;inset:0;background:linear-gradient(135deg,rgba(255,255,255,.03) 0%,transparent 50%);pointer-events:none}
.exhibit.full .caption{position:absolute;bottom:48px;left:48px;z-index:10;opacity:0;transform:translateY(20px);transition:all .8s ease .4s}
.exhibit.full .caption.visible{opacity:1;transform:translateY(0)}
.exhibit.full .caption .num{font-size:10px;color:var(--muted);letter-spacing:2px;margin-bottom:6px}
.exhibit.full .caption .title{font-size:16px;font-weight:300;color:var(--fg);letter-spacing:1px}
.divider{padding:60px 48px;text-align:center;font-size:9px;color:var(--line);letter-spacing:6px;position:relative}
.divider::before,.divider::after{content:'';position:absolute;top:50%;width:30%;height:1px;background:var(--line)}
.divider::before{left:48px}
.divider::after{right:48px}
.section-title{padding:120px 48px 24px;font-size:10px;color:var(--faint);letter-spacing:6px;font-weight:400}
.carousel{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:2px;padding:0 2px;background:var(--bg)}
.carousel .cell{position:relative;overflow:hidden;cursor:pointer;aspect-ratio:16/10;background:var(--cell-bg);opacity:0;transform:translateY(20px);transition:all .6s cubic-bezier(.25,.46,.45,.94)}
.carousel .cell.visible{opacity:1;transform:translateY(0)}
.carousel .cell img{width:100%;height:100%;object-fit:cover;display:block;transition:transform .6s ease,filter .6s ease}
.carousel .cell:hover img{transform:scale(1.04);filter:brightness(1.06)}
.carousel .cell .c-label{position:absolute;bottom:0;left:0;right:0;padding:48px 16px 12px;background:linear-gradient(transparent,rgba(0,0,0,.7));opacity:0;transition:opacity .4s ease}
.carousel .cell:hover .c-label{opacity:1}
.carousel .cell .c-label span{font-size:10px;color:var(--label-fg);letter-spacing:1px}
.lightbox{position:fixed;inset:0;z-index:9999;background:var(--overlay);backdrop-filter:blur(24px);-webkit-backdrop-filter:blur(24px);display:flex;align-items:center;justify-content:center;opacity:0;pointer-events:none;transition:opacity .5s ease}
.lightbox.active{opacity:1;pointer-events:auto}
.lightbox .lb-frame{position:relative;max-width:90vw}
.lightbox .lb-frame img{max-width:90vw;max-height:88vh;object-fit:contain;border-radius:2px;box-shadow:0 24px 80px rgba(0,0,0,.4);transform:scale(.94);transition:transform .5s cubic-bezier(.25,.46,.45,.94)}
.lightbox.active .lb-frame img{transform:scale(1)}
.lightbox .lb-frame .lb-meta{text-align:center;margin-top:16px;font-size:10px;color:var(--muted);letter-spacing:2px}
.lightbox .lb-close{position:absolute;top:24px;right:32px;width:36px;height:36px;display:flex;align-items:center;justify-content:center;color:var(--muted);font-size:16px;cursor:pointer;background:none;border:none;border-radius:50%;transition:all .3s}
.lightbox .lb-close:hover{color:var(--fg);background:var(--hover-bg)}
.lightbox .lb-nav{position:absolute;top:50%;transform:translateY(-50%);width:44px;height:44px;display:flex;align-items:center;justify-content:center;background:none;border:none;color:var(--muted);font-size:18px;cursor:pointer;border-radius:50%;transition:all .3s}
.lightbox .lb-nav:hover{color:var(--fg);background:var(--hover-bg)}
.lightbox .lb-nav.prev{left:16px}
.lightbox .lb-nav.next{right:16px}
footer{padding:120px 48px 40px;text-align:center;font-size:9px;color:var(--faint);letter-spacing:4px}
@media(max-width:768px){
  header{padding:16px 20px}
  .divider{padding:40px 20px}
  .divider::before,.divider::after{width:20%}
  .divider::before{left:20px}
  .divider::after{right:20px}
  .section-title{padding:80px 20px 16px}
  .carousel{grid-template-columns:repeat(auto-fill,minmax(160px,1fr))}
  .exhibit.full .caption{bottom:24px;left:20px}
  .lightbox .lb-nav{width:36px;height:36px;font-size:14px}
  .lightbox .lb-nav.prev{left:4px}
  .lightbox .lb-nav.next{right:4px}
  .lightbox .lb-close{top:12px;right:16px}
}
__EXTRA__
</style>
</head>
<body>

<div id="preloader"><div class="spinner"></div></div>

<header>
  <span class="logo">__TITLE__ <em>· 전시</em></span>
  <span class="info" id="workCount"></span>
</header>

<div class="hero">
  <img src="__HERO__" alt="">
  <div class="title-overlay">
    <h2>__TITLE__</h2>
    <p>__SUBTITLE__</p>
  </div>
  <div class="scroll-hint">↓ SCROLL</div>
</div>

<div id="fullExhibits"></div>

<div class="section-title" id="gridTitle" style="display:none">COLLECTION</div>
<div class="carousel" id="gridCarousel"></div>

<footer>__TITLE__ · __YEAR__</footer>

<div class="lightbox" id="lightbox">
  <button class="lb-close" id="lbClose">✕</button>
  <button class="lb-nav prev" id="lbPrev">‹</button>
  <button class="lb-nav next" id="lbNext">›</button>
  <div class="lb-frame" id="lbFrame"></div>
</div>

<script>
window.addEventListener('load',()=>{document.getElementById('preloader').classList.add('hide')});

const fullItems=__FULL_ITEMS__;
const gridItems=__GRID_ITEMS__;
const allItems=[...fullItems,...gridItems];
let ci=0;
document.getElementById('workCount').textContent=allItems.length+' works';

function esc(s){const d=document.createElement('div');d.textContent=s;return d.innerHTML}

const fullWrap=document.getElementById('fullExhibits');
fullItems.forEach((item,i)=>{
  if(i>0&&i%4===0){const dv=document.createElement('div');dv.className='divider';dv.textContent='·  ·  ·';fullWrap.appendChild(dv)}
  const div=document.createElement('div');
  div.className='exhibit full';
  div.addEventListener('click',()=>openLB(i));
  div.innerHTML=`<div class="artwork"><div class="frame"><img src="${item.file}" alt="${esc(item.label)}" loading="lazy"><div class="glint"></div></div></div>
  <div class="caption"><div class="num">${item.num}</div><div class="title">${esc(item.label)}${item.sub?' · '+esc(item.sub):''}</div></div>`;
  fullWrap.appendChild(div);
});

if(gridItems.length){
  document.getElementById('gridTitle').style.display='block';
  const c=document.getElementById('gridCarousel');
  gridItems.forEach((item,i)=>{
    const cell=document.createElement('div');
    cell.className='cell';
    cell.innerHTML=`<img src="${item.file}" alt="${esc(item.label)}" loading="lazy"><div class="c-label"><span>${esc(item.label)}${item.sub?' '+esc(item.sub):''}</span></div>`;
    cell.addEventListener('click',()=>openLB(fullItems.length+i));
    c.appendChild(cell);
  });
}

const observer=new IntersectionObserver(entries=>{
  entries.forEach(e=>{
    if(!e.isIntersecting)return;
    const artwork=e.target.querySelector('.artwork');
    const caption=e.target.querySelector('.caption');
    if(artwork)artwork.classList.add('visible');
    if(caption)setTimeout(()=>caption.classList.add('visible'),200);
    e.target.classList.add('visible');
  });
},{threshold:0.15,rootMargin:'0px 0px -40px 0px'});
document.querySelectorAll('.exhibit.full').forEach(el=>observer.observe(el));
document.querySelectorAll('.carousel .cell').forEach((el,i)=>{el.style.transitionDelay=(i%6)*60+'ms';observer.observe(el)});

function openLB(idx){ci=idx;updateLB();document.getElementById('lightbox').classList.add('active');document.body.style.overflow='hidden'}
function updateLB(){
  const item=allItems[ci];
  document.getElementById('lbFrame').innerHTML=
    `<img src="${item.file}" alt="${esc(item.label)}"><div class="lb-meta">${esc(item.label)}${item.sub?' · '+esc(item.sub):''} · ${ci+1}/${allItems.length}</div>`;
}
function closeLB(){document.getElementById('lightbox').classList.remove('active');document.body.style.overflow=''}
document.getElementById('lbClose').addEventListener('click',closeLB);
document.getElementById('lbPrev').addEventListener('click',()=>{ci=(ci-1+allItems.length)%allItems.length;updateLB()});
document.getElementById('lbNext').addEventListener('click',()=>{ci=(ci+1)%allItems.length;updateLB()});
document.getElementById('lightbox').addEventListener('click',e=>{if(e.target===e.currentTarget)closeLB()});
document.addEventListener('keydown',e=>{
  if(!document.getElementById('lightbox').classList.contains('active'))return;
  if(e.key==='Escape')closeLB();
  if(e.key==='ArrowLeft'){ci=(ci-1+allItems.length)%allItems.length;updateLB()}
  if(e.key==='ArrowRight'){ci=(ci+1)%allItems.length;updateLB()}
});
</script>
</body>
</html>
'''


def render(preset_key, title, hero, specials, fulls, grid, src_of):
    """프리셋을 적용한 전시 HTML 문자열을 만든다."""
    import datetime
    p = PRESETS[preset_key]
    ordered_fulls = specials + fulls
    full_arr = []
    for i, it in enumerate(ordered_fulls):
        prefix = 'MOTION' if it['gif'] else 'WORK'
        full_arr.append({'file': src_of(it['path']), 'label': it['label'], 'sub': it['sub'],
                         'num': '%s %02d' % (prefix, i + 1)})
    grid_arr = [{'file': src_of(it['path']), 'label': it['label'], 'sub': it['sub']}
                for it in grid]

    subtitle = ' · '.join(re.sub(r'[^A-Za-z0-9 ]', '', title).upper().split())
    subtitle = (subtitle + ' · EXHIBITION') if subtitle else 'EXHIBITION'
    css_vars = ';'.join('%s:%s' % (k, v) for k, v in p['vars'].items())
    return (TEMPLATE
            .replace('__FONTS__', p['fonts'])
            .replace('__VARS__', css_vars)
            .replace('__EXTRA__', p['extra'])
            .replace('__TITLE__', title.replace('<', '&lt;'))
            .replace('__SUBTITLE__', subtitle)
            .replace('__HERO__', src_of(hero['path']).replace('"', '&quot;'))
            .replace('__YEAR__', str(datetime.date.today().year))
            .replace('__FULL_ITEMS__', json.dumps(full_arr, ensure_ascii=False).replace('</', '<\\/'))
            .replace('__GRID_ITEMS__', json.dumps(grid_arr, ensure_ascii=False).replace('</', '<\\/')))


def generate(folder, items, preset_key, title, out_path, min_wide):
    specials, fulls, grid = classify(items, min_wide)
    hero = pick_hero(items)
    out_dir = os.path.dirname(os.path.abspath(out_path))
    src_of = lambda p: os.path.relpath(p, out_dir).replace(os.sep, '/')
    html = render(preset_key, title, hero, specials, fulls, grid, src_of)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)
    return len(specials), len(fulls), len(grid)


# ── 웹 UI ────────────────────────────────────────────────────

UI_HTML = r'''<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>gallery — 프리셋 고르기</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;700&display=swap');
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0a0a0a;color:#d4d0ca;font-family:'Noto Sans KR',sans-serif;font-weight:300}
.top{padding:28px 40px 8px}
.top h1{font-size:15px;font-weight:500;letter-spacing:3px}
.top h1 em{font-style:normal;opacity:.4;font-weight:300}
.top .path{margin-top:6px;font-size:11px;color:#555;letter-spacing:.5px}
.controls{display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end;padding:18px 40px 6px}
.field{display:flex;flex-direction:column;gap:6px}
.field label{font-size:10px;color:#666;letter-spacing:2px}
.field input{background:#141414;border:1px solid #262626;color:#d4d0ca;padding:9px 12px;font-size:13px;border-radius:4px;font-family:inherit;outline:none;width:220px}
.field input:focus{border-color:#4a4a4a}
.field.small input{width:110px}
button.go{background:#d4d0ca;color:#0a0a0a;border:none;padding:10px 26px;font-size:13px;font-weight:500;letter-spacing:1px;border-radius:4px;cursor:pointer;font-family:inherit}
button.go:hover{background:#fff}
button.go:disabled{opacity:.4;cursor:wait}
#msg{padding:6px 40px;font-size:12px;color:#8fae8b;min-height:24px}
#msg code{color:#d4d0ca;background:#161616;padding:2px 6px;border-radius:3px;font-size:11px}
#msg a{color:#9ecbff}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:20px;padding:14px 40px 60px}
.card{position:relative;border:1px solid #222;border-radius:8px;overflow:hidden;cursor:pointer;background:#101010;transition:border-color .25s,transform .25s}
.card:hover{border-color:#3a3a3a;transform:translateY(-2px)}
.card.sel{border-color:#d4d0ca}
.card input{position:absolute;opacity:0;pointer-events:none}
.pv{position:relative;width:100%;overflow:hidden;background:#000}
.pv iframe{width:1280px;height:800px;border:0;transform-origin:0 0;pointer-events:none;display:block}
.meta{display:flex;justify-content:space-between;align-items:center;padding:12px 16px}
.meta .name{font-size:13px;font-weight:500;letter-spacing:1px}
.meta .name small{display:block;margin-top:3px;font-size:11px;color:#666;font-weight:300;letter-spacing:.3px}
.meta a{font-size:11px;color:#777;text-decoration:none;letter-spacing:1px;white-space:nowrap}
.meta a:hover{color:#d4d0ca}
.card .badge{position:absolute;top:10px;left:10px;z-index:5;font-size:9px;letter-spacing:2px;background:rgba(212,208,202,.92);color:#0a0a0a;padding:3px 8px;border-radius:3px;opacity:0;transition:opacity .2s}
.card.sel .badge{opacity:1}
</style>
</head>
<body>
<div class="top">
  <h1>GALLERY <em>· 프리셋 고르기</em></h1>
  <div class="path">__FOLDER__ · __COUNT__ works</div>
</div>
<div class="controls">
  <div class="field"><label>제목</label><input id="i_title" value="__TITLE__"></div>
  <div class="field"><label>출력 파일명</label><input id="i_out" value="gallery.html"></div>
  <div class="field small"><label>MIN-WIDE(px)</label><input id="i_mw" type="number" value="900"></div>
  <button class="go" id="btnGo" onclick="gen()">생성하기</button>
</div>
<div id="msg"></div>
<div class="grid">__CARDS__</div>
<script>
function fit(){document.querySelectorAll('.pv').forEach(w=>{const f=w.querySelector('iframe');const s=w.clientWidth/1280;f.style.transform='scale('+s+')';w.style.height=(800*s)+'px'})}
addEventListener('resize',fit);fit();
document.querySelectorAll('.card').forEach(c=>{
  c.addEventListener('click',e=>{
    if(e.target.closest('a'))return;
    document.querySelectorAll('.card').forEach(x=>x.classList.remove('sel'));
    c.classList.add('sel');c.querySelector('input').checked=true;
  });
});
async function gen(){
  const sel=document.querySelector('input[name=preset]:checked');
  const msg=document.getElementById('msg'),btn=document.getElementById('btnGo');
  if(!sel){msg.style.color='#c98b8b';msg.textContent='프리셋을 먼저 골라주세요.';return}
  btn.disabled=true;msg.style.color='#8fae8b';msg.textContent='생성 중…';
  try{
    const r=await fetch('/generate',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({preset:sel.value,title:document.getElementById('i_title').value,
        out:document.getElementById('i_out').value,min_wide:+document.getElementById('i_mw').value||900})});
    const d=await r.json();
    if(d.ok){msg.innerHTML='완성! <code>'+d.path+'</code> &nbsp;<a href="'+d.url+'" target="_blank">브라우저로 보기 ↗</a>'}
    else{msg.style.color='#c98b8b';msg.textContent=d.error||'실패'}
  }catch(e){msg.style.color='#c98b8b';msg.textContent='요청 실패: '+e}
  btn.disabled=false;
}
</script>
</body>
</html>
'''

CARD_HTML = '''<label class="card{sel}">
  <input type="radio" name="preset" value="{key}"{checked}>
  <span class="badge">선택됨</span>
  <div class="pv"><iframe src="/preview/{key}" loading="lazy" scrolling="no"></iframe></div>
  <div class="meta">
    <div class="name">{label}<small>{desc}</small></div>
    <a href="/preview/{key}?full=1" target="_blank">전체 미리보기 ↗</a>
  </div>
</label>'''


def make_handler(folder, items, title):
    folder_abs = os.path.abspath(folder)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype='text/html; charset=utf-8'):
            data = body if isinstance(body, bytes) else body.encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            parsed = urlparse(self.path)
            path = unquote(parsed.path)

            if path == '/':
                cards = ''
                for i, (key, p) in enumerate(PRESETS.items()):
                    cards += CARD_HTML.format(
                        key=key, label=p['label'], desc=p['desc'],
                        sel=' sel' if i == 0 else '', checked=' checked' if i == 0 else '')
                html = (UI_HTML
                        .replace('__FOLDER__', folder_abs.replace('<', '&lt;'))
                        .replace('__COUNT__', str(len(items)))
                        .replace('__TITLE__', title.replace('"', '&quot;').replace('<', '&lt;'))
                        .replace('__CARDS__', cards))
                return self._send(200, html)

            if path.startswith('/preview/'):
                key = path[len('/preview/'):]
                if key not in PRESETS:
                    return self._send(404, '없는 프리셋: %s' % key)
                full = 'full' in parse_qs(parsed.query)
                specials, fulls, grid = classify(items, 900)
                if not full:  # 카드 미리보기는 가볍게 일부만
                    specials, fulls, grid = specials[:2], fulls[:5], grid[:12]
                hero = pick_hero(items)
                src_of = lambda p: '/f/' + quote(os.path.relpath(p, folder_abs).replace(os.sep, '/'))
                return self._send(200, render(key, title, hero, specials, fulls, grid, src_of))

            if path.startswith('/f/'):
                rel = path[len('/f/'):]
                target = os.path.normpath(os.path.join(folder_abs, rel))
                if not target.startswith(folder_abs + os.sep):
                    return self._send(403, '안 됨')
                if not os.path.isfile(target):
                    return self._send(404, '없는 파일')
                ctype = mimetypes.guess_type(target)[0] or 'application/octet-stream'
                with open(target, 'rb') as f:
                    return self._send(200, f.read(), ctype)

            return self._send(404, '없는 경로')

        def do_POST(self):
            if urlparse(self.path).path != '/generate':
                return self._send(404, '{}', 'application/json')
            try:
                length = int(self.headers.get('Content-Length', 0))
                req = json.loads(self.rfile.read(length) or b'{}')
                key = req.get('preset', DEFAULT_PRESET)
                if key not in PRESETS:
                    raise ValueError('없는 프리셋: %s' % key)
                t = (req.get('title') or title).strip() or title
                out_name = os.path.basename((req.get('out') or 'gallery.html').strip()) or 'gallery.html'
                if not out_name.endswith('.html'):
                    out_name += '.html'
                min_wide = int(req.get('min_wide') or 900)
                out_path = next_free(os.path.join(folder_abs, out_name))
                generate(folder_abs, items, key, t, out_path, min_wide)
                body = {'ok': True, 'path': out_path,
                        'url': '/f/' + quote(os.path.basename(out_path))}
                print('생성: %s (%s)' % (out_path, PRESETS[key]['label']))
            except Exception as e:
                body = {'ok': False, 'error': str(e)}
            return self._send(200, json.dumps(body, ensure_ascii=False), 'application/json')

    return Handler


def run_ui(folder, items, title, port, open_browser):
    handler = make_handler(folder, items, title)
    last_err = None
    for p in ([port] if port else range(8756, 8800)):
        try:
            server = ThreadingHTTPServer(('127.0.0.1', p), handler)
            port = p
            break
        except OSError as e:
            last_err = e
    else:
        print('포트를 못 잡았어요: %s' % last_err)
        return 1
    url = 'http://localhost:%d/' % port
    print('웹 UI 열림: %s  (끄려면 Ctrl+C)' % url)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n종료')
    return 0


# ── CLI ──────────────────────────────────────────────────────

def next_free(path):
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    n = 2
    while os.path.exists('%s_%d%s' % (base, n, ext)):
        n += 1
    return '%s_%d%s' % (base, n, ext)


def list_presets():
    print('프리셋 목록:')
    for key, p in PRESETS.items():
        mark = ' (기본)' if key == DEFAULT_PRESET else ''
        print('  %-10s %s%s — %s' % (key, p['label'], mark, p['desc']))


def main(argv):
    if not argv or argv[0] in ('help', '도움말', '-h', '--help'):
        print(__doc__.strip())
        return 0

    folder = None
    title = out = None
    preset = DEFAULT_PRESET
    recursive = ui = open_after = False
    min_wide = 900
    port = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ('-t', '--title'):
            i += 1; title = argv[i]
        elif a in ('-o', '--out'):
            i += 1; out = argv[i]
        elif a in ('-p', '--preset'):
            i += 1; preset = argv[i]
        elif a == '--presets':
            list_presets(); return 0
        elif a == '--ui':
            ui = True
        elif a == '--port':
            i += 1; port = int(argv[i])
        elif a in ('-r', '--recursive'):
            recursive = True
        elif a == '--min-wide':
            i += 1; min_wide = int(argv[i])
        elif a == '--open':
            open_after = True
        elif folder is None:
            folder = a
        else:
            print('모르는 옵션: %s (gallery help 참고)' % a)
            return 1
        i += 1

    if not folder:
        print('폴더를 알려주세요. 예: gallery ~/Desktop/이미지들 --ui')
        return 1
    folder = os.path.abspath(os.path.expanduser(folder))
    if not os.path.isdir(folder):
        print('폴더가 없어요: %s' % folder)
        return 1
    if preset not in PRESETS:
        print('없는 프리셋: %s' % preset)
        list_presets()
        return 1

    items = collect(folder, recursive)
    if not items:
        print('이미지가 하나도 없어요: %s' % folder)
        return 1

    title = title or os.path.basename(folder)

    if ui:
        return run_ui(folder, items, title, port, open_after)

    out = os.path.abspath(os.path.expanduser(out)) if out \
        else next_free(os.path.join(folder, 'gallery.html'))
    ns, nf, ng = generate(folder, items, preset, title, out, min_wide)
    print('완성! %s' % out)
    print('  프리셋 %s(%s) · 작품 %d점 = 모션 %d + 대형 전시 %d + 그리드 %d'
          % (preset, PRESETS[preset]['label'], len(items), ns, nf, ng))
    if open_after:
        webbrowser.open('file://' + out)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
