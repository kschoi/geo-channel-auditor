# ClawClip 매장 인사이트 · Geo Channel Auditor

> Liner .EXE-THON · 팀 클로클립 · 최경선

**“고객은 우리 매장을 어떻게 볼까?” 후기에서 이유를 찾고, 동네 인구를 살펴보고, 이번 주에 해볼 일을 고릅니다.**

`산본 디델리 분식`처럼 동네·실제 매장명·업종을 한 번에 입력합니다. 경쟁점을 최대 2개 넣으면 같은 검색 예산으로 자료를 모아 맛·응대·가격·분위기·접근성·대기·청결을 비교합니다.

## 실행

심사자는 제출한 소스 ZIP을 풀거나 저장소를 받습니다.

```bash
git clone https://github.com/kschoi/geo-channel-auditor.git
cd geo-channel-auditor
```

ZIP을 사용했다면 압축을 푼 프로젝트 폴더에서 바로 실행합니다. Python 3.10 이상과 [uv](https://docs.astral.sh/uv/)가 필요합니다.

```bash
uv sync --frozen --extra dev
cp .env.example .env
# .env에 LINER_API_KEY 설정
uv run uvicorn app.main:app --host 127.0.0.1 --port 8765
```

[로컬 앱](http://127.0.0.1:8765)에서 시작합니다. 심사용 키를 `.env`의 `LINER_API_KEY` 값에 넣으면 됩니다. 추가 공공데이터 키는 필요하지 않습니다. `.env`는 Git과 제출 ZIP에서 제외합니다. 접힌 API 설정의 일회성 키도 사용할 수 있으며 브라우저에 저장하지 않습니다.

## 세 가지 자료를 함께 읽습니다

| 화면 | 무엇을 보여주나요? | 숫자의 의미 |
| --- | --- | --- |
| 공개 후기 분석 | 긍정·부정·혼합·중립, 항목별 경쟁점 비교, 원문 인용 | 이번 검색에서 확인한 후기 문서 수 |
| 우리 동네 인구 | 행정동을 골라 성별×연령 분포와 숫자 표 확인 | 행안부의 2026년 10월 말 주민등록 인구 |
| 가상 고객 반응 실험 | 합성 인물의 음식 취향과 매장 근거를 연결한 반응·질문 | 공개 합성 인물 10명의 모델 반응. 실제 고객 조사 아님 |

기본 기간은 **찾은 전체 기간**입니다. 게시일이 없는 후기도 표시를 남기고 함께 읽습니다. 최근 90·180·365일을 선택하면 그 기간에 게시된 후기만 주요 집계에 넣습니다. 같은 문서는 반복해서 세지 않습니다.

인구 화면은 공식 CSV를 담은 스냅샷이라 별도 공공데이터 키 없이 열립니다. 전국·시도·시군구·읍면동 3,921개 행정구역을 검색하고 직접 선택합니다. 지역 인구를 매장 고객 수로 바꾸거나 리뷰 작성자의 성별·연령을 추정하지 않습니다.

가상 반응 실험은 **NVIDIA Nemotron-Personas-Korea**의 성인 합성 인물 10명을 사용합니다. 남녀 각 20·30·40·50·60세 이상에서 1명씩 고른 시연용 표본입니다. 모델에는 음식 취향과 확인한 매장 근거를 전달합니다. 이름·성별·나이·출신 지역은 판단 입력에서 제외합니다. 각 칸은 가상 인물 1명이며, 집단의 만족도나 방문 확률이 아닙니다.

## 근거에서 행동으로

1. **Liner Search·Search Agent**가 실제 매장 공개 자료를 찾습니다. Agent가 작성한 답변 자체는 사실 근거로 쓰지 않습니다.
2. **Liner Router**가 인용문·항목·감정·글의 성격을 추출합니다.
3. 서버가 출처·인용 일치·게시일·중복을 검사합니다. 후기와 홍보·기사·미확인 자료를 나눕니다.
4. 확인할 불편과 살릴 강점을 원문 근거에 연결합니다. 가상 반응 실험은 사용자가 별도 버튼으로 시작합니다.

| Liner API | 실제 호출 | 이 앱에서 하는 일 |
| --- | --- | --- |
| Search | `POST /tools/search/web` | 공개 매장·경쟁점 문서와 공식 상권 자료 후보 검색 |
| Search Agent | `POST /agents/search` | 추가 출처 수집. 반환한 `references`만 근거로 채택 |
| GPT Router | `POST /chat/completions` | 후기의 경험·감정·인용 추출, 상권 수치 후보 정리, 별도 가상 반응 실험 |
| Images | `POST /images/generations` | 카드뉴스 AI 배경 이미지 생성 (`/source-audit`) |

공통 주소는 `https://platform.liner.com/api/v1`이며, Router는 `model=liner-gpt-router`, `routing_strategy=balanced`를 사용합니다. 출처·인용 검증과 행동 제안 연결은 서버 코드가 담당합니다. 주민등록 인구는 행안부 CSV 스냅샷에서 읽으며 Liner가 생성한 숫자가 아닙니다.

매장당 Search 3회·Agent 1회, 공식 상권 검색 2회, 근거가 있으면 Router 최대 2회입니다. 가상 반응은 같은 분석 보고서에서 Router를 최대 1회 추가 호출합니다. 카드뉴스·이미지 생성은 별도 요청입니다. 상세 출처와 실제 호출 기록은 접힌 영역에서 확인합니다.

감정 분류는 AI 해석입니다. 전체 고객 만족도, 사이트 전체 리뷰, 다른 AI의 인용률을 측정하지 않습니다. 비교 자료가 적거나 게시 시점·출처 구성이 맞지 않으면 한계를 표시합니다. 자세한 기준은 [제품 스펙](docs/customer-perception-spec.md)에 정리했습니다.

## 데이터 출처와 최신성

- **실제 인구:** [행정안전부 주민등록 인구통계](https://jumin.mois.go.kr/ageStatMonth.do), 2026-10-31 기준. [공공데이터포털의 같은 종류 CSV 카탈로그](https://www.data.go.kr/data/15097972/fileData.do)는 무료·이용허락범위 제한 없음으로 안내합니다. 원본 해시·조회 조건·집계 방식은 `app/data/population-202610.json`에 담았습니다. 실시간 갱신 자료가 아닙니다.
- **합성 인물:** [NVIDIA Nemotron-Personas-Korea](https://huggingface.co/datasets/nvidia/Nemotron-Personas-Korea), NVIDIA Corporation, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). 첫 100행에서 성별·연령 구간별 첫 사례를 선택하고 필요한 필드만 추출했습니다. 원본 UUID·행 번호·출처와 변경 내역은 `app/data/synthetic-personas.json`에 남깁니다.

## 매장 정보 검토·카드뉴스

[/source-audit](http://127.0.0.1:8765/source-audit)에서는 주소·시간·가격·주차·예약·메뉴를 확인하고 카드뉴스 3장을 제작·수정·다운로드합니다. 새 분석에서 대상 매장 검색어만 넘겨 경쟁점 자료가 원고에 섞이지 않게 합니다.

문구·사진 연출안은 Liner Router, AI 배경은 Liner Images API, 사진 배치·한글 합성·1080×1350 PNG 출력은 브라우저 Canvas가 담당합니다. 생성 이미지는 AI 연출 시안이며 실제 매장 사진이 아닙니다. [카드뉴스 사용법](docs/source-audit.md)을 참고하세요.

## 검증과 시연

```bash
uv run pytest
uv run ruff check .
node --check app/static/perception.js
node --check app/static/population.js
node --check app/static/scenarios.js
node tests/test_photo_editor.cjs
```

2026-11-20 기준 자동 테스트 **482개**, Ruff, 기존 카드 편집기 검사를 통과했습니다. 원문·기간·중복·키 보호·취소, 인구 합계와 지역 선택, 합성 반응의 취향·근거 연결을 검사합니다.

실제 산본 디델리·두끼 검색에서 Search·Agent·Router **12회가 모두 성공**했습니다. 전체 기간의 고객 경험 문서는 디델리 10건·두끼 12건이며, 이 중 날짜 미상은 각각 5건·6건입니다. 근거 관찰 90개를 채택하고 1개를 제외했습니다. 문서와 관찰은 집계 단위가 다릅니다. 가상 반응 Router 1회도 26.2초에 완료돼 총 13회 호출이 성공했습니다. 합성 인물 10명 모두 취향 인용과 근거 ID를 검증했습니다. 반응은 관심 1·망설임 1·혼합 4·보류 4이며 실제 고객 비율이 아닙니다.

390px 모바일에서 요약·인구·가상 실험, 표와 인물 선택을 확인했습니다. 페이지 폭도 390px로 가로 넘침이 없습니다.

[최종 시연 대본](docs/demo-script.md)은 실제 실행 화면에 맞춰 Liner API 활용 지점을 설명합니다. 검색 장면에서 Search·Search Agent, 경험 분류와 가상 반응 장면에서 GPT Router의 역할을 보여줍니다. 영상은 5분 이내이며, 검색·가상 반응의 대기 구간 일부만 단축했습니다. 앱 자체에는 영상 생성·SNS 자동 발행 기능이 없습니다.

제출 기획서는 [2페이지 PDF](docs/Geo_Channel_Auditor_기획서.pdf)입니다. 대상 사용자의 문제, 에이전트의 처리 방식, 실제 Liner API 호출, 다른 조직·직무로의 확장 계획을 담았습니다. 공개 후기·지역 인구·합성 인물 반응을 구분하며, 업무 시간 절감과 매출 효과는 측정한 결과로 주장하지 않습니다.


## API

- `POST /api/perception`, `/api/perception/stream`: 공개 후기·경쟁점·상권 문서 분석
- `GET /api/population/regions?q=산본`: 공식 행정구역 후보
- `GET /api/population/{region_code}`: 선택 지역의 실제 인구 스냅샷
- `GET /api/scenarios/personas`: 출처가 있는 합성 인물 10명
- `POST /api/scenarios/react`: 서버가 보관한 분석 근거로 가상 반응 실험
- `POST /api/audit`, `/api/audit/stream`: 방문 정보 검토
- `POST /api/cards`: 카드뉴스 문구·연출안
- `POST /api/card-images`, `GET /api/card-images/{request_id}`: 이미지 생성·조회
- `GET /api/health`: 키 설정 여부. `probe=true`는 잔액 API 연결 검사

API 오류를 가상 결과로 덮어쓰지 않습니다. 가상 고객 실험은 이름과 목적이 분리된 선택 기능입니다.
