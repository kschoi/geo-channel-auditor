"""공개 매장 검색용 예시와 테스트 전용 가상 픽스처.

SAMPLES에는 검색할 매장명과 상권만 둔다. 주소·운영시간·가격·채널 문구는 저장하지 않는다.
DEMO_SAMPLES는 GEO_AUDITOR_TEST_DEMO=1인 오프라인 테스트에서만 조회할 수 있다.
실행 앱은 API 키가 없거나 API가 실패하면 오류를 반환하며 가상 진단으로 대체하지 않는다.
"""

from __future__ import annotations

import os
from typing import Any

# 빠른 예시 — 실제 매장. 채널 문구는 비워 두고 공개 채널 자동 수집으로 진단한다.
SAMPLES: list[dict[str, Any]] = [
    {
        "id": "seongsu-lowkey",
        "label": "성수 로우키",
        "emoji": "☕",
        "store_name": "로우키 성수점",
        "location": "서울 성동구 성수동",
        "district": "성수동 연무장길",
        "industry": "스페셜티 커피 로스터리 카페",
        "category": "카페·디저트",
        "query": "성수 로우키 카페",
        "one_liner": "성수 로우키의 공개 자료를 검색합니다.",
        "real": True,
        "channels": {},
        "queries": [
            "성수 로우키 카페 메뉴 가격 디카페인",
            "성수동 로우키 주차 웨이팅 영업시간",
            "성수 카페 노트북 반려견 동반 로우키",
        ],
    },
    {
        "id": "snu-wootaeha-derm",
        "label": "서울대입구 우태하피부과",
        "emoji": "🩺",
        "store_name": "관악우태하피부과의원",
        "location": "서울 관악구 서울대입구",
        "district": "서울대입구역 상권",
        "industry": "피부과 의원",
        "category": "의원·클리닉",
        "query": "서울대입구 우태하피부과",
        "one_liner": "서울대입구 우태하피부과의 공개 자료를 검색합니다.",
        "real": True,
        "channels": {},
        "queries": [
            "서울대입구 우태하피부과 비급여 가격",
            "관악우태하피부과 진료시간 예약 대기",
            "서울대입구역 피부과 여드름 치료 후기",
        ],
    },
    {
        "id": "hongdae-tastyburger",
        "label": "홍대 테이스티버거",
        "emoji": "🍔",
        "store_name": "테이스티버거 홍대",
        "location": "서울 마포구 홍대",
        "district": "홍대 상권",
        "industry": "수제버거",
        "category": "외식·패스트푸드",
        "query": "홍대 테이스티버거",
        "one_liner": "홍대 테이스티버거의 공개 자료를 검색합니다.",
        "real": True,
        "channels": {},
        "queries": [
            "홍대 테이스티버거 메뉴 가격",
            "홍대 테이스티버거 웨이팅 영업시간",
            "홍대 수제버거 포장 배달 테이스티버거",
        ],
    },
]


