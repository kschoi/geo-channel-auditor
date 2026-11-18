"""실제 결과 누락·부분 실패·출처 URL 경계 회귀 검증."""

from __future__ import annotations

import copy
import json

import httpx
import pytest

from app import samples as sample_data
from app.auditor import (
    AuditInput,
    _normalize_gaps,
    _normalize_source,
    build_citation_analysis,
    filter_store_sources,
    parse_search_query,
)
from app.demo_fixtures import get_fixture
from app.liner_client import LinerClient


def install_transport(monkeypatch, analysis, *, fail_collection=False):
    searches = 0
    instances = []

    def handler(request):
        nonlocal searches
        if request.url.path.endswith('/tools/search/web'):
            searches += 1
            if fail_collection and searches > 3:
                return httpx.Response(503, json={})
            return httpx.Response(200, json={'results': [{'url': 'https://blog.naver.com/review/1', 'title': '성수동 모먼트 카페 검색 결과'}]})
        if request.url.path.endswith('/agents/search'):
            return httpx.Response(200, json={'answer': '주차·가격 안내가 필요합니다.', 'references': []})
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(analysis)}}]})

    original_init = LinerClient.__init__

    def patched_init(self, api_key=None, **kwargs):
        original_init(self, api_key=api_key, transport=httpx.MockTransport(handler), **kwargs)
        instances.append(self)

    monkeypatch.setattr(LinerClient, '__init__', patched_init)
    monkeypatch.setenv('LINER_API_KEY', 'synthetic-test-key')
    return instances


@pytest.mark.parametrize('defect', [
    'empty', 'scores', 'gaps', 'contents', 'empty_body', 'bad_score', 'summary',
    'summary_object', 'gap_reason_object', 'gap_title_object', 'content_body_object', 'content_title_object',
])
def test_incomplete_router_output_fails_without_synthetic_result(client, monkeypatch, defect):
    analysis = copy.deepcopy(get_fixture('seongsu-cafe')['analysis'])
    if defect == 'empty':
        analysis = {}
    elif defect in ('scores', 'gaps', 'contents', 'summary'):
        analysis.pop(defect)
    elif defect == 'empty_body':
        analysis['contents'][0]['body'] = ''
    elif defect == 'summary_object':
        analysis['summary'] = {'text': '잘못된 타입'}
    elif defect.startswith('gap_'):
        analysis['gaps'][0][defect.split('_')[1]] = {'text': '잘못된 타입'}
    elif defect.startswith('content_'):
        analysis['contents'][0][defect.split('_')[1]] = {'text': '잘못된 타입'}
    else:
        analysis['scores'][0]['score'] = 'not-a-number'
    instances = install_transport(monkeypatch, analysis)
    response = client.post('/api/audit', json={'query': '성수동 모먼트 카페'})
    assert response.status_code == 502
    body = response.json()
    assert 'detail' in body
    assert 'overall_score' not in body
    assert 'contents' not in body
    assert 'demo' not in response.text
    router_call = next(call for call in instances[-1].calls if call['endpoint'] == 'chat.completions')
    assert router_call['status'] == 'error'
    assert router_call['detail'] == 'invalid_response'


def test_collection_failure_stops_without_fake_diagnosis(client, monkeypatch):
    analysis = copy.deepcopy(get_fixture('seongsu-cafe')['analysis'])
    install_transport(monkeypatch, analysis, fail_collection=True)
    response = client.post('/api/audit', json={'query': '성수동 모먼트 카페'})
    assert response.status_code == 502
    assert 'overall_score' not in response.json()
    assert '503' in response.json()['detail']


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'httpx:secret', 'http:/bad', 'https://',
                                 'https://user:password@example.com/', 'https://example.com:bad/',
                                 'https://example.com/\nattack'])
def test_invalid_source_urls_are_rejected(url):
    assert _normalize_source(url) is None
    assert _normalize_source({'url': url}) is None
    assert build_citation_analysis([{'url': url}])['total'] == 0


