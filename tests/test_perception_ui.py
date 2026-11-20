"""Execute the public dashboard renderer against untrusted and incomplete response data."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "app" / "static" / "perception.js"

FAKE_DOM = r"""
class Element {
  constructor(tag) {
    this.tagName = tag; this.childNodes = []; this.attributes = {};
    this.style = {setProperty(k, v) {this[k] = v;}};
    this._text = ''; this.value = ''; this.hidden = false; this.listeners = {};
  }
  set textContent(value) {this._text = String(value); this.childNodes = [];}
  get textContent() {return this._text + this.childNodes.map(x => x.textContent).join('');}
  set innerHTML(_) {throw new Error('Untrusted HTML insertion');}
  append(...nodes) {this.childNodes.push(...nodes);}
  replaceChildren(...nodes) {this._text = ''; this.childNodes = nodes;}
  setAttribute(k, v) {this.attributes[k] = String(v);}
  addEventListener(k, cb) {this.listeners[k] = cb;}
  scrollIntoView() {}
  focus() {}
}
globalThis.document = {
  readyState: 'loading', addEventListener() {},
  createElement: tag => new Element(tag),
  createDocumentFragment: () => new Element('#fragment'),
};
function walk(node) {return [node, ...node.childNodes.flatMap(walk)];}
"""


def run_js(code: str, payload: dict | None = None) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the frontend security contract")
    program = FAKE_DOM + "\n" + SCRIPT.read_text() + "\n"
    program += "const payload = " + json.dumps(payload or {}, ensure_ascii=False) + ";\n" + code
    completed = subprocess.run([node, "-e", program], capture_output=True, text=True, check=True)
    return json.loads(completed.stdout)


def minimal_result() -> dict:
    return {
        "stores": [{
            "id": "target", "name": "실제 매장", "location": "성수동", "query": "성수동 실제 매장",
            "summary": {"documents": 2, "positive": 1, "negative": 1, "mixed": 0, "neutral": 0,
                        "undated_documents": 0, "non_customer_documents": 0, "unclassified_documents": 0},
            "aspects": [{"id": "service", "label": "서비스", "documents": 2, "positive": 1,
                         "negative": 1, "mixed": 0, "neutral": 0}],
            "evidence": [], "channels": [],
        }],
        "period": {"start": "2026-01-01", "end": "2026-11-20"},
        "comparison": {"status": "not_requested", "rows": []}, "decisions": [],
        "market": {"facts": [], "sources": [], "demographics": {"cells": []}},
        "sources": [], "pipeline": {"calls": []},
    }


def test_unsafe_source_urls_never_become_links():
    result = run_js("""
const inputs = ['javascript:alert(1)', 'data:text/html,x', 'https://user:secret@example.com', '//example.com', 'https://example.com/a?q=1'];
console.log(JSON.stringify({urls: inputs.map(PerceptionView.safeUrl)}));
""")
    assert result["urls"] == ["", "", "", "", "https://example.com/a?q=1"]


def test_unknown_and_zero_counts_stay_distinct_and_zero_denominator_has_no_percentage():
    result = run_js("""
const p = PerceptionView;
console.log(JSON.stringify({counts:[p.count(null),p.count(0),p.count(-1)],
 ratios:[p.ratio(0,0),p.ratio(0,2),p.ratio(1,2),p.ratio(3,2)],
 levels:[p.density(null),p.density(0),p.density(25),p.density(50),p.density(75),p.density(100)]}));
""")
    assert result["counts"] == ["—", "0", "—"]
    assert result["ratios"] == [None, 0, 50, None]
    assert result["levels"] == ["unknown", "0", "1", "2", "3", "4"]


def test_partial_sse_is_buffered_and_errors_do_not_reflect_key():
    result = run_js(r"""
const first = PerceptionView.parseSSE('event: step\r\ndata: {"message":"찾는 중"}\r\n\r\nevent: result\ndata: {"stores":');
const next = PerceptionView.parseSSE(first.remainder + '[]}\n\n');
let malformed = false;
try {PerceptionView.parseSSE('event: result\ndata: nope\n\n');} catch (_) {malformed = true;}
console.log(JSON.stringify({first, next, malformed, redacted:PerceptionView.safeDetail('failed key-test-secret', 'key-test-secret')}));
""")
    assert result["first"]["events"] == [{"event": "step", "data": {"message": "찾는 중"}}]
    assert result["next"]["events"] == [{"event": "result", "data": {"stores": []}}]
    assert result["malformed"]
    assert "key-test-secret" not in result["redacted"]


def test_render_escapes_all_remote_text_and_omits_untrusted_api_body():
    payload = minimal_result()
    malicious = '<img src=x onerror="alert(1)">'
    payload["stores"][0]["name"] = malicious
    payload["stores"][0]["evidence"] = [{
        "id": "e1", "store_id": "target", "aspect": "service", "quote": malicious,
        "sentiment": "negative", "voice": "customer_experience", "period_status": "in_period",
        "url": "javascript:alert(1)", "title": malicious,
    }]
    payload["sources"] = [{"source_id": "S1", "url": "javascript:alert(1)", "title": malicious}]
    payload["pipeline"]["calls"] = [{"endpoint": "search", "status": "ok", "request": "private-key-in-body", "latency_ms": 123}]
    result = run_js("""
