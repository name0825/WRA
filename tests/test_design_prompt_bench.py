import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from wra.core.models import SINGLE_MODEL_ROLES
from scripts import design_prompt_bench as bench
from scripts.design_prompt_bench import ROOT, SAMPLES, digest, prepare


@pytest.fixture
def experiment(tmp_path):
    snapshot = tmp_path / 'snapshot'
    baseline = snapshot / 'baseline'
    prompts = baseline / 'prompts'
    candidate = tmp_path / 'candidate'
    for name in ('designer/system.md', 'generator/system.md', 'creator/system.md', 'evaluator/llm.md'):
        path = prompts / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name, encoding='utf-8')
    shutil.copytree(prompts, candidate)
    config = baseline / 'config'
    config.mkdir()
    for name in ('config.yaml', 'models.yaml'):
        shutil.copy2(ROOT / 'config' / f'{name}.sample', config / name)
    shutil.copy2(ROOT / 'config/frameworks.yaml', config / 'frameworks.yaml')
    models = yaml.safe_load((config / 'models.yaml').read_text(encoding='utf-8'))
    models['models']['single'].update({'enabled': True, 'think': True})
    (config / 'models.yaml').write_text(yaml.safe_dump(models), encoding='utf-8')
    cases = {}
    for name in SAMPLES:
        path = snapshot / 'inputs' / name
        path.mkdir(parents=True)
        filename = 'input.txt' if name == 'brief' else 'index.html'
        (path / filename).write_text('brief' if name == 'brief' else '<title>Sample</title>', encoding='utf-8')
        cases[name] = {'source_type': 'brief' if name == 'brief' else 'html', 'entry': None if name == 'brief' else filename, 'files': {filename: digest(path / filename)}}
    hashes = {'baseline/prompts/' + path.relative_to(prompts).as_posix(): digest(path) for path in prompts.rglob('*') if path.is_file()}
    (snapshot / 'manifest.json').write_text(json.dumps({'cases': cases, 'hashes': hashes}), encoding='utf-8')
    return snapshot, candidate, tmp_path / 'prepared'


def test_preflight_stages_inputs_and_independent_settings(experiment):
    snapshot, candidate, dest = experiment
    result = prepare(snapshot, candidate, dest)
    assert len(result['prepared']) == 12
    assert result['status'] == 'prepared'
    assert result['live_execution_supported']
    for name in SAMPLES:
        a, b = dest / 'A' / name, dest / 'B' / name
        for work in (a, b):
            config = yaml.safe_load((work / 'config/config.yaml').read_text(encoding='utf-8'))
            assert config['input']['mode'] == 'entry'
            assert Path(config['input']['directory']) == work / 'input'
            assert config['creator']['max_pages'] == 1
            assert config['pipeline']['max_iterations'] == 3
            assert config['pipeline']['termination']['patience'] == 0
            assert not config['pipeline']['refine']['enabled']
        assert (a / 'prompts/evaluator/llm.md').read_bytes() == (b / 'prompts/evaluator/llm.md').read_bytes()
    assert json.loads((dest / 'preflight.json').read_text(encoding='utf-8')) == result
    with pytest.raises(FileExistsError):
        prepare(snapshot, candidate, dest)


def test_evaluator_runs_same_model_with_thinking_off(experiment):
    from wra.core.config import load_raw
    from wra.core.models import WRAConfig
    snapshot, candidate, dest = experiment
    result = prepare(snapshot, candidate, dest)
    assert result['evaluator_think'] is False
    for job in result['prepared']:
        work = Path(job['workdir'])
        overrides = {name: yaml.safe_load((work / 'config' / name).read_text(encoding='utf-8')) for name in ('config.yaml', 'models.yaml', 'frameworks.yaml')}
        resolved = WRAConfig.model_validate(load_raw(overrides=overrides))
        model = overrides['models.yaml']['models']['single']['name']
        timeout = overrides['models.yaml']['models']['single'].get('timeout')
        assert [(spec.name, spec.think, spec.timeout) for spec in resolved.models.evaluator] == [(model, False, timeout)]
        for role in SINGLE_MODEL_ROLES:
            specs = getattr(resolved.models, role)
            assert len(specs) == 1
            assert (specs[0].name, specs[0].timeout) == (model, timeout)
            if role != 'evaluator':
                assert specs[0].think is True


