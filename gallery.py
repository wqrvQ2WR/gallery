#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gallery — 이미지 폴더를 넣으면 다크 전시(갤러리) HTML을 만들어주는 CLI.

사용법:
  gallery <폴더> [옵션]

옵션:
  -t, --title <제목>   전시 제목 (기본: 폴더 이름)
  -o, --out <파일>     출력 HTML 경로 (기본: <폴더>/gallery.html, 있으면 gallery_2.html …)
  -r, --recursive      하위 폴더까지 스캔
  --min-wide <px>      풀스크린 전시로 걸 최소 가로 픽셀 (기본 900)
  --open               만든 뒤 브라우저로 열기
  help, 도움말, -h     이 도움말
"""

import json
import os
import re
import struct
import sys
import webbrowser

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


# ── HTML 생성 ────────────────────────────────────────────────

TEMPLATE = r'''<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__TITLE__ — 전시</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@200;300;400;500&family=Inter:wght@200;300;400&display=swap');
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0a0a0a;color:#d4d0ca;font-family:'Noto Sans KR','Inter',sans-serif;font-weight:300;-webkit-font-smoothing:antialiased;overflow-x:hidden}
#preloader{position:fixed;inset:0;z-index:99999;background:#0a0a0a;display:flex;align-items:center;justify-content:center;transition:opacity .8s ease}
#preloader.hide{opacity:0;pointer-events:none}
#preloader .spinner{width:20px;height:20px;border:1px solid #2a2a2a;border-top-color:#d4d0ca;border-radius:50%;animation:spin 1s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
header{position:fixed;top:0;left:0;width:100%;z-index:100;padding:24px 48px;display:flex;justify-content:space-between;align-items:center;mix-blend-mode:difference}
header .logo{font-size:11px;font-weight:400;letter-spacing:3px;color:#fff}
header .logo em{font-style:normal;opacity:.4}
header .info{font-size:10px;color:#888;letter-spacing:2px}
.hero{width:100%;height:100vh;display:flex;align-items:center;justify-content:center;position:relative;overflow:hidden}
.hero::after{content:'';position:absolute;inset:0;background:radial-gradient(ellipse at center,transparent 20%,#0a0a0a 85%);pointer-events:none}
.hero img{width:100%;height:100%;object-fit:cover;filter:saturate(.6) brightness(.5)}
.hero .title-overlay{position:absolute;z-index:10;text-align:center}
.hero .title-overlay h2{font-size:clamp(48px,12vw,140px);font-weight:200;letter-spacing:-3px;background:linear-gradient(180deg,#e8e4de 40%,#666);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.hero .title-overlay p{margin-top:12px;font-size:11px;color:#555;letter-spacing:8px}
.hero .scroll-hint{position:absolute;bottom:36px;left:50%;transform:translateX(-50%);font-size:9px;color:#333;letter-spacing:4px;animation:breath 2.4s ease infinite}
@keyframes breath{0%,100%{opacity:.25}50%{opacity:.8}}
.exhibit.full{min-height:100vh;padding:0;display:flex;flex-direction:column;justify-content:center;align-items:stretch;position:relative}
.exhibit.full .artwork{opacity:0;transform:scale(.96);transition:all 1.2s cubic-bezier(.25,.46,.45,.94)}
.exhibit.full .artwork.visible{opacity:1;transform:scale(1)}
.exhibit.full .artwork .frame{position:relative;display:inline-block;width:100%}
.exhibit.full .artwork .frame img{width:100%;max-height:85vh;object-fit:cover;display:block;transition:transform .6s ease}
.exhibit.full .artwork .frame:hover img{transform:scale(1.01)}
.exhibit.full .artwork .frame .glint{position:absolute;inset:0;background:linear-gradient(135deg,rgba(255,255,255,.03) 0%,transparent 50%);pointer-events:none}
.exhibit.full .caption{position:absolute;bottom:48px;left:48px;z-index:10;opacity:0;transform:translateY(20px);transition:all .8s ease .4s}
.exhibit.full .caption.visible{opacity:1;transform:translateY(0)}
.exhibit.full .caption .num{font-size:10px;color:#555;letter-spacing:2px;margin-bottom:6px}
.exhibit.full .caption .title{font-size:16px;font-weight:300;color:#d4d0ca;letter-spacing:1px}
.divider{padding:60px 48px;text-align:center;font-size:9px;color:#1a1a1a;letter-spacing:6px;position:relative}
.divider::before,.divider::after{content:'';position:absolute;top:50%;width:30%;height:1px;background:#1a1a1a}
.divider::before{left:48px}
.divider::after{right:48px}
.section-title{padding:120px 48px 24px;font-size:10px;color:#333;letter-spacing:6px;font-weight:400}
.carousel{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:2px;padding:0 2px;background:#0a0a0a}
.carousel .cell{position:relative;overflow:hidden;cursor:pointer;aspect-ratio:16/10;background:#111;opacity:0;transform:translateY(20px);transition:all .6s cubic-bezier(.25,.46,.45,.94)}
.carousel .cell.visible{opacity:1;transform:translateY(0)}
.carousel .cell img{width:100%;height:100%;object-fit:cover;transition:transform .6s ease,filter .6s ease}
.carousel .cell:hover img{transform:scale(1.04);filter:brightness(1.06)}
.carousel .cell .c-label{position:absolute;bottom:0;left:0;right:0;padding:48px 16px 12px;background:linear-gradient(transparent,rgba(0,0,0,.7));opacity:0;transition:opacity .4s ease}
.carousel .cell:hover .c-label{opacity:1}
.carousel .cell .c-label span{font-size:10px;color:#999;letter-spacing:1px}
.lightbox{position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,.88);backdrop-filter:blur(24px);-webkit-backdrop-filter:blur(24px);display:flex;align-items:center;justify-content:center;opacity:0;pointer-events:none;transition:opacity .5s ease}
.lightbox.active{opacity:1;pointer-events:auto}
.lightbox .lb-frame{position:relative;max-width:90vw}
.lightbox .lb-frame img{max-width:90vw;max-height:88vh;object-fit:contain;border-radius:2px;box-shadow:0 24px 80px rgba(0,0,0,.6);transform:scale(.94);transition:transform .5s cubic-bezier(.25,.46,.45,.94)}
.lightbox.active .lb-frame img{transform:scale(1)}
.lightbox .lb-frame .lb-meta{text-align:center;margin-top:16px;font-size:10px;color:#555;letter-spacing:2px}
.lightbox .lb-close{position:absolute;top:24px;right:32px;width:36px;height:36px;display:flex;align-items:center;justify-content:center;color:#555;font-size:16px;cursor:pointer;background:none;border:none;border-radius:50%;transition:all .3s}
.lightbox .lb-close:hover{color:#d4d0ca;background:rgba(255,255,255,.04)}
.lightbox .lb-nav{position:absolute;top:50%;transform:translateY(-50%);width:44px;height:44px;display:flex;align-items:center;justify-content:center;background:none;border:none;color:#444;font-size:18px;cursor:pointer;border-radius:50%;transition:all .3s}
.lightbox .lb-nav:hover{color:#d4d0ca;background:rgba(255,255,255,.04)}
.lightbox .lb-nav.prev{left:16px}
.lightbox .lb-nav.next{right:16px}
footer{padding:120px 48px 40px;text-align:center;font-size:9px;color:#1a1a1a;letter-spacing:4px}
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
</style>
</head>
<body>

<div id="preloader"><div class="spinner"></div></div>

<header>
  <span class="logo">__TITLE__ <em>· 전시</em></span>
  <span class="info" id="workCount"></span>
</header>

<div class="hero">
  <img src="__HERO__" alt="" loading="lazy">
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


def js_array(items, out_dir, num_prefix):
    arr = []
    for i, it in enumerate(items):
        rel = os.path.relpath(it['path'], out_dir).replace(os.sep, '/')
        entry = {'file': rel, 'label': it['label'], 'sub': it['sub']}
        if num_prefix:
            entry['num'] = '%s %02d' % (num_prefix, i + 1)
        arr.append(entry)
    return json.dumps(arr, ensure_ascii=False).replace('</', '<\\/')


def build_html(title, hero, specials, fulls, grid, out_path):
    import datetime
    out_dir = os.path.dirname(os.path.abspath(out_path))
    ordered_fulls = specials + fulls
    full_arr = []
    for i, it in enumerate(ordered_fulls):
        rel = os.path.relpath(it['path'], out_dir).replace(os.sep, '/')
        prefix = 'MOTION' if it['gif'] else 'WORK'
        full_arr.append({'file': rel, 'label': it['label'], 'sub': it['sub'],
                         'num': '%s %02d' % (prefix, i + 1)})
    grid_arr = []
    for it in grid:
        rel = os.path.relpath(it['path'], out_dir).replace(os.sep, '/')
        grid_arr.append({'file': rel, 'label': it['label'], 'sub': it['sub']})

    hero_rel = os.path.relpath(hero['path'], out_dir).replace(os.sep, '/')
    subtitle = ' · '.join(re.sub(r'[^A-Za-z0-9 ]', '', title).upper().split()) or 'EXHIBITION'
    html = (TEMPLATE
            .replace('__TITLE__', title.replace('<', '&lt;'))
            .replace('__SUBTITLE__', subtitle + ' · EXHIBITION' if 'EXHIBITION' not in subtitle else subtitle)
            .replace('__HERO__', hero_rel.replace('"', '&quot;'))
            .replace('__YEAR__', str(datetime.date.today().year))
            .replace('__FULL_ITEMS__', json.dumps(full_arr, ensure_ascii=False).replace('</', '<\\/'))
            .replace('__GRID_ITEMS__', json.dumps(grid_arr, ensure_ascii=False).replace('</', '<\\/')))
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)


# ── CLI ──────────────────────────────────────────────────────

def next_free(path):
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    n = 2
    while os.path.exists('%s_%d%s' % (base, n, ext)):
        n += 1
    return '%s_%d%s' % (base, n, ext)


def main(argv):
    if not argv or argv[0] in ('help', '도움말', '-h', '--help'):
        print(__doc__.strip())
        return 0

    folder = None
    title = out = None
    recursive = False
    min_wide = 900
    open_after = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ('-t', '--title'):
            i += 1; title = argv[i]
        elif a in ('-o', '--out'):
            i += 1; out = argv[i]
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
        print('폴더를 알려주세요. 예: gallery ~/Desktop/이미지들')
        return 1
    folder = os.path.abspath(os.path.expanduser(folder))
    if not os.path.isdir(folder):
        print('폴더가 없어요: %s' % folder)
        return 1

    items = collect(folder, recursive)
    if not items:
        print('이미지가 하나도 없어요: %s' % folder)
        return 1

    title = title or os.path.basename(folder)
    out = os.path.abspath(os.path.expanduser(out)) if out \
        else next_free(os.path.join(folder, 'gallery.html'))

    specials, fulls, grid = classify(items, min_wide)
    hero = pick_hero(items)
    build_html(title, hero, specials, fulls, grid, out)

    print('완성! %s' % out)
    print('  작품 %d점 = 모션 %d + 대형 전시 %d + 그리드 %d'
          % (len(items), len(specials), len(fulls), len(grid)))
    if open_after:
        webbrowser.open('file://' + out)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