def test_source_hostname_and_favicon_do_not_trust_upstream_fields():
    source = _normalize_source({'url': 'https://blog.naver.com/a/1', 'hostname': 'unrelated.example',
                                'faviconUrl': 'data:text/html,malicious'})
    assert source['hostname'] == 'blog.naver.com'
    assert source['faviconUrl'].startswith('https://www.google.com/')


def test_citation_summary_explains_search_results_and_deduplication():
    sources = [{'url': 'https://blog.naver.com/a/1'}, {'url': 'https://blog.naver.com/a/1'}]
    result = build_citation_analysis(sources)
    assert result['total'] == 1
    assert '검색 결과' in result['summary']
    assert '자동 수집 포함' in result['summary']


def test_no_key_outside_test_mode_fails_clearly(client, monkeypatch):
    monkeypatch.delenv('GEO_AUDITOR_TEST_DEMO', raising=False)
    response = client.post('/api/audit', json={'query': '성수 카페 로우키'})
    assert response.status_code == 502
    assert 'API 키가 없습니다' in response.json()['detail']
    assert 'overall_score' not in response.json()
    assert sample_data.get_sample('seongsu-cafe') is None


def test_public_examples_have_no_fabricated_channel_copy():
    assert all(sample['real'] is True for sample in sample_data.SAMPLES)
    assert all(not sample_data.channel_text_of(sample) for sample in sample_data.SAMPLES)
    assert all(not any(char.isdigit() for char in sample['location']) for sample in sample_data.SAMPLES)


@pytest.mark.parametrize('store,location,good,bad', [
    ('로우키 성수점', '서울 성동구 성수동', '성수동 로우키 카페 후기', '연남동 로우키 후기'),
    ('관악우태하피부과의원', '서울 관악구 서울대입구', '서울대입구 우태하 피부과', '서울대입구 피부과 추천'),
    ('포에버의원 서울대입구점', '서울 관악구 서울대입구', '포에버의원 서울대입구', '포에버의원 강남점'),
    ('테이스티버거 홍대', '서울 마포구 홍대', '홍대 테이스티버거 후기', '홍대 햄버거 맛집'),
])
def test_store_evidence_requires_both_locality_and_distinctive_name(store, location, good, bad):
    audit = AuditInput(location=location, industry='카페 피부과 수제버거', store_name=store)
    sources = [
        {'url': 'https://example.com/1', 'title': good},
        {'url': 'https://example.com/2', 'title': bad},
    ]
    matched, excluded = filter_store_sources(audit, sources)
    assert matched == sources[:1]
    assert excluded == sources[1:]


def test_unrelated_sources_never_become_store_diagnosis(client, monkeypatch):
    instances = install_transport(monkeypatch, get_fixture('seongsu-cafe')['analysis'])
    response = client.post('/api/audit', json={'query': '성수동 카페 로우키'})
    assert response.status_code == 502
    assert '공개 자료를 찾지 못했어요' in response.json()['detail']
    assert not any(call['endpoint'] == 'chat.completions' for call in instances[-1].calls)


def test_evidence_and_unknown_domains_are_not_claimed_as_official(client, monkeypatch):
    install_transport(monkeypatch, get_fixture('seongsu-cafe')['analysis'])
    body = client.post('/api/audit', json={'query': '성수동 카페 모먼트'}).json()
    assert body['mode'] == 'live'
    assert body['store_evidence']['matched_source_count'] == 1
    assert body['store_evidence']['sources'] == body['sources']
    assert build_citation_analysis([{'url': 'https://unknown-shop.example/'}])['top_channel'] == 'unclassified'


def test_model_invented_urls_are_not_accepted_as_gap_evidence():
    gaps = _normalize_gaps([{'title': '안내 확인', 'reason': '확인 필요',
                             'source_urls': ['https://unreturned.example/']}], {})
    assert gaps[0]['sources'] == []
    assert gaps[0]['source_urls'] == []


