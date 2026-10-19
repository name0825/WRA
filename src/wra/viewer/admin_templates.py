"""Templates for the admin console pages."""

ADMIN_CSS = """
nav.admin { display: flex; gap: 14px; padding: 10px 24px; background: #171b21; border-bottom: 1px solid #2c323c; font-size: 13px; }
nav.admin a.active { color: #e6e8eb; font-weight: 600; }
nav.admin form.logout { display: inline; }
nav.admin form.logout button { background: none; border: 0; padding: 0; font: inherit; color: inherit; cursor: pointer; }
form.stack { display: flex; flex-direction: column; gap: 10px; max-width: 520px; }
input[type=text], input[type=password], input[type=number], select, textarea {
  background: #0f1114; color: #e6e8eb; border: 1px solid #2c323c; border-radius: 6px; padding: 8px 10px; font-size: 13px; font-family: inherit; }
textarea { font-family: ui-monospace, Consolas, monospace; width: 100%; min-height: 60vh; line-height: 1.5; }
button, .btn { background: #27405f; color: #dce8ff; border: 1px solid #33507a; border-radius: 6px; padding: 8px 14px; font-size: 13px; cursor: pointer; }
button:hover, .btn:hover { background: #2f4c72; text-decoration: none; }
button.danger { background: #4a2320; border-color: #6b332e; color: #f5b8b1; }
.toolbar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 12px; font-size: 13px; }
.notice { border-radius: 8px; padding: 10px 12px; margin-bottom: 16px; font-size: 13px; white-space: pre-wrap; }
.notice.ok { background: #1d3d2a; color: #a8e8c4; }
.notice.error { background: #45231f; color: #f3b6b0; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 16px; }
.card { border: 1px solid #2c323c; border-radius: 8px; padding: 14px 16px; background: #1b1f26; }
.card h3 { margin: 0 0 8px; font-size: 13px; color: #9aa4b2; text-transform: uppercase; letter-spacing: .04em; }
.card p { margin: 4px 0; font-size: 14px; }
.log { background: #0f1114; border: 1px solid #2c323c; border-radius: 8px; padding: 12px; font-size: 12px;
  font-family: ui-monospace, Consolas, monospace; white-space: pre-wrap; word-break: break-all; max-height: 70vh; overflow: auto; line-height: 1.5; }
.log .lvl-ERROR, .log .lvl-CRITICAL { color: #f09a92; }
.log .lvl-WARNING { color: #e8ce77; }
.log .lvl-DEBUG { color: #8d97a5; }
.dropzone { border: 2px dashed #33507a; border-radius: 10px; padding: 26px 18px; text-align: center; color: #9aa4b2;
  font-size: 13px; background: #171b21; margin-bottom: 16px; cursor: pointer; }
.dropzone.hover { background: #1e2836; border-color: #4a6f9f; color: #dce8ff; }
.dropzone input[type=file] { display: none; }
.dropzone p { margin: 4px 0; }
.preview { background: #0f1114; border: 1px solid #2c323c; border-radius: 8px; padding: 12px; text-align: center; }
.preview img { max-width: 100%; }
td form.inline { display: inline; }
td form.inline button { background: none; border: 0; padding: 0; font: inherit; color: #f3b6b0; cursor: pointer; }
"""