def test_preflight_rejects_changed_input_and_common_evaluator(experiment):
    snapshot, candidate, dest = experiment
    target = snapshot / 'inputs/bakery/index.html'
    original = target.read_bytes()
    target.write_bytes(original + b'changed')
    with pytest.raises(ValueError, match='frozen input'):
        prepare(snapshot, candidate, dest)
    assert not dest.exists()
    target.write_bytes(original)
    evaluator = candidate / 'evaluator/llm.md'
    evaluator.write_bytes(evaluator.read_bytes() + b'changed')
    with pytest.raises(ValueError, match='outside the permitted'):
        prepare(snapshot, candidate, dest)
    assert not dest.exists()


@pytest.mark.parametrize('change', [{'enabled': False}, {'think': None}])
def test_preflight_requires_enabled_single_with_explicit_think(experiment, change):
    snapshot, candidate, dest = experiment
    path = snapshot / 'baseline/config/models.yaml'
    models = yaml.safe_load(path.read_text(encoding='utf-8'))
    single = models['models']['single']
    if 'think' in change:
        del single['think']
    else:
        single.update(change)
    path.write_text(yaml.safe_dump(models), encoding='utf-8')
    with pytest.raises(ValueError, match='models.single must be enabled'):
        prepare(snapshot, candidate, dest)
    assert not dest.exists()


def test_preflight_hashes_runtime_configs_without_confusing_sample_files(experiment):
    snapshot, candidate, destination = experiment
    manifest = bench.read_json(snapshot / 'manifest.json')
    config = snapshot / 'baseline/config'
    manifest['baseline_config_hashes'] = bench.inventory(config)
    bench.write_json(snapshot / 'manifest.json', manifest)
    (config / 'config.yaml.sample').write_text('unused example', encoding='utf-8')
    prepare(snapshot, candidate, destination)
    (config / 'models.yaml').write_text('changed runtime', encoding='utf-8')
    with pytest.raises(ValueError, match='Baseline config'):
        prepare(snapshot, candidate, destination.with_name('other'))


def completed_runner(destination, calls, fail=None):
    def run(command, **kwargs):
        bundle, case, token = command[command.index('--bundle') + 1], command[command.index('--case') + 1], command[-1]
        key = f'{bundle}/{case}'
        calls.append(key)
        assert command[:3] == [bench.sys.executable, str(Path(bench.__file__).resolve()), 'worker']
        assert kwargs['cwd'] == destination / bundle / case
        assert kwargs['env']['PYTHONPATH'].split(bench.os.pathsep)[0] == str(ROOT / 'src')
        code = int(key == fail)
        bench.write_json(kwargs['cwd'] / 'receipt.json', {'token': token, 'exit_code': code})
        return SimpleNamespace(returncode=code)
    return run


def test_pilot_remaining_gate_and_resume_never_repeat_cases(experiment):
    snapshot, candidate, destination = experiment
    prepare(snapshot, candidate, destination)
    calls = []
    runner = completed_runner(destination, calls)
    with pytest.raises(RuntimeError, match='successful pilot'):
        bench.execute(destination, phase='remaining', pilot_reviewed=True, runner=runner)
    pilot = bench.execute(destination, runner=runner)
    assert calls == ['A/bakery', 'B/bakery', 'A/reference-polished', 'B/reference-polished']
    assert sum(job['reserved_iterations'] for job in pilot['jobs'].values()) == 12
    with pytest.raises(RuntimeError, match='--resume'):
        bench.execute(destination, runner=runner)
    bench.execute(destination, resume=True, runner=runner)
    assert len(calls) == 4
    with pytest.raises(RuntimeError, match='--pilot-reviewed'):
        bench.execute(destination, phase='remaining', resume=True, runner=runner)
    result = bench.execute(destination, phase='remaining', resume=True, pilot_reviewed=True, runner=runner)
    assert len(calls) == 12
    assert sum(job['reserved_iterations'] for job in result['jobs'].values()) == 36