# 데모 모드·테스트 전용 가상 매장. 채널 텍스트는 "매장이 운영 중인 외부 채널"을 가정한 가상 원고다.
DEMO_SAMPLES: list[dict[str, Any]] = [
    {
        "id": "seongsu-cafe",
        "label": "성수동 스페셜티 카페",
        "emoji": "☕",
        "store_name": "모먼트 로스터리 성수점",
        "location": "서울 성동구 성수동2가 315-12",
        "district": "성수동 카페거리",
        "industry": "스페셜티 카페",
        "category": "카페·디저트",
        "one_liner": "로스팅 스토리는 강하지만 주차·가격·좌석 정보가 비어 있는 카페",
        "channels": {
            "instagram": (
                "모먼트 로스터리 성수점 (@moment.roastery.seongsu) · 게시물 128 · 팔로워 3,412\n"
                "소개: 성수동 스페셜티 커피 | 직접 로스팅한 원두 | OPEN 08:00 - 20:00\n"
                "최근 게시물: \"오늘의 싱글 오리진, 에티오피아 구지 #2. 산미 좋아하시는 분들께 추천드려요.\"\n"
                "최근 게시물: \"라떼아트 연습 중. 주말엔 바리스타가 직접 내려드립니다.\"\n"
                "해시태그: #성수카페 #스페셜티커피 #홈카페 #원두"
            ),
            "naver_place": (
                "모먼트 로스터리 성수점\n"
                "스페셜티 카페 · 서울 성동구 성수동2가\n"
                "영업시간 08:00 - 20:00 (매일)\n"
                "소개: 성수동에서 직접 로스팅한 원두로 커피를 내리는 스페셜티 카페입니다. "
                "좋은 원두와 정성껏 내린 커피를 제공합니다.\n"
                "편의: 포장 가능, 단체석 문의"
            ),
            "blog": (
                "제목: 성수동 주말, 모먼트 로스터리에서 보낸 오후\n"
                "본문: 주말 오후 성수동 골목을 걷다가 발견한 카페. 창가 자리에 앉아 에티오피아 원두로 "
                "내린 핸드드립을 마셨다. 원두 향이 좋았고, 사장님이 원두 설명을 친절하게 해주셨다. "
                "다음에는 디저트도 먹어보고 싶다."
            ),
        },
        # 데모 모드에서 Liner Search API 대신 사용하는 상권 검색 쿼리 (가상)
        "queries": [
            "성수동 카페 주차 웨이팅",
            "성수동 스페셜티 카페 메뉴 가격 디카페인",
            "성수동 카페 노트북 콘센트 반려동물",
        ],
    },
    {
        "id": "snu-derm",
        "label": "서울대입구 피부과 의원",
        "emoji": "🩺",
        "store_name": "브라이트 피부과의원",
        "location": "서울 관악구 봉천동 1623-4 (서울대입구역 3번 출구 도보 3분)",
        "district": "서울대입구역 상권",
        "industry": "피부과 의원",
        "category": "의원·클리닉",
        "one_liner": "진료시간만 있고 가격·사례·예약 정보가 없는 의원",
        "channels": {
            "instagram": (
                "브라이트 피부과의원 (@bright.derm) · 게시물 41 · 팔로워 512\n"
                "소개: 서울대입구역 피부과 | 최신 장비와 숙련된 의료진\n"
                "최근 게시물: \"환절기 피부 관리, 무엇부터 시작할까요?\"\n"
                "최근 게시물: \"브라이트 피부과의원 내부 전경\""
            ),
            "naver_place": (
                "브라이트 피부과의원\n"
                "피부과 · 서울 관악구 봉천동\n"
                "진료시간: 평일 10:00 - 19:00, 토요일 10:00 - 14:00, 점심시간 13:00 - 14:00\n"
                "소개: 서울대입구역 피부과, 브라이트 피부과의원입니다. 깨끗하고 편안한 환경에서 "
                "진료합니다. 피부 고민이 있으시면 언제든 편하게 내원해 주세요.\n"
                "편의: 건물 지하주차장 이용 가능"
            ),
            "blog": (
                "제목: 브라이트 피부과의원에 다녀왔습니다\n"
                "본문: 여드름이 계속 올라와서 서울대입구역 근처 피부과를 찾았다. 상담을 받고 "
                "관리 과정을 안내받았다. 친절하게 설명해주셔서 좋았다."
            ),
        },
        "queries": [
            "서울대입구 피부과 비급여 가격",
            "여드름 흉터 치료 비용 횟수",
            "서울대입구 피부과 예약 대기 주차",
        ],
    },
    {
        "id": "sanbon-burger",
        "label": "산본 중심상가 수제버거",
        "emoji": "🍔",
        "store_name": "버거워크 산본점",
        "location": "경기 군포시 산본동 1140-3 산본역 3번 출구 도보 2분",
        "district": "산본 중심상가",
        "industry": "수제버거",
        "category": "외식·패스트푸드",
        "one_liner": "릴스는 잘 되지만 메뉴판·포장·대기 정보가 없는 매장",
        "channels": {
            "instagram": (
                "버거워크 산본점 (@burgerwalk.sanbon) · 게시물 76 · 팔로워 1,204\n"
                "소개: 산본역 3번 출구 수제버거 | 매일 11:00 - 21:00 | 브레이크 15:00 - 17:00\n"
                "최근 게시물(릴스): \"더블 치즈버거 만드는 과정\"\n"
                "최근 게시물(릴스): \"오늘도 패티 굽는 소리\"\n"
                "해시태그: #산본맛집 #수제버거 #군포맛집 #산본역맛집"
            ),
            "naver_place": (
                "버거워크 산본점\n"
                "수제버거 · 경기 군포시 산본동\n"
                "영업시간 11:00 - 21:00 (브레이크타임 15:00 - 17:00)\n"
                "소개: 산본역 근처 수제버거 맛집입니다. 매일 아침 직접 반죽한 번과 수제 패티로 "
                "버거를 만듭니다.\n"
                "편의: 포장 가능"
            ),
            "blog": "(네이버 블로그 미운영 — 인스타그램 릴스 중심으로만 홍보 중)",
        },
        "queries": [
            "산본 수제버거 가격 메뉴",
            "산본 중심상가 포장 배달 버거",
            "산본역 점심 맛집 대기 시간",
        ],
    },
]


def get_sample(sample_id: str) -> dict[str, Any] | None:
    """실제 매장 예시를 찾는다. 가상 픽스처는 명시적 오프라인 테스트에서만 허용한다."""
    for sample in (*SAMPLES, *(DEMO_SAMPLES if os.environ.get("GEO_AUDITOR_TEST_DEMO") == "1" else [])):
        if sample["id"] == sample_id:
            return sample
    return None


def sample_brief(sample: dict[str, Any]) -> dict[str, Any]:
    """UI 카드에 필요한 최소 정보만 추린다."""
    return {
        "id": sample["id"],
        "label": sample["label"],
        "emoji": sample["emoji"],
        "store_name": sample["store_name"],
        "location": sample["location"],
        "industry": sample["industry"],
        "one_liner": sample["one_liner"],
        "real": bool(sample.get("real")),
    }


def channel_text_of(sample: dict[str, Any]) -> str:
    """채널 텍스트를 하나의 진단 입력으로 합친다."""
    labels = {"instagram": "[Instagram]", "naver_place": "[Naver Place]", "blog": "[Blog]"}
    parts = []
    for key in ("instagram", "naver_place", "blog"):
        text = (sample.get("channels") or {}).get(key)
        if text:
            parts.append(f"{labels[key]}\n{text}")
    return "\n\n".join(parts)