_HEAD = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>{{ title }}</title><style>{{ css }}</style></head>
<body><header><h1><a href="/">WRA</a> / 어드민 콘솔</h1><span class="crumb">{{ subtitle }}</span></header>
<nav class="admin">
<a href="/admin" class="{{ 'active' if active == 'home' }}">대시보드</a>
<a href="/admin/input" class="{{ 'active' if active == 'input' }}">입력</a>
<a href="/admin/process" class="{{ 'active' if active == 'process' }}">실행</a>
<a href="/admin/logs" class="{{ 'active' if active == 'logs' }}">로그</a>
<a href="/admin/config" class="{{ 'active' if active == 'config' }}">설정</a>
<a href="/admin/security" class="{{ 'active' if active == 'security' }}">보안</a>
<a href="/">결과 뷰어</a>
{% if login_required %}<form method="post" action="/admin/logout" class="logout"><button type="submit">로그아웃</button></form>{% endif %}
</nav><main>
{% if message %}<div class="notice ok">{{ message }}</div>{% endif %}
{% if error %}<div class="notice error">{{ error }}</div>{% endif %}
"""

_FOOT = "</main></body></html>"

LOGIN_TEMPLATE = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>어드민 로그인</title><style>{{ css }}</style></head>
<body><header><h1>WRA 어드민 콘솔</h1><span class="crumb">로그인</span></header><main>
{% if error %}<div class="notice error">{{ error }}</div>{% endif %}
<form class="stack" method="post"><label>비밀번호</label><input type="password" name="password" autofocus>
<div><button type="submit">로그인</button></div></form>
<p class="muted">비밀번호는 WRA_ADMIN_PASSWORD 환경변수, 없으면 config.yaml의 viewer.admin.password 값입니다.</p>
</main></body></html>"""

