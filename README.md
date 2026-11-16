# 📍 Geo Channel Auditor

> **제1회 라이너 해커톤 (.EXE-THON) 출품작**
> 상권 Geo 분석 기반 외부 채널 결핍 진단 · 콘텐츠 처방 에이전트
> 팀 클로클립 · 개인 참가: 최경선

오프라인 매장은 인스타그램·네이버 플레이스·블로그를 운영하지만, **"이 상권의 고객이 방문 전에 확인하려는 정보 중 우리 채널에 없는 것은 무엇인가"** 를 객관적으로 진단하지 못합니다.
Geo Channel Auditor는 **매장명과 상권 위치만** 넣으면 공개 채널을 자동 수집해 **Liner Search → Liner Search Agent → Liner Router** 3단계 파이프라인으로 돌리고, ① 정보 결핍 3개, ② **AI 인용 출처 분석**(상권 검색이 어떤 채널을 얼마나 인용하는지), ③ **채널 우선순위 추천**(어디부터 채워야 하는지), ④ 인스타그램 카드뉴스·플레이스 새소식·블로그 FAQ 원고를 **바로 발행할 수 있는 형태로** 써 줍니다.

```
상권 위치 + 업종 (+ 매장명, 채널 텍스트는 선택)
        │
        ├─ 0. 공개 채널 자동 수집   채널 텍스트가 없으면 매장 채널 노출 현황을 스스로 검색
        ├─ 1. Liner Search        상권 검색 쿼리 3개 → 실시간 수요·경쟁 출처 수집
        ├─ 2. Liner Search Agent  고객이 기대하는 정보·질문 요약 + 인용 수집
        ├─ 3. Liner Router        채널 vs 수요 vs 인용 비중 대조 → 결핍 3개 + 채널 우선순위 + 콘텐츠
                │
                └─ 점수 게이지 · 인용 출처 비중 바 · 1순위 추천 배너 · 결핍 카드(근거 출처) · 콘텐츠 탭(원클릭 복사) · API 파이프라인 로그
```

---

## 🚀 Quick Start (2분)

