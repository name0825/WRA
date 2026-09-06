BASE_CSS = """
* { box-sizing: border-box; }
body { margin: 0; font-family: -apple-system, "Segoe UI", "Malgun Gothic", sans-serif; background: #14161a; color: #e6e8eb; }
a { color: #7fb3ff; text-decoration: none; }
a:hover { text-decoration: underline; }
header { padding: 16px 24px; background: #1b1f26; border-bottom: 1px solid #2c323c; display: flex; gap: 16px; align-items: baseline; }
header h1 { font-size: 18px; margin: 0; }
header .crumb { color: #9aa4b2; font-size: 13px; }
main { padding: 24px; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th, td { padding: 8px 10px; text-align: left; border-bottom: 1px solid #2c323c; vertical-align: top; }
th { color: #9aa4b2; font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
tr:hover td { background: #1b1f26; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 12px; font-weight: 600; }
.high { background: #1d3d2a; color: #7ee2a8; }
.mid { background: #40381c; color: #e8ce77; }
.low { background: #45231f; color: #f09a92; }
.none { background: #2c323c; color: #9aa4b2; }
.muted { color: #9aa4b2; }
.pill { background: #2c323c; color: #cfd6e0; padding: 2px 8px; border-radius: 10px; font-size: 12px; margin-right: 4px; }
.frames { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.frame { border: 1px solid #2c323c; border-radius: 8px; overflow: hidden; background: #0f1114; }
.frame h3 { margin: 0; padding: 8px 12px; font-size: 13px; background: #1b1f26; border-bottom: 1px solid #2c323c; }
.frame iframe { width: 100%; height: 70vh; border: 0; background: #fff; }
section { margin-bottom: 28px; }
section h2 { font-size: 15px; margin: 0 0 12px; color: #cfd6e0; }
pre { background: #0f1114; border: 1px solid #2c323c; border-radius: 8px; padding: 12px; overflow: auto; max-height: 60vh; font-size: 12px; }
ul.suggestions { margin: 0; padding-left: 18px; font-size: 14px; line-height: 1.6; }
"""

RUN_LIST_TEMPLATE = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>WRA 결과 뷰어</title><style>{{ css }}</style></head>
<body><header><h1>WRA 결과 뷰어</h1><span class="crumb">{{ runs|length }}개 실행</span></header><main>
{% if not runs %}<p class="muted">'{{ output_root }}' 폴더에 실행 결과가 없습니다.</p>{% else %}
<table><tr><th>실행</th><th>제목</th><th>유형</th><th>페이지</th><th>반복</th><th>최고 반복</th><th>최고 점수</th><th>결과물</th></tr>
{% for run in runs %}<tr>
<td><a href="/run/{{ run.run_id }}">{{ run.run_id }}</a></td>
<td>{{ run.title }}</td><td>{{ run.site_type }}</td><td>{{ run.slugs|length }}</td><td>{{ run.iterations|length }}</td>
<td>{{ run.best_iteration if run.best_iteration is not none else '-' }}</td>
<td>{% if run.best_score is not none %}<span class="badge {{ score_band(run.best_score) }}">{{ '%.2f'|format(run.best_score) }}</span>{% else %}<span class="badge none">-</span>{% endif %}</td>
<td>{% if run.complete %}<span class="pill">final</span>{% else %}<span class="muted">미완료</span>{% endif %}</td>
</tr>{% endfor %}</table>{% endif %}
</main></body></html>"""

RUN_DETAIL_TEMPLATE = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>{{ run.run_id }}</title><style>{{ css }}</style></head>
<body><header><h1><a href="/">WRA</a> / {{ run.run_id }}</h1>
<span class="crumb">{{ run.title }} · {{ run.site_type }} · 최고 {{ '%.2f'|format(run.best_score) if run.best_score is not none else '-' }} (반복 {{ run.best_iteration if run.best_iteration is not none else '-' }})</span>
{% if run.features %}<span class="crumb"><a href="/run/{{ run.run_id }}/features?page={{ run.slug }}">features.json</a></span>{% endif %}</header><main>
{% if run.slugs|length > 1 %}<p class="muted">페이지: {% for slug in run.slugs %}{% if slug == run.slug %}<strong>{{ slug }}</strong>{% else %}<a href="/run/{{ run.run_id }}?page={{ slug }}">{{ slug }}</a>{% endif %}{% if not loop.last %} · {% endif %}{% endfor %}</p>{% endif %}
<section><h2>원본 / 최종</h2><div class="frames">
<div class="frame"><h3>원본 {{ run.slug }} {% if run.has_original %}<a href="/raw/{{ run.run_id }}/original/{{ run.slug }}" target="_blank">새 창</a>{% endif %}</h3>
{% if run.has_original %}<iframe src="/raw/{{ run.run_id }}/original/{{ run.slug }}"></iframe>{% else %}<p class="muted" style="padding:12px">없음</p>{% endif %}</div>
<div class="frame"><h3>{{ run.slug }}.html {% if run.has_final %}<a href="/raw/{{ run.run_id }}/final/{{ run.slug }}" target="_blank">새 창</a>{% endif %}</h3>
{% if run.has_final %}<iframe src="/raw/{{ run.run_id }}/final/{{ run.slug }}"></iframe>{% else %}<p class="muted" style="padding:12px">없음</p>{% endif %}</div>
</div></section>
<section><h2>반복</h2>
{% if not run.iterations %}<p class="muted">저장된 반복 결과가 없습니다.</p>{% else %}
<table><tr><th>반복</th><th>점수</th><th>검증</th><th>요약</th><th>산출물</th></tr>
{% for iteration in run.iterations %}<tr>
<td><a href="/run/{{ run.run_id }}/iteration/{{ iteration.number }}?page={{ run.slug }}">{{ iteration.number }}</a>{% if iteration.number == run.best_iteration %} <span class="pill">best</span>{% endif %}</td>
<td>{% if iteration.score is not none %}<span class="badge {{ score_band(iteration.score) }}">{{ '%.2f'|format(iteration.score) }}</span>{% else %}<span class="badge none">-</span>{% endif %}</td>
<td>{% if iteration.valid is none %}<span class="muted">-</span>{% elif iteration.valid %}<span class="badge high">valid</span>{% else %}<span class="badge low">invalid</span>{% endif %}</td>
<td class="muted">{{ (iteration.evaluation or {}).get('summary') or '' }}</td>
<td>{% if iteration.has_html %}<a href="/raw/{{ run.run_id }}/iteration/{{ iteration.number }}/{{ run.slug }}" target="_blank">html</a>{% endif %}
{% for artifact in artifacts(iteration) %} <a href="/json/{{ run.run_id }}/iteration/{{ iteration.number }}/{{ artifact }}">{{ artifact }}</a>{% endfor %}</td>
</tr>{% endfor %}</table>{% endif %}</section>
</main></body></html>"""