DENIED_TEMPLATE = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>접근 거부</title><style>{{ css }}</style></head>
<body><header><h1>WRA 어드민 콘솔</h1><span class="crumb">접근 거부</span></header><main>
<div class="notice error">{{ reason }}</div><p><a href="/">결과 뷰어로 돌아가기</a></p>
</main></body></html>"""

HOME_TEMPLATE = _HEAD + """
<section><h2>상태</h2><div class="cards">
<div class="card"><h3>실행</h3>
<p><span class="badge {{ 'high' if process.state == 'running' else ('low' if process.state == 'failed' else 'none') }}">{{ process.state }}</span></p>
{% if process.started_at %}<p class="muted">시작 {{ process.started_at }}{% if process.pid %} · pid {{ process.pid }}{% endif %}</p>{% endif %}
<p><a href="/admin/process">실행 관리 →</a></p></div>
<div class="card"><h3>결과</h3><p>{{ run_count }}개 실행 결과</p>
<p class="muted">{{ output_root }}</p><p><a href="/">결과 뷰어 →</a></p></div>
<div class="card"><h3>입력</h3><p>{{ input_count }}개 파일</p>
<p class="muted">{{ input_dir }}</p><p><a href="/admin/input">입력 파일 →</a></p></div>
<div class="card"><h3>로그</h3><p>{{ logs|length }}개 파일</p>
<p class="muted">{{ log_dir }}</p><p><a href="/admin/logs">로그 보기 →</a></p></div>
<div class="card"><h3>설정</h3><p>{{ config_files|length }}개 파일</p>
<p class="muted">편집 {{ '허용' if allow_config_edit else '잠김' }}</p><p><a href="/admin/config">설정 편집 →</a></p></div>
<div class="card"><h3>보안</h3><p>{{ '비밀번호 로그인' if login_required else '루프백 전용' }}</p>
<p class="muted">{{ warning_count }}건의 주의</p><p><a href="/admin/security">보안 상태 →</a></p></div>
</div></section>
<section><h2>최근 로그</h2><div class="log">{% for line in recent %}{{ line }}
{% endfor %}{% if not recent %}기록된 로그가 없습니다.{% endif %}</div></section>
""" + _FOOT

PROCESS_TEMPLATE = _HEAD + """
<section><h2>파이프라인 실행</h2>
<p>명령: <code>{{ command }}</code></p>
<p>상태: <span class="badge {{ 'high' if process.state == 'running' else ('low' if process.state == 'failed' else 'none') }}">{{ process.state }}</span>
{% if process.started_at %}<span class="muted"> · 시작 {{ process.started_at }} · pid {{ process.pid }}</span>{% endif %}
{% if process.returncode is not none %}<span class="muted"> · 종료 코드 {{ process.returncode }}</span>{% endif %}</p>
{% if allow_run %}
<div class="toolbar">
<form method="post" action="/admin/process/start"><button type="submit" {{ 'disabled' if process.state == 'running' }}>실행 시작</button></form>
<form method="post" action="/admin/process/stop"><button class="danger" type="submit" {{ '' if process.state == 'running' else 'disabled' }}>중지</button></form>
<label><input type="checkbox" id="follow" checked> 5초마다 갱신</label>
</div>
{% else %}<p class="muted">실행이 잠겨 있습니다 (viewer.admin.allow_run).</p>{% endif %}
{% if process.log_path %}<p class="muted">출력: {{ process.log_path }}</p>{% endif %}
<div class="log" id="output">{% for line in output %}{{ line }}
{% endfor %}{% if not output %}출력이 없습니다.{% endif %}</div></section>
<script>
setInterval(async () => {
  if (!document.getElementById('follow') || !document.getElementById('follow').checked) return;
  const response = await fetch('/admin/process/tail');
  if (!response.ok) return;
  const data = await response.json();
  const box = document.getElementById('output');
  const bottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 20;
  box.textContent = data.output.join('\\n') || '출력이 없습니다.';
  if (bottom) box.scrollTop = box.scrollHeight;
  if (data.state !== window.__state) location.reload();
}, 5000);
window.__state = {{ process.state|tojson }};
</script>
""" + _FOOT

LOG_LIST_TEMPLATE = _HEAD + """
<section><h2>로그 파일 <span class="muted">{{ log_dir }}</span></h2>
{% if not logs %}<p class="muted">로그 파일이 없습니다.</p>{% else %}
<table><tr><th>파일</th><th>크기</th><th>수정</th><th></th></tr>
{% for item in logs %}<tr><td><a href="/admin/logs/{{ item.name }}">{{ item.name }}</a></td>
<td>{{ item.size_text }}</td><td class="muted">{{ item.modified.strftime('%Y-%m-%d %H:%M:%S') }}</td>
<td><a href="/admin/logs/{{ item.name }}/download">내려받기</a></td></tr>{% endfor %}</table>{% endif %}
</section>
""" + _FOOT

LOG_VIEW_TEMPLATE = _HEAD + """
<section><h2>{{ name }} <span class="muted">{{ lines|length }}줄</span></h2>
<form class="toolbar" method="get">
<label>줄 수 <input type="number" name="lines" value="{{ tail_lines }}" min="10" max="20000" step="10"></label>
<label>최소 레벨 <select name="level"><option value="">전체</option>
{% for option in levels %}<option value="{{ option }}" {{ 'selected' if option == level }}>{{ option }}</option>{% endfor %}</select></label>
<label>검색 <input type="text" name="q" value="{{ query or '' }}"></label>
<button type="submit">적용</button>
<a class="btn" href="/admin/logs/{{ name }}/download">내려받기</a>
</form>
<div class="log">{% for line, level_name in lines %}<span class="lvl-{{ level_name or 'NONE' }}">{{ line }}</span>
{% endfor %}{% if not lines %}조건에 맞는 줄이 없습니다.{% endif %}</div></section>
""" + _FOOT

CONFIG_TEMPLATE = _HEAD + """
<section><h2>설정 파일</h2>
<p class="muted">{% for item in config_files %}{% if item.name == name %}<strong>{{ item.name }}</strong>{% else %}<a href="/admin/config?file={{ item.name }}">{{ item.name }}</a>{% endif %}{% if not item.exists %} <span class="pill">없음</span>{% endif %}{% if not loop.last %} · {% endif %}{% endfor %}</p>
<form method="post" action="/admin/config">
<input type="hidden" name="file" value="{{ name }}">
<textarea name="text" spellcheck="false" {{ 'readonly' if not allow_config_edit }}>{{ text }}</textarea>
<div class="toolbar" style="margin-top:12px">
{% if allow_config_edit %}<button type="submit" name="action" value="validate">검증만</button>
<button type="submit" name="action" value="save">저장</button>{% else %}<span class="muted">편집이 잠겨 있습니다 (viewer.admin.allow_config_edit).</span>{% endif %}
<span class="muted">저장 시 이전 내용은 .bak 파일로 남습니다. 저장한 설정은 다음 실행부터 적용됩니다.</span>
</div></form></section>
""" + _FOOT

SECURITY_TEMPLATE = _HEAD + """
<section><h2>접근 제어</h2><table>
<tr><th>항목</th><th>값</th></tr>
<tr><td>어드민 콘솔</td><td>{{ '활성' if admin.enabled else '비활성' }}</td></tr>
<tr><td>인증 방식</td><td>{% if password_set %}비밀번호 로그인 · <span class="muted">{{ password_source }}</span>{% else %}루프백 전용 (비밀번호 없음){% endif %}</td></tr>
<tr><td>세션 유지</td><td>{{ admin.session_hours }}시간{% if session_expires %} · 현재 세션 {{ session_expires }} 만료{% endif %}</td></tr>
<tr><td>바인딩</td><td>{{ host }}:{{ port }}</td></tr>
<tr><td>Flask 디버그</td><td>{{ '켜짐' if debug else '꺼짐' }}</td></tr>
<tr><td>설정 편집</td><td>{{ '허용' if admin.allow_config_edit else '잠김' }}</td></tr>
<tr><td>프로세스 실행</td><td>{{ '허용' if admin.allow_run else '잠김' }}</td></tr>
<tr><td>이 요청의 출발지</td><td>{{ remote_addr }}</td></tr>
</table></section>
<section><h2>주의</h2>
{% if not warnings %}<p class="muted">주의할 항목이 없습니다.</p>{% else %}
<ul class="suggestions">{% for item in warnings %}<li>{{ item }}</li>{% endfor %}</ul>{% endif %}
<p class="muted">이 값들은 config.yaml의 viewer 블록에서 바꿉니다. <a href="/admin/config?file=config.yaml">설정 편집 →</a></p>
</section>
""" + _FOOT

INPUT_LIST_TEMPLATE = _HEAD + """
<section><h2>입력 파일 <span class="muted">{{ input_dir }}</span></h2>
{% if allow_input_edit %}
<div class="dropzone" id="drop">
<p><strong>파일이나 폴더를 여기에 끌어다 놓으세요</strong></p>
<p>클릭하면 파일 선택 창이 열립니다. HTML·CSS·JS·이미지·폰트만 받습니다.</p>
<p class="muted" id="dropstatus"></p>
<input type="file" id="picker" multiple>
</div>
{% else %}<p class="muted">입력 파일 편집이 잠겨 있습니다 (viewer.admin.allow_input_edit).</p>{% endif %}
{% if not files %}<p class="muted">입력 폴더가 비어 있습니다.</p>{% else %}
<table><tr><th>파일</th><th>크기</th><th>수정</th><th></th></tr>
{% for item in files %}<tr>
<td><a href="/admin/input/view?path={{ item.relative|urlencode }}">{{ item.relative }}</a></td>
<td>{{ item.size_text }}</td><td class="muted">{{ item.modified.strftime('%Y-%m-%d %H:%M:%S') }}</td>
<td><a href="/admin/input/raw?path={{ item.relative|urlencode }}&amp;download=1">내려받기</a>
{% if allow_input_edit %} · <form class="inline" method="post" action="/admin/input/delete" data-confirm="{{ item.relative }}">
<input type="hidden" name="path" value="{{ item.relative }}"><button type="submit">삭제</button></form>{% endif %}</td>
</tr>{% endfor %}</table>
<p class="muted">{{ files|length }}개 파일 · 저장한 입력은 다음 실행부터 쓰입니다.</p>{% endif %}
</section>
{% if allow_input_edit %}<script>
document.querySelectorAll('form[data-confirm]').forEach(form => form.addEventListener('submit', event => {
  if (!confirm(form.dataset.confirm + ' 을(를) 지웁니다.')) event.preventDefault();
}));
</script>
<script>
const zone = document.getElementById('drop');
const picker = document.getElementById('picker');
const status = document.getElementById('dropstatus');