```bash
git clone https://github.com/kschoi/geo-channel-auditor.git
cd geo-channel-auditor

# 방법 A — uv
uv run uvicorn app.main:app --reload

# 방법 B — pip
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

브라우저에서 **http://127.0.0.1:8000** 을 열면 끝입니다.

* **API 키 없이도 바로 시연됩니다.** 키가 없으면 화면 상단에 `DEMO MOCK MODE` 배지가 뜨고, 가상 매장 3종(성수동 카페·서울대입구 피부과·산본 수제버거)으로 3단계 진단 전체를 체험할 수 있습니다.
* **실제 Liner API로 실행하려면** `.env` 또는 환경변수에 키를 넣습니다.

```bash
cp .env.example .env
# .env 파일에 LINER_API_KEY=... 입력 (커밋되지 않음, 앱 시작 시 자동으로 읽습니다)
uv run uvicorn app.main:app --reload
```

키를 넣으면 배지가 `LIVE LINER API` 로 바뀌고, 세 API가 실시간으로 호출됩니다.
UI의 **“Live 모드로 실행”** 칸에 키를 그때그때 넣을 수도 있습니다(이번 요청에만 사용, 저장·로깅 없음).

---

## 🧭 화면 구성

| 영역 | 내용 |
| --- | --- |
| 직접 입력(최상단) | 상권 위치 · 업종 · 매장명(선택) · 채널 텍스트(접힌 상세 영역의 **선택 입력**, 비우면 공개 채널 자동 수집) · (선택) API 키 |
| 빠른 예시 | 가상 매장 3종 칩(맨 아래). 누르면 입력이 채워지고 즉시 진단이 시작됩니다. |
| 진행 표시 | 1. Liner Search(자동 수집 포함) → 2. Liner Search Agent → 3. Liner Router 단계별 상태·소요 시간(SSE 스트리밍) |
| 결과 | 종합 점수 게이지 + 4개 세부 점수(방문편의·가격투명성·차별화·신뢰근거) |
| 수집된 매장 채널 | 자동 수집 / 직접 입력 / 직접+자동 배지, 수집 요약, 수집 쿼리·채널 분포 |
| AI 인용 출처 분석 | 채널별 인용 비중 수평 바 + 건수·퍼센트 + 대표 도메인 (네이버 블로그·플레이스·SNS·공식 웹·리뷰/언론) |
| 채널 우선순위 추천 | "1순위 추천: 네이버 블로그 (상권 인용 비중 XX%)" + 추천 이유, 2·3순위 목록 |
| 3대 정보 결핍 | 심각도 배지, 누락 이유, 상권 검색 수요 근거, **클릭 가능한 근거 출처 뱃지**, 한 줄 처방 |
| 채널별 콘텐츠 | Instagram 카드뉴스 / Naver Place 새소식 / Blog FAQ 탭 + **1순위 추천 마이크로 배지** + **원클릭 복사** + 규제 주의 문구 |
| 투명성 패널 | 어떤 Liner API가 몇 번, 어떤 모드(LIVE/DEMO)로, 얼마나 걸려 호출됐는지 + request ID + 원시 JSON |

---

## 🏗 구조

```
geo-channel-auditor/
├── app/
│   ├── main.py            FastAPI · Jinja2 · /api/health · /api/samples · /api/audit · /api/audit/stream
│   ├── auditor.py         3단계 파이프라인, 점수 가중 합산, 응답 정규화, 폴백
│   ├── liner_client.py    Liner API 클라이언트 (x-api-key, 텔레메트리, JSON 추출)
│   ├── samples.py         가상 매장 3종 (매장명·주소·채널 텍스트 전부 가상)
│   ├── demo_fixtures.py   데모 모드의 가상 API 응답 + 직접 입력용 템플릿
│   ├── templates/index.html
│   └── static/{app.js,styles.css}
├── tests/
│   ├── test_auditor.py    데모·라이브·폴백·스트리밍·키 비노출 검증
│   └── test_ui_contract.py 프런트엔드가 기대하는 응답 키/DOM 계약 검증
├── pyproject.toml · requirements.txt · .env.example · .gitignore
```

### Liner Platform API 사용 지점

| 단계 | 엔드포인트 | 용도 |
| --- | --- | --- |
| 1 | `POST /tools/search/web` | 상권 위치·업종으로 만든 쿼리 3개 실행 (`country_code: kr`, `lang: ko`) + 채널 텍스트가 없으면 매장 채널 수집 쿼리 2개 추가 |
| 2 | `POST /agents/search` | 상권 고객의 기대 정보·질문 요약과 인용 수집 |
| 3 | `POST /chat/completions` (`liner-gpt-router`, `response_format: json_object`) | 결핍 진단 + 채널 우선순위 추천 + 채널별 콘텐츠 JSON 생성 |
| 헬스 | `GET /credits/balance` | `/api/health?probe=true` 에서 잔액 확인 |

Base URL은 `https://platform.liner.com/api/v1`, 인증은 `x-api-key` 헤더입니다.
3단계 결과는 모두 UI에 그대로 매핑됩니다(점수·결핍·출처·인용 분석·추천·콘텐츠·호출 로그).

### 응답의 핵심 필드

* `collected_channel` — `mode`(`manual`/`auto`/`hybrid`), `label`, `summary`, `source_count`, `queries`, `breakdown`, `sources`
* `citation_analysis` — `total`, `summary`, 채널 5종(`naver_blog`·`naver_place`·`instagram_sns`·`official_web`·`review_media`)별 `count`·`share_pct`(합계 100%)·`domains`·`urls`, `top_channel`
* `recommended_channels` — `rank`·`channel`·`label`·`reason`·`priority_badge`·`content_channel` (Router가 누락하면 인용 비중 기반 결정론적 폴백)
* `contents[].priority_badge` — 1순위 추천 채널의 콘텐츠 탭 표시