def test_failure_is_charged_and_resume_skips_it(experiment):
    snapshot, candidate, destination = experiment
    prepare(snapshot, candidate, destination)
    calls = []
    runner = completed_runner(destination, calls, fail='A/bakery')
    with pytest.raises(RuntimeError, match='A/bakery failed'):
        bench.execute(destination, runner=runner)
    ledger = bench.read_json(destination / 'ledger.json')
    assert ledger['jobs']['A/bakery']['reserved_iterations'] == 3
    assert ledger['jobs']['A/bakery']['status'] == 'failed'
    bench.execute(destination, resume=True, runner=runner)
    assert calls.count('A/bakery') == 1
    with pytest.raises(RuntimeError, match='successful pilot'):
        bench.execute(destination, phase='remaining', resume=True, pilot_reviewed=True, runner=runner)


def test_crash_blocks_until_explicit_abandon_and_stale_worker_cannot_start(experiment, monkeypatch):
    snapshot, candidate, destination = experiment
    prepare(snapshot, candidate, destination)
    def crash(*args, **kwargs):
        raise OSError('parent interrupted after launch')
    with pytest.raises(OSError, match='interrupted'):
        bench.execute(destination, runner=crash)
    ledger = bench.read_json(destination / 'ledger.json')
    token = ledger['jobs']['A/bakery']['token']
    with pytest.raises(RuntimeError, match='incomplete'):
        bench.execute(destination, resume=True, runner=crash)
    with bench.exclusive(destination / 'worker.lock'):
        with pytest.raises(RuntimeError, match='already active'):
            bench.abandon(destination, 'A', 'bakery')
    bench.abandon(destination, 'A', 'bakery')
    monkeypatch.chdir(destination / 'A/bakery')
    with pytest.raises(RuntimeError, match='no longer active'):
        bench.worker(destination, 'A', 'bakery', token)
    calls = []
    result = bench.execute(destination, resume=True, runner=completed_runner(destination, calls))
    assert 'A/bakery' not in calls
    assert sum(job['reserved_iterations'] for job in result['jobs'].values()) == 12


def test_worker_receipt_recovers_parent_crash_without_reexecution(experiment):
    snapshot, candidate, destination = experiment
    prepare(snapshot, candidate, destination)
    calls = []
    normal = completed_runner(destination, calls)
    def crash_after_receipt(*args, **kwargs):
        normal(*args, **kwargs)
        raise OSError('parent died after child finished')
    with pytest.raises(OSError):
        bench.execute(destination, runner=crash_after_receipt)
    bench.execute(destination, resume=True, runner=normal)
    assert calls.count('A/bakery') == 1


def test_receipt_is_not_reconciled_until_worker_releases_lock(experiment):
    snapshot, candidate, destination = experiment
    prepare(snapshot, candidate, destination)
    calls = []
    normal = completed_runner(destination, calls)
    def crash_after_receipt(*args, **kwargs):
        normal(*args, **kwargs)
        raise OSError('parent died while worker is finishing')
    with pytest.raises(OSError):
        bench.execute(destination, runner=crash_after_receipt)
    with bench.exclusive(destination / 'worker.lock'):
        with pytest.raises(RuntimeError, match='already active'):
            bench.execute(destination, resume=True, runner=normal)
        ledger = bench.read_json(destination / 'ledger.json')
        assert list(ledger['jobs']) == ['A/bakery']
        assert ledger['jobs']['A/bakery']['status'] == 'running'
        assert sum(job['reserved_iterations'] for job in ledger['jobs'].values()) == 3
        assert calls == ['A/bakery']
    bench.abandon(destination, 'A', 'bakery')
    assert bench.read_json(destination / 'ledger.json')['jobs']['A/bakery']['status'] == 'succeeded'
    bench.execute(destination, resume=True, runner=normal)
    assert calls.count('A/bakery') == 1
    assert calls[1] == 'B/bakery'