zone.addEventListener('click', event => { if (event.target !== picker) picker.click(); });
picker.addEventListener('change', () => send([...picker.files].map(file => [file.webkitRelativePath || file.name, file])));
['dragenter', 'dragover'].forEach(name => zone.addEventListener(name, event => {
  event.preventDefault(); zone.classList.add('hover'); }));
['dragleave', 'drop'].forEach(name => zone.addEventListener(name, event => {
  event.preventDefault(); zone.classList.remove('hover'); }));

zone.addEventListener('drop', async event => {
  const entries = [...event.dataTransfer.items]
    .map(item => item.webkitGetAsEntry && item.webkitGetAsEntry()).filter(Boolean);
  if (!entries.length) return send([...event.dataTransfer.files].map(file => [file.name, file]));
  status.textContent = '폴더를 읽는 중...';
  const collected = [];
  for (const entry of entries) await walk(entry, '', collected);
  send(collected);
});

async function walk(entry, prefix, collected) {
  if (entry.isFile) {
    const file = await new Promise((resolve, reject) => entry.file(resolve, reject));
    collected.push([prefix + entry.name, file]);
    return;
  }
  const reader = entry.createReader();
  while (true) {
    const batch = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
    if (!batch.length) break;
    for (const child of batch) await walk(child, prefix + entry.name + '/', collected);
  }
}

