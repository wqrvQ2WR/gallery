# gallery

이미지 폴더를 넣으면 전시(exhibition) 스타일의 단일 `gallery.html`을 만들어주는 CLI.

폴더 하나 던지면 알아서 큐레이션해서 미술관처럼 걸어준다:

- **히어로** — 가로형 이미지 중 가장 디테일한 것(파일 용량 기준)을 골라 타이틀 뒤에 깔음
- **MOTION** — GIF는 맨 위 스페셜로
- **풀스크린 전시** — 큰 이미지는 한 점씩 100vh로, 4점마다 구분선
- **그리드** — 변형이 많은 이미지 그룹(같은 이름 3개 이상)과 작은 UI 조각들은 하단 그리드로
- **라이트박스** — 클릭하면 확대, `←` `→` `Esc` 키보드 지원
- 스크롤 리빌 애니메이션, 프리로더, 반응형(모바일) 포함

`bg_sec01_w-BNYkEmmn.png` 같은 빌드 해시는 라벨에서 자동으로 떼고,
`_w` / `_m` 접미사는 `WIDE` / `MOBILE` 태그로 표시한다.

## 프리셋

| 이름 | 설명 |
|---|---|
| `dark` (기본) | 다크 전시 — 어두운 미술관, 은은한 스크롤 리빌 |
| `white` | 화이트 큐브 — 밝은 갤러리, 세리프 타이포, 흰 매트 액자 |
| `polaroid` | 폴라로이드 — 크림색 스크랩북, 삐뚤빼뚤 프레임, 손글씨 캡션 |
| `neon` | 네온 아케이드 — 심야 아케이드, 시안·마젠타 글로우 |
| `film` | 필름 시트 — 콘택트 시트, 필름 구멍, 모노스페이스 라벨 |
| `magazine` | 매거진 — 에디토리얼, 큼직한 세리프 헤드라인, 빨간 포인트 |

## 웹 UI로 고르기

```zsh
gallery ~/Desktop/짤모음 --ui --open
```

브라우저에 6개 프리셋이 **실제 내 이미지로** 라이브 미리보기로 뜬다.
카드를 골라 제목·파일명 넣고 [생성하기]를 누르면 끝. 전체 미리보기(↗)도 지원.

## 설치

의존성 없음. 파이썬 표준 라이브러리만 쓴다 (이미지 크기도 png/gif/jpeg/webp/bmp 헤더를 직접 파싱).

```zsh
git clone https://github.com/wqrvQ2WR/gallery.git
echo 'alias gallery='"'"'python3 "'"$PWD"'/gallery/gallery.py"'"'" >> ~/.zshrc
source ~/.zshrc
```

## 사용법

```
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
  help, 도움말, -h     도움말
```

예시:

```zsh
gallery ~/Desktop/짤모음 --ui --open       # 웹 UI로 고르기
gallery ~/Desktop/짤모음 -p polaroid --open
gallery ./assets -t "트릭" -p magazine -o 전시.html
```

기존 `gallery.html`이 있으면 덮어쓰지 않고 `gallery_2.html`로 저장한다.
지원 포맷: png · jpg · jpeg · gif · webp · bmp · svg · avif