ITERATION_TEMPLATE = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>{{ run.run_id }} #{{ iteration.number }}</title><style>{{ css }}</style></head>
<body><header><h1><a href="/">WRA</a> / <a href="/run/{{ run.run_id }}?page={{ run.slug }}">{{ run.run_id }}</a> / {{ run.slug }} / 반복 {{ iteration.number }}</h1>
<span class="crumb">{% if previous %}<a href="/run/{{ run.run_id }}/iteration/{{ previous }}?page={{ run.slug }}">← 이전</a>{% endif %}
{% if next %}<a href="/run/{{ run.run_id }}/iteration/{{ next }}?page={{ run.slug }}">다음 →</a>{% endif %}</span></header><main>
<section><h2>결과 HTML</h2>
{% if iteration.has_html %}<div class="frame"><h3><a href="/raw/{{ run.run_id }}/iteration/{{ iteration.number }}/{{ run.slug }}" target="_blank">새 창에서 열기</a></h3>
<iframe src="/raw/{{ run.run_id }}/iteration/{{ iteration.number }}/{{ run.slug }}"></iframe></div>
{% else %}<p class="muted">html.html 없음</p>{% endif %}</section>
{% if criteria %}<section><h2>평가 · 총점
<span class="badge {{ score_band(iteration.score) }}">{{ '%.2f'|format(iteration.score) if iteration.score is not none else '-' }}</span></h2>
<table><tr><th>항목</th><th>점수</th><th>피드백</th></tr>
{% for key, score, feedback in criteria %}<tr><td>{{ key }}</td>
<td>{% if score is not none %}<span class="badge {{ score_band(score, 10) }}">{{ score }}</span>{% else %}<span class="badge none">-</span>{% endif %}</td>
<td>{{ feedback }}</td></tr>{% endfor %}</table></section>{% endif %}
{% if summary %}<section><h2>요약</h2><p>{{ summary }}</p></section>{% endif %}
{% if suggestions %}<section><h2>개선 제안</h2><ul class="suggestions">{% for item in suggestions %}<li>{{ item }}</li>{% endfor %}</ul></section>{% endif %}
{% if validation %}<section><h2>검증</h2><pre>{{ validation }}</pre></section>{% endif %}
<section><h2>산출물</h2><p>{% for artifact in artifacts %}<a class="pill" href="/json/{{ run.run_id }}/iteration/{{ iteration.number }}/{{ artifact }}">{{ artifact }}.json</a>{% endfor %}</p></section>
</main></body></html>"""

JSON_TEMPLATE = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>{{ heading }}</title><style>{{ css }}</style></head>
<body><header><h1><a href="/">WRA</a> / <a href="/run/{{ run_id }}">{{ run_id }}</a> / {{ heading }}</h1></header>
<main><pre>{{ payload }}</pre></main></body></html>"""