async function send(pairs) {
  if (!pairs.length) return;
  const body = new FormData();
  for (const [path, file] of pairs) { body.append('files', file); body.append('paths', path); }
  status.textContent = pairs.length + '개 파일을 올리는 중...';
  try {
    const response = await fetch('/admin/input/upload', { method: 'POST', body });
    if (response.status === 413) {
      status.textContent = '한 번에 올릴 수 있는 용량을 넘었습니다.';
      return;
    }
    const data = await response.json();
    const key = data.error ? 'error' : 'message';
    location.href = '/admin/input?' + key + '=' + encodeURIComponent(data.error || data.message);
  } catch (failure) {
    status.textContent = '업로드에 실패했습니다: ' + failure;
  }
}
</script>{% endif %}
""" + _FOOT

INPUT_VIEW_TEMPLATE = _HEAD + """
<section><h2>{{ name }} <span class="muted">{{ file.size_text }} · {{ file.modified.strftime('%Y-%m-%d %H:%M:%S') }}</span></h2>
<div class="toolbar">
<a class="btn" href="/admin/input">목록으로</a>
<a class="btn" href="/admin/input/raw?path={{ name|urlencode }}&amp;download=1">내려받기</a>
{% if allow_input_edit %}<form method="post" action="/admin/input/delete" data-confirm="{{ name }}">
<input type="hidden" name="path" value="{{ name }}"><button class="danger" type="submit">삭제</button></form>{% endif %}
</div>
{% if truncated %}<p class="muted">파일이 커서 앞부분만 보여 줍니다. 잘린 내용을 덮어쓰지 않도록 편집은 막아 두었고, 전체는 내려받아 확인하세요.</p>{% endif %}
{% if file.kind == 'image' %}<div class="preview"><img src="/admin/input/raw?path={{ name|urlencode }}" alt="{{ name }}"></div>
{% elif text is not none %}
{% if allow_input_edit and not truncated %}
<form method="post" action="/admin/input/save">
<input type="hidden" name="path" value="{{ name }}">
<textarea name="text" spellcheck="false">{{ text }}</textarea>
<div class="toolbar" style="margin-top:12px"><button type="submit">저장</button>
<span class="muted">이전 내용은 입력 폴더의 {{ backup_dir }} 안에 남습니다. 저장한 내용은 다음 실행부터 쓰입니다.</span>
</div></form>
{% else %}<div class="log">{{ text }}</div>{% endif %}
{% else %}<p class="muted">텍스트로 볼 수 없는 파일입니다. 내려받아 확인하세요.</p>{% endif %}
</section>
{% if allow_input_edit %}<script>
document.querySelectorAll('form[data-confirm]').forEach(form => form.addEventListener('submit', event => {
  if (!confirm(form.dataset.confirm + ' 을(를) 지웁니다.')) event.preventDefault();
}));
</script>
{% endif %}
""" + _FOOT