const container = new Element('main');
PerceptionView.render(payload, container);
const nodes = walk(container);
console.log(JSON.stringify({text:container.textContent, hrefs:nodes.filter(n=>n.href).map(n=>n.href),
 dangerous:nodes.filter(n=>['img','script'].includes(n.tagName)).length}));
""", payload)
    assert malicious in result["text"]
    assert result["dangerous"] == 0
    assert not any(url.startswith("javascript:") for url in result["hrefs"])
    assert "private-key-in-body" not in result["text"]
    assert "123ms" in result["text"]
    assert "부정 50.0%" in result["text"]


def test_public_review_demographics_remain_unknown_even_if_response_contains_inferred_numbers():
    result = run_js("""
const cells=[{gender:'female',age_group:'20s',count:17},{gender:'male',age_group:'30s',count:0}];
const reviews=PerceptionView.demographicGrid(cells,false);
const population=PerceptionView.demographicGrid(cells,true);
console.log(JSON.stringify({reviews:walk(reviews).map(n=>n.attributes['aria-label']).filter(Boolean),
 population:walk(population).map(n=>n.attributes['aria-label']).filter(Boolean)}));
""")
    assert "여성 20대: 미확인" in result["reviews"]
    assert "남성 30대: 미확인" in result["reviews"]
    assert "여성 20대: 17명" in result["population"]
    assert "남성 30대: 0명" in result["population"]


def test_unknown_period_evidence_filter_does_not_mix_primary_period():
    payload = minimal_result()
    payload["stores"][0]["evidence"] = [
        {"id": "known", "store_id": "target", "aspect": "service", "quote": "known-period-quote",
         "sentiment": "negative", "voice": "customer_experience", "period_status": "in_period"},
        {"id": "unknown", "store_id": "target", "aspect": "service", "quote": "unknown-period-quote",
         "sentiment": "negative", "voice": "customer_experience", "period_status": "unknown"},
    ]
    result = run_js("""
const container = new Element('main');
const view = PerceptionView.render(payload, container);
view.inspect({store:'target',aspect:'service',voice:'customer_experience',period:'in_period'});
const quotes=walk(container).filter(n=>n.tagName==='blockquote').map(n=>n.textContent);
console.log(JSON.stringify({quotes}));
""", payload)
    assert result["quotes"] == ["known-period-quote"]


def test_renderer_rejects_missing_store_instead_of_showing_fabricated_result():
    result = run_js("""
let error='';
try {PerceptionView.render({stores:[]},new Element('main'));} catch (e) {error=e.message;}
console.log(JSON.stringify({error}));
""")
    assert "비어 있어요" in result["error"]


def test_item_comparison_limit_is_visible_in_matrix_table_and_accessible_label():
    payload = minimal_result()
    payload["stores"].append({**payload["stores"][0], "id": "competitor-1", "name": "경쟁 매장"})
    row = {"aspect": "service", "label": "서비스", "comparable": False,
           "stores": [{"store_id": s["id"], "documents": 5, "positive": 0, "negative": 5, "mixed": 0, "neutral": 0} for s in payload["stores"]]}
    payload["comparison"] = {"status": "comparable", "rows": [row]}
    code = """
const root=new Element('main'); PerceptionView.render(payload,root); const nodes=walk(root);
console.log(JSON.stringify({text:root.textContent,limited:nodes.filter(n=>String(n.className).includes('p-matrix-limited')).length,
 labels:nodes.map(n=>n.attributes['aria-label']).filter(Boolean)}));
"""
    limited = run_js(code, payload)
    assert limited["limited"] == 1
    assert limited["text"].count("비교 제한") >= 3
    assert "같은 기준으로 비교" not in limited["text"]
    assert any("이 항목은" in value and "비교가 제한" in value for value in limited["labels"])
    payload["comparison"]["rows"][0]["comparable"] = True
    comparable = run_js(code, payload)
    assert comparable["limited"] == 0
    assert "같은 기준으로 비교" in comparable["text"]