def test_run_rejects_staged_changes_and_concurrent_orchestrator(experiment):
    snapshot, candidate, destination = experiment
    prepare(snapshot, candidate, destination)
    with bench.exclusive(destination / 'orchestrator.lock'):
        with pytest.raises(RuntimeError, match='already active'):
            bench.execute(destination)
    path = destination / 'B/brief/config/config.yaml'
    path.write_text(path.read_text(encoding='utf-8') + '\nchanged: true\n', encoding='utf-8')
    with pytest.raises(ValueError, match='config changed'):
        bench.execute(destination)
    assert not bench.read_json(destination / 'ledger.json')['jobs']


def test_transport_observer_records_attempts_and_restores_clients(tmp_path, monkeypatch):
    import ollama
    from wra.llm.ollama import OllamaClient
    calls = []
    def fake_http(self, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise ConnectionError('retry')
        return SimpleNamespace(response='ok', prompt_eval_count=2, eval_count=1)
    def fake_logical(self, prompt):
        client = ollama.Client()
        try:
            client.generate(model='offline', prompt=prompt)
        except ConnectionError:
            pass
        return client.generate(model='offline', prompt=prompt)
    monkeypatch.setattr(ollama.Client, 'generate', fake_http)
    monkeypatch.setattr(OllamaClient, 'generate', fake_logical)
    path = tmp_path / 'calls.jsonl'
    with bench.observe_calls(path):
        OllamaClient('http://localhost').generate('sample')
    events = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    assert [event['event'] for event in events] == ['started', 'failed', 'started', 'finished']
    assert len({event['logical_call']['id'] for event in events}) == 1
    assert events[-1]['prompt_tokens'] == 2
    assert OllamaClient.generate is fake_logical
    assert ollama.Client.generate is fake_http
    summary = bench.call_summary(path)
    assert summary['http_generation_attempts'] == 2
    assert summary['logical_pipeline_calls'] == 1
    assert summary['transport_resends'] == 1
    assert summary['failed_http_attempts'] == 1
    assert summary['unfinished_http_attempts'] == 0


@pytest.mark.parametrize('bundle', ['A', 'B'])
@pytest.mark.parametrize('visual_outcome', ['disabled', 'valid', 'length'])
def test_prepared_source_reaches_actual_evaluator_with_offline_client(experiment, monkeypatch, bundle, visual_outcome):
    from test_llm_evaluator import _evaluation_response, make_spec
    from wra.core.inputs import discover
    from wra.core.iteration import IterationEngine
    from wra.core.models import FeatureModel, Heading, ModelSpec, WRAConfig
    from wra.core.preparation import InputPreparer
    from wra.evaluator.llm import LLMEvaluator
    from wra.evaluator.visual_check import VisualInspector
    from wra.browser.models import BrowserEvaluationResult, BrowserViewportResult
    from wra.core.config import load_raw
    snapshot, candidate, destination = experiment
    prepare(snapshot, candidate, destination)
    work = destination / bundle / 'bakery'
    monkeypatch.chdir(work)
    config = WRAConfig.model_validate(load_raw())
    visual_enabled = visual_outcome != 'disabled'
    config = config.model_copy(update={'browser': config.browser.model_copy(update={'enabled': visual_enabled}), 'input': config.input.model_copy(update={'vision': config.input.vision.model_copy(update={'enabled': False})})})
    config = config.model_copy(update={'models': config.models.model_copy(update={role: [ModelSpec(name='offline')] for role in ('designer', 'generator', 'evaluator')})})
    features = FeatureModel()
    features.metadata.title = 'Prepared sample'
    features.content.headings = [Heading(level=1, text='Grounded heading')]
    features.content.paragraphs = ['Grounded sample content.']
    class Analyzer:
        def analyze(self, html):
            assert '<title>Sample</title>' in html
            return features
    monkeypatch.setattr(InputPreparer, '_page_impression', lambda *args: None)
    pages = InputPreparer(config=config, analyzer=Analyzer()).prepare(discover(config.input.directory), work / 'offline_output')
    assert len(pages) == 1
    spec = make_spec()
    class Designer:
        def plan(self, *args, **kwargs):
            return spec
        def revise(self, *args, **kwargs):
            return spec
    html = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Prepared sample</title></head><body><main><h1>Grounded heading</h1><p>Grounded sample content.</p></main></body></html>'
    class Generator:
        def generate(self, received, *args, **kwargs):
            assert received is pages[0].features
            return html
    prompts = []
    class Client:
        def generate(self, prompt, **kwargs):
            prompts.append(prompt)
            return _evaluation_response()
    evaluator = LLMEvaluator(Client(), 'offline', prompt_path=work / 'prompts/evaluator/llm.md', content_freedom='madmax')
    visual_calls = []
    class VisualClient:
        def generate(self, **kwargs):
            visual_calls.append(kwargs)
            if visual_outcome == 'length':
                raise RuntimeError('LLM generation stopped due to output token limit (Ollama done_reason=length)')
            return '{"defects": []}'
    browser_iterations = []
    class Browser:
        def evaluate(self, *, output_dir, iteration, **kwargs):
            from PIL import Image
            browser_iterations.append(iteration)
            output_dir.mkdir(parents=True, exist_ok=True)
            screenshot = output_dir / 'offline.png'
            Image.new('RGB', (2, 2), 'white').save(screenshot)
            return BrowserEvaluationResult(enabled=True, viewports=[BrowserViewportResult(name='desktop', width=1440, height=900, screenshot_path=str(screenshot))])
    inspector = VisualInspector(VisualClient(), prompt_path=ROOT / 'prompts/evaluator/visual_check.md')
    engine = IterationEngine(config, Designer(), Generator(), evaluator, browser_evaluator=Browser() if visual_enabled else None, visual_inspector=inspector if visual_enabled else None)
    path = work / 'visual_boundary.jsonl'
    with bench.observe_visual_inspections(path, required=visual_enabled):
        if visual_outcome == 'length':
            with pytest.raises(RuntimeError, match='Required visual inspection did not complete'):
                engine.run_pages(pages)
            assert prompts == []
            assert len(visual_calls) == 1
            assert browser_iterations == [1]
            summary = bench.call_summary(path)
            assert summary['failed_visual_inspections'] == 1
            assert summary['failed_http_attempts'] == 0
            return
        results = engine.run_pages(pages)
    assert len(results) == 1
    assert len(prompts) == 3
    assert all('Grounded heading' in prompt and 'Grounded sample content.' in prompt for prompt in prompts)
    assert (work / 'offline_output/result.json').is_file()
    assert len(list((work / 'offline_output/iterations').glob('iter*/data/*/evaluation.json'))) == 3
    if visual_enabled:
        assert len(visual_calls) == 3
        assert bench.call_summary(path)['performed_visual_inspections'] == 3


@pytest.mark.parametrize('responses', [['{"defects": []}'], ['not json', '{"defects": []}']])
def test_actual_visual_empty_findings_and_recovered_parse_are_performed(tmp_path, responses):
    from wra.evaluator.visual_check import VisualInspector
    from wra.browser.models import BrowserEvaluationResult, BrowserViewportResult
    class Client:
        def generate(self, **kwargs):
            return responses.pop(0)
    screenshot = tmp_path / 'desktop.png'
    screenshot.write_bytes(b'offline')
    browser = BrowserEvaluationResult(enabled=True, viewports=[BrowserViewportResult(name='desktop', width=1440, height=900, screenshot_path=str(screenshot))])
    inspector = VisualInspector(Client(), prompt_path=ROOT / 'prompts/evaluator/visual_check.md')
    path = tmp_path / 'calls.jsonl'
    with bench.observe_visual_inspections(path, required=True):
        assert inspector.inspect(browser, '<html></html>', model='offline') == []
    assert not responses
    assert bench.call_summary(path)['performed_visual_inspections'] == 1
    assert bench.call_summary(path)['failed_visual_inspections'] == 0


@pytest.mark.parametrize('failure', ['invalid_response', 'missing_screenshots', 'missing_model', 'no_invocation'])
def test_actual_visual_unavailable_or_skipped_inspection_cannot_reach_evaluator(tmp_path, monkeypatch, failure):
    from wra.evaluator.visual_check import VisualInspector
    from wra.evaluator.llm import LLMEvaluator
    from wra.browser.models import BrowserEvaluationResult, BrowserViewportResult
    calls = []
    class Client:
        def generate(self, **kwargs):
            calls.append(kwargs)
            return '{"defects": null}'
    evaluated = []
    monkeypatch.setattr(LLMEvaluator, 'evaluate', lambda *args, **kwargs: evaluated.append(True))
    screenshot = tmp_path / 'desktop.png'
    screenshot.write_bytes(b'offline')
    browser = BrowserEvaluationResult(enabled=True, viewports=[BrowserViewportResult(name='desktop', width=1440, height=900, screenshot_path=str(screenshot) if failure != 'missing_screenshots' else None)])
    inspector = VisualInspector(Client(), prompt_path=ROOT / 'prompts/evaluator/visual_check.md')
    path = tmp_path / 'calls.jsonl'
    with bench.observe_visual_inspections(path, required=True):
        if failure != 'no_invocation':
            with pytest.raises(RuntimeError, match='Required visual inspection'):
                inspector.inspect(browser, '<html></html>', model='' if failure == 'missing_model' else 'offline')
        with pytest.raises(RuntimeError, match='not performed before evaluation'):
            LLMEvaluator.evaluate(None)
    assert evaluated == []
    if failure != 'invalid_response':
        assert not calls
    assert bench.call_summary(path)['failed_visual_inspections'] >= 1


def test_subprocess_worker_rejects_missing_reservation_without_external_calls(experiment):
    snapshot, candidate, destination = experiment
    prepare(snapshot, candidate, destination)
    work = destination / 'A/bakery'
    env = bench.os.environ.copy()
    env['PYTHONPATH'] = str(ROOT / 'src')
    result = bench.subprocess.run([bench.sys.executable, str(Path(bench.__file__).resolve()), 'worker', str(destination), '--bundle', 'A', '--case', 'bakery', '--token', 'missing'], cwd=work, env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'reservation is no longer active' in result.stderr
    assert not (work / 'calls.jsonl').exists()
    assert not (work / 'output').exists()


@pytest.mark.parametrize('evidence_failure', ['browser', 'visual'])
def test_worker_requires_full_saved_evidence_and_preserves_failed_case_budget(experiment, monkeypatch, evidence_failure):
    snapshot, candidate, destination = experiment
    config_path = snapshot / 'baseline/config/config.yaml'
    config_data = yaml.safe_load(config_path.read_text(encoding='utf-8'))
    config_data['browser']['enabled'] = True
    config_data['browser']['visual_inspection'] = True
    config_path.write_text(yaml.safe_dump(config_data), encoding='utf-8')
    prepare(snapshot, candidate, destination)
    def fake_cli():
        work = Path.cwd()
        config = yaml.safe_load((work / 'config/config.yaml').read_text(encoding='utf-8'))
        run = work / 'output/run_offline'
        run.mkdir(parents=True)
        (run / 'index.html').write_text('<html></html>', encoding='utf-8')
        bench.write_json(run / 'result.json', {'pages': {'index': {'best_iteration': 1}}, 'entry': 'index'})
        for iteration in range(1, 4):
            data = run / 'iterations' / f'iter{iteration}' / 'data/index'
            data.mkdir(parents=True)
            for name, value in [('design.json', {}), ('evaluation.json', {'overall_score': 7}), ('validation.json', {'valid': True})]:
                bench.write_json(data / name, value)
            viewports = []
            for viewport in config['browser']['viewports']:
                shot = data / 'browser' / (viewport['name'] + '.png')
                shot.parent.mkdir(exist_ok=True)
                shot.write_bytes(b'offline')
                viewports.append({**viewport, 'screenshot_path': str(shot)})
            bench.write_json(data / 'browser.json', {'enabled': True, 'viewports': viewports})
            if not (evidence_failure == 'visual' and work.parent.name == 'B' and iteration == 3):
                bench.append_event(work / 'calls.jsonl', {'event': 'visual_inspection', 'iteration': iteration, 'slug': 'index', 'performed': True})
        if evidence_failure == 'browser' and work.name == 'bakery' and work.parent.name == 'B':
            bench.write_json(run / 'iterations/iter3/data/index/browser.json', {'enabled': True, 'viewports': []})
    monkeypatch.setattr(bench, 'execute_cli', fake_cli)
    def run_worker(command, **kwargs):
        with monkeypatch.context() as isolated:
            isolated.chdir(kwargs['cwd'])
            return SimpleNamespace(returncode=bench.worker(destination, command[command.index('--bundle') + 1], command[command.index('--case') + 1], command[-1]))
    with pytest.raises(RuntimeError, match='B/bakery failed'):
        bench.execute(destination, runner=run_worker)
    ledger = bench.read_json(destination / 'ledger.json')
    assert ledger['jobs']['A/bakery']['status'] == 'succeeded'
    assert ledger['jobs']['B/bakery']['status'] == 'failed'
    assert sum(job['reserved_iterations'] for job in ledger['jobs'].values()) == 6
    assert ('lacks complete configured browser evidence' if evidence_failure == 'browser' else 'three completed required visual inspections') in ledger['jobs']['B/bakery']['result']['error']
    assert ledger['jobs']['A/bakery']['result']['observed_iterations'] == 3


def test_required_visual_default_applies_when_work_config_omits_key(experiment, monkeypatch):
    snapshot, candidate, destination = experiment
    config_path = snapshot / 'baseline/config/config.yaml'
    config_data = yaml.safe_load(config_path.read_text(encoding='utf-8'))
    config_data['browser']['enabled'] = True
    config_data['browser'].pop('visual_inspection', None)
    config_path.write_text(yaml.safe_dump(config_data), encoding='utf-8')
    prepare(snapshot, candidate, destination)
    def fake_cli():
        run = Path.cwd() / 'output/run_offline'
        run.mkdir(parents=True)
        (run / 'index.html').write_text('<html></html>', encoding='utf-8')
        bench.write_json(run / 'result.json', {'pages': {'index': {'best_iteration': 1}}, 'entry': 'index'})
    monkeypatch.setattr(bench, 'execute_cli', fake_cli)
    def run_worker(command, **kwargs):
        with monkeypatch.context() as isolated:
            isolated.chdir(kwargs['cwd'])
            return SimpleNamespace(returncode=bench.worker(destination, command[command.index('--bundle') + 1], command[command.index('--case') + 1], command[-1]))
    with pytest.raises(RuntimeError):
        bench.execute(destination, runner=run_worker)
    ledger = bench.read_json(destination / 'ledger.json')
    assert ledger['jobs']['A/bakery']['status'] == 'failed'
    assert 'three completed required visual inspections' in ledger['jobs']['A/bakery']['result']['error']
