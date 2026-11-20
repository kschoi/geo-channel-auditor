"""Keep real population and synthetic reactions separate, including hostile text."""
import json
import shutil
import subprocess

from test_perception_ui import FAKE_DOM, ROOT, minimal_result, run_js


def execute(script, code, payload):
    source = FAKE_DOM + '\n' + (ROOT / 'app/static' / script).read_text()
    source += '\nconst payload=' + json.dumps(payload, ensure_ascii=False) + ';\n' + code
    result = subprocess.run([shutil.which('node'), '-e', source], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def test_population_chart_and_table_use_residents_not_customer_sentiment():
    data = {
        'region': {'name': '<img onerror=alert(1)> 산본2동'}, 'period_label': '2026년 10월 말',
        'metric': '주민등록 인구 · 10세 이상', 'display_total': 200, 'total': 220,
        'under10': {'total': 20},
        'cells': [{'gender': 'female', 'age_group': '20s', 'count': 50, 'share_pct': 25},
                  {'gender': 'male', 'age_group': '20s', 'count': 150, 'share_pct': 75}],
    }
    result = execute('population.js', """
const view=PopulationView.population(payload),nodes=walk(view);
console.log(JSON.stringify({text:view.textContent,links:nodes.filter(n=>n.href).map(n=>n.href),
 images:nodes.filter(n=>['img','script'].includes(n.tagName)).length,widths:nodes.map(n=>n.style.width).filter(Boolean)}));
""", data)
    assert '200' in result['text']
    assert '전체 연령 220명' in result['text']
    assert '10세 미만 20명' in result['text']
    assert '25.0%' in result['text'] and '75.0%' in result['text']
    assert '만족도나 방문 확률을 뜻하지 않아요' in result['text']
    assert result['images'] == 0
    assert result['links'] == ['https://jumin.mois.go.kr/ageStatMonth.do']
    assert all(0 <= float(v.rstrip('%')) <= 100 for v in result['widths'])


def test_synthetic_cells_show_one_person_not_demographic_percentages_and_escape_text():
    data = {'summary': {'positive': 1, 'negative': 0, 'mixed': 0, 'unknown': 9}, 'personas': [{
        'id': 'P01', 'gender': 'female', 'age': 21, 'age_group': '20s', 'district': '서울-동대문구',
        'reaction': {'sentiment': 'positive', 'reason': '<img src=x onerror=alert(1)>',
                     'preference_quote': '편안한 분위기의 식당을 찾는 가상 인물',
                     'question': '분위기에서 어떤 점을 가장 중요하게 보시나요?', 'evidence_ids': ['real-E1']},
    }]}
    result = execute('scenarios.js', """
let inspected=null;const view=ScenarioView.reactions(payload,x=>inspected=x);const nodes=walk(view);
const link=nodes.find(n=>n.tagName==='button'&&n.textContent.includes('연결된 매장 근거'));link.listeners.click();
console.log(JSON.stringify({text:view.textContent,inspected,images:nodes.filter(n=>['img','script'].includes(n.tagName)).length,labels:nodes.map(n=>n.attributes['aria-label']).filter(Boolean)}));
""", data)
    assert '실제 고객 감정 아님' in result['text']
    assert '가상 1명' in result['text']
    assert '100%' not in result['text']
    assert '서울-동대문구' in result['text']
    assert result['images'] == 0
    assert result['inspected'] == {'ids': ['real-E1']}
    assert any('가상 여성 20대 1명' in label for label in result['labels'])


def test_all_time_comparison_inspection_includes_valid_unknown_date_quotes():
    data=minimal_result()
    data['period']['scope']='all_available'
    data['stores'][0]['evidence']=[{'id':'u1','store_id':'target','aspect':'service','quote':'날짜 미확인 서비스 원문',
                                  'sentiment':'negative','voice':'customer_experience','period_status':'unknown'}]
    result=run_js("""
const view=new Element('main');PerceptionView.render(payload,view);
walk(view).find(n=>n.className==='p-matrix-cell').listeners.click();
console.log(JSON.stringify({quotes:walk(view).filter(n=>n.tagName==='blockquote').map(n=>n.textContent),text:view.textContent}));
""",data)
    assert '날짜 미확인 서비스 원문' in result['quotes']
    assert '게시일이 없는 문서도 포함' in result['text']