---

## 🔐 보안 · 데이터 원칙

* **API 키 하드코딩 없음.** 키는 `LINER_API_KEY` 환경변수 또는 요청 본문의 1회성 값으로만 받고, 코드·로그·응답·디스크 어디에도 남기지 않습니다. (`tests/test_auditor.py::test_request_api_key_is_not_echoed` 로 검증)
* **가상·공개 데이터만 사용.** `app/samples.py` 의 매장 3종은 매장명·주소·채널 텍스트·가격 모두 **가상**입니다. 실제 고객 정보, 실제 사업자 정보, 클로클립의 비공개 데이터는 포함하지 않습니다.
* **데모 데이터 명시.** 데모 모드의 출처는 `demo: true` 플래그와 `DEMO` 표시로 구분되고, 제목 앞에 `[데모]` 가 붙습니다. 출처 URL은 실제로 열리는 채널별 검색 페이지(네이버 블로그·플레이스·인스타그램·다음)이며, `channel` 필드로 인용 채널을 함께 선언합니다.
* **기존 ClawClip과 분리된 신규 독립 프로젝트.** ClawClip의 비공개 비즈니스 로직·코드·데이터를 일절 사용하지 않았습니다. 저장소·의존성·배포 모두 독립적입니다.
* **폴백의 투명성.** LIVE 모드에서 API가 실패하면 화면을 비우지 않고 데모 데이터로 대체하되, 배지를 `DEMO FALLBACK` 으로 바꾸고 경고 문구와 파이프라인 로그에 남깁니다.

---

## 🧪 테스트

```bash
uv run pytest            # 또는 .venv/bin/pytest
```

검증 범위:

* `/api/health`, `/api/samples`, `/api/audit`, `/api/audit/stream`
* 데모 모드 샘플 3종 + 직접 입력(키워드 커버리지 기반 결핍 도출)
* **공개 채널 자동 수집**: `channel_text` 없이 실행(`auto`)·짧은 문구(`hybrid`)·충분한 문구(`manual`), 자동 수집 검색 2회 추가 호출
* **인용 출처 분석**: 도메인별 채널 분류(25개 URL 파라미터라이즈), 비중 합계 100%, 빈 출처 처리
* **채널 우선순위 추천**: Router 응답 반영 + 누락 시 인용 비중 폴백
* **라이브 모드**: `httpx.MockTransport` 로 Liner API 3종 응답을 흉내 내 실제 파싱 경로 검증(코드펜스 감싼 JSON 포함)
* **폴백**: 라이브 호출 500 → 데모 데이터 대체, `degraded: true`, 경고 노출
* 응답에 API 키가 새지 않는지, 결핍마다 근거 URL이 붙는지, 콘텐츠 3종이 채널 순서대로 오는지
* 프런트엔드 계약: JS가 읽는 응답 키와 DOM id가 템플릿에 존재하는지, 입력 폼이 샘플보다 위에 있는지, 채널 텍스트가 접힌 선택 입력인지

---

## ⚠️ 알려진 한계

* **데모 모드의 진단은 가상 데이터입니다.** 샘플 3종은 상권별 시나리오를 재현한 픽스처이고, 직접 입력은 채널 텍스트의 키워드 커버리지로 결핍을 추정하는 템플릿입니다. 실제 상권 검색 결과가 아니며, LIVE 모드에서만 Liner API의 실시간 결과를 사용합니다.
* **콘텐츠 원고는 초안입니다.** 특히 의료·법률 업종은 광고 심의 대상이 될 수 있어 `caution` 필드로 주의를 표시합니다. 발행 전 사실 확인이 필요합니다.
* Tailwind CSS는 CDN을 사용합니다(오프라인에서는 스타일이 단순해지지만 기능은 동작합니다).
* 상권 쿼리는 한국어/한국 상권 기준으로 생성됩니다(`country_code: kr`, `lang: ko`).

---

## 📄 라이선스

MIT. 해커톤 심사 목적의 공개 저장소입니다.