@pytest.mark.parametrize('query,store,industry', [
    ('서울대입구 우태하피부과', '우태하피부과', '피부과'),
    ('홍대 테이스티버거', '테이스티버거', '버거'),
    ('서울대입구 피부과 포에버의원', '포에버의원', '피부과'),
    ('성수동 카페 로우키', '로우키', '카페'),
    ('홍대 버거킹', '버거킹', '버거'),
    ('성수동 커피빈', '커피빈', '커피'),
    ('강남역 김밥천국', '김밥천국', '김밥'),
])
def test_free_query_preserves_store_names_containing_industry(query, store, industry):
    parsed = parse_search_query(query)
    assert parsed['store_name'] == store
    assert parsed['industry'] == industry
    assert parsed['location']


@pytest.mark.parametrize('query', ['성수동 카페', '서울대입구 피부과', '로우키'])
def test_live_diagnosis_requires_location_and_store_name(client, monkeypatch, query):
    instances = install_transport(monkeypatch, get_fixture('seongsu-cafe')['analysis'])
    response = client.post('/api/audit', json={'query': query})
    assert response.status_code == 400
    assert 'overall_score' not in response.json()
    assert not instances[-1].calls


@pytest.mark.parametrize('query,industry', [
    ('강남역 네일샵', '네일샵'), ('홍대 카페', '카페'),
    ('성수동 수제버거', '수제버거'), ('서울대입구 피부과의원', '피부과의원'),
])
def test_generic_industry_words_do_not_become_store_names(query, industry):
    parsed = parse_search_query(query)
    assert parsed['store_name'] == ''
    assert parsed['industry'] == industry


def test_nearby_recommendation_in_snippet_is_not_store_identity():
    audit = AuditInput(location='산본', industry='분식', store_name='디델리')
    sources = [
        {'title': '디델리 - 산본 분식, 라볶이 맛집',
         'url': 'https://www.diningcode.com/profile.php?rid=dideli',
         'description': '디델리 산본점의 안내'},
        {'title': '봉추찜닭 산본점 - 산본 찜닭 맛집',
         'url': 'https://www.diningcode.com/profile.php?rid=bongchu',
         'description': '60m 디델리 74점· 산본· 분식,라볶이. 봉추찜닭 영업시간 10시부터 밤 10시'},
        {'title': 'Poke all day 포케&샐러드 산본점',
         'url': 'https://www.diningcode.com/profile.php?rid=poke',
         'description': '디델리 74점· 산본· 매일 09:30~20:30'},
    ]
    matched, excluded = filter_store_sources(audit, sources)
    assert matched == sources[:1]
    assert excluded == sources[1:]


def test_store_name_ending_in_jeom_is_not_removed_as_branch_name():
    audit = AuditInput(location='홍대', industry='중식', store_name='홍콩반점')
    source = {'title': '홍대 홍콩반점 후기', 'url': 'https://blog.naver.com/review/1'}
    matched, excluded = filter_store_sources(audit, [source])
    assert matched == [source]
    assert not excluded


@pytest.mark.parametrize('title,expected', [
    ('<로우키 로스터리 카페영업 종료 안내> 로우키 남양주점은', False),
    ('로우키 강남점 영업 안내', False),
    ('로우키 성수점 영업 안내', True),
    ('로우키 성수점과 남양주점 안내', True),
])
def test_explicit_other_branch_title_is_not_target_store_evidence(title, expected):
    audit = AuditInput(location='서울 성동구 성수동', industry='카페', store_name='로우키 성수점')
    source = {'title': title, 'url': 'https://example.com/lowkey/notice',
              'description': '로우키 성수점과 남양주점의 안내 문구'}
    matched, excluded = filter_store_sources(audit, [source])
    assert bool(matched) is expected
    assert bool(excluded) is not expected
