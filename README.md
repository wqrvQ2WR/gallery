# gallery

이미지·동영상 폴더를 넣으면 전시(exhibition) 스타일의 단일 `gallery.html`을 만들어주는 CLI.

폴더 하나 던지면 알아서 큐레이션해서 미술관처럼 걸어준다:

- **히어로** — 가로형 이미지 중 가장 디테일한 것(파일 용량 기준)을 골라 타이틀 뒤에 깔음
- **MOTION** — GIF·동영상(.mp4/.webm)은 맨 위 스페셜로. 화면에 보이면 자동 재생(음소거 루프), 라이트박스에선 컨트롤 지원
- **스크롤 BGM** — 폴더에 오디오(.mp3 .wav .ogg .m4a .aac .flac .opus)가 있으면 스크롤 위치에 따라 곡이 바뀐다. 우하단 ♪ 버튼으로 켬 (크로스페이드 전환)
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

## 웹 UI (기본 모드)

```zsh
gallery ~/Desktop/짤모음
```

그냥 폴더만 주면 웹 UI가 브라우저로 열린다. 6개 프리셋이 **실제 내 이미지로**
라이브 미리보기로 뜨고, 카드를 골라 제목·파일명 넣고 [생성하기]를 누르면 끝.
전체 미리보기(↗)도 지원. 브라우저 자동 실행이 싫으면 `--no-open`.

## 설치

의존성 없음. 파이썬 표준 라이브러리만 쓴다 (이미지 크기도 png/gif/jpeg/webp/bmp 헤더를 직접 파싱).

```zsh
git clone https://github.com/wqrvQ2WR/gallery.git
echo 'alias gallery='"'"'python3 "'"$PWD"'/gallery/gallery.py"'"'" >> ~/.zshrc
source ~/.zshrc
```

## 사용법

```
gallery <폴더>                → 웹 UI가 열림 (기본)
gallery <폴더> -p <프리셋>    → UI 없이 바로 생성

옵션:
  -p, --preset <이름>  프리셋 지정 → UI 없이 바로 생성 (목록은 --presets)
  -o, --out <파일>     출력 HTML 경로 지정 → UI 없이 바로 생성
                       (기본: <폴더>/gallery.html, 있으면 gallery_2.html …)
  --presets            프리셋 목록 보기
  -t, --title <제목>   전시 제목 (기본: 폴더 이름)
  -r, --recursive      하위 폴더까지 스캔
  --min-wide <px>      풀스크린 전시로 걸 최소 가로 픽셀 (기본 900)
  --audio <파일=A-B>   오디오 스크롤 구간 커스텀(%). 예: --audio bgm.mp3=0-50
                       여러 번 사용 가능. 기본은 찾은 오디오를 균등 분할
  --no-audio           오디오 아예 빼기
  --port <n>           웹 UI 포트 (기본: 8756부터 빈 포트 탐색)
  --no-open            웹 UI를 열 때 브라우저 자동 실행 끄기
  --open               바로 생성 모드에서 결과를 브라우저로 열기
  --ui                 프리셋/출력을 지정했어도 웹 UI를 강제로 열기
  help, 도움말, -h     도움말
```

예시:

```zsh
gallery ~/Desktop/짤모음                   # 웹 UI로 고르기 (기본)
gallery ~/Desktop/짤모음 -p polaroid --open
gallery ./assets -t "트릭" -p magazine -o 전시.html
gallery ./assets -p dark --audio intro.mp3=0-30 --audio main.mp3=30-100
```

웹 UI에도 **스크롤 BGM 패널**이 떠서 곡별로 켜고 끄거나 구간(%)을 바꿀 수 있다.

기존 `gallery.html`이 있으면 덮어쓰지 않고 `gallery_2.html`로 저장한다.
지원 포맷 — 이미지: png · jpg · jpeg · gif · webp · bmp · svg · avif /
동영상: mp4 · webm / 오디오: mp3 · wav · ogg · m4a · aac · flac · opus
