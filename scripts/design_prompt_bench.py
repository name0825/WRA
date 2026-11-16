import argparse
import contextvars
import hashlib
import inspect
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import yaml

from wra.core.config import load_raw
from wra.core.inputs import discover
from wra.core.models import SINGLE_MODEL_ROLES, WRAConfig

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / 'bench' / 'design_prompt_comparison'
CANDIDATE = ROOT / 'benchmarks' / 'design_prompt_comparison' / 'candidate' / 'prompts'
SAMPLES = ('bakery', 'reference-polished', 'saas-product', 'research-lab', 'portfolio', 'brief')
CHANGED_PROMPTS = {'designer/system.md', 'generator/system.md', 'creator/system.md'}
PILOT = SAMPLES[:2]


def write_json(path, value):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def append_event(path, event):
    with path.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


@contextmanager
def exclusive(path):
    with path.open('a+b') as stream:
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError(f'Experiment is already active: {path}') from error
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def runtime_hashes():
    paths = sorted((ROOT / 'src').rglob('*.py')) + [Path(__file__).resolve(), ROOT / 'pyproject.toml']
    return {path.relative_to(ROOT).as_posix(): digest(path) for path in paths}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(root):
    return {p.relative_to(root).as_posix(): digest(p) for p in sorted(root.rglob('*')) if p.is_file()}


def prepare(snapshot, candidate, destination, seed=42, cases=None):
    snapshot, candidate, destination = map(lambda p: Path(p).resolve(), (snapshot, candidate, destination))
    cases = list(SAMPLES if cases is None else cases)
    if not cases or len(set(cases)) != len(cases) or any(name not in SAMPLES for name in cases):
        raise ValueError(f'cases must be a non-empty list of distinct names from {SAMPLES}')
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError('seed must be an integer')
    manifest = json.loads((snapshot / 'manifest.json').read_text(encoding='utf-8'))
    baseline = snapshot / 'baseline'
    baseline_prompts = inventory(baseline / 'prompts')
    expected_prompts = {k.removeprefix('baseline/prompts/'): v for k, v in manifest['hashes'].items()}
    candidate_prompts = inventory(candidate)
    if baseline_prompts != expected_prompts:
        raise ValueError('Baseline prompts differ from the frozen manifest')
    if 'baseline_config_hashes' in manifest:
        config_hashes = {name: digest(baseline / 'config' / name) for name in ('config.yaml', 'models.yaml', 'frameworks.yaml')}
        if config_hashes != manifest['baseline_config_hashes']:
            raise ValueError('Baseline config differs from the frozen manifest')
    if 'shared_runtime_correction' in manifest:
        correction = manifest['shared_runtime_correction']
        if digest(ROOT / correction['file']) != correction['sha256']:
            raise ValueError('Shared runtime correction differs from the frozen manifest')
    if baseline_prompts.keys() != candidate_prompts.keys():
        raise ValueError('Candidate prompt inventory differs from baseline')
    if any(candidate_prompts[name] != value for name, value in baseline_prompts.items() if name not in CHANGED_PROMPTS):
        raise ValueError('Candidate changed a prompt outside the permitted three files')
    if destination.exists():
        raise FileExistsError(f'Destination already exists: {destination}')
    sources = {}
    for name in cases:
        source = snapshot / 'inputs' / name
        expected = manifest['cases'][name]
        files = {f.relative: digest(f.path) for f in discover(source).files}
        if files != expected['files']:
            raise ValueError(f'{name}: frozen input differs from the manifest')
        bundle = discover(source)
        entry = bundle.entry.relative if bundle.entry else None
        if entry != expected['entry'] or bundle.creates != (expected['source_type'] == 'brief'):
            raise ValueError(f'{name}: input discovery differs from the manifest')
        sources[name] = source
    raw_config = yaml.safe_load((baseline / 'config/config.yaml').read_text(encoding='utf-8'))
    configs = {name: yaml.safe_load((baseline / 'config' / name).read_text(encoding='utf-8')) for name in ('models.yaml', 'frameworks.yaml')}
    configs['models.yaml']['ollama']['seed'] = seed
    single = configs['models.yaml']['models']['single']
    if single.get('enabled') is not True or 'think' not in single:
        raise ValueError('models.single must be enabled and set think explicitly')
    single['exclude_roles'] = [*(single.get('exclude_roles') or []), 'evaluator']
    configs['models.yaml']['models']['evaluator'] = [{'name': single['name'], 'think': False, 'timeout': single.get('timeout')}]
    destination.mkdir(parents=True)
    prepared = []
    for bundle_name, prompt_dir in (('A', baseline / 'prompts'), ('B', candidate)):
        for name, source in sources.items():
            work = destination / bundle_name / name
            (work / 'config').mkdir(parents=True)
            shutil.copytree(prompt_dir, work / 'prompts')
            shutil.copytree(source, work / 'input')
            config = yaml.safe_load(yaml.safe_dump(raw_config))
            config['input'].update({'directory': str(work / 'input'), 'mode': 'entry', 'entry': None})
            config['creator']['max_pages'] = 1
            config['content']['freedom'] = 'madmax'
            config['pipeline']['max_iterations'] = 3
            config['pipeline']['evaluation_threshold'] = 10.1
            config['pipeline']['termination'].update({'enabled': True, 'patience': 0})
            config['pipeline']['refine']['enabled'] = False
            config['pipeline']['output']['save_mode'] = 'all'
            (work / 'config/config.yaml').write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding='utf-8')
            for config_name in configs:
                (work / 'config' / config_name).write_text(yaml.safe_dump(configs[config_name], allow_unicode=True, sort_keys=False), encoding='utf-8')
            overrides = {'config.yaml': config, **configs}
            resolved = WRAConfig.model_validate(load_raw(overrides=overrides))
            actual = discover(resolved.input.directory, entry=resolved.input.entry, asset_directory=resolved.input.asset_directory)
            if resolved.input.mode != 'entry' or resolved.creator.max_pages != 1 or resolved.pipeline.max_iterations != 3 or resolved.pipeline.refine.enabled or resolved.pipeline.termination.patience != 0 or resolved.pipeline.evaluation_threshold <= 10:
                raise ValueError(f'{bundle_name}/{name}: invalid resolved settings')
            evaluator = resolved.models.evaluator
            if len(evaluator) != 1 or evaluator[0].think is not False or any(spec.name != single['name'] or spec.timeout != single.get('timeout') for role in SINGLE_MODEL_ROLES for spec in getattr(resolved.models, role)) or any(spec.think != single['think'] for role in SINGLE_MODEL_ROLES if role != 'evaluator' for spec in getattr(resolved.models, role)):
                raise ValueError(f'{bundle_name}/{name}: invalid evaluator settings')
            if (actual.entry.relative if actual.entry else None) != manifest['cases'][name]['entry'] or actual.creates != (name == 'brief'):
                raise ValueError(f'{bundle_name}/{name}: staged input discovery failed')
            prepared.append({'bundle': bundle_name, 'case': name, 'workdir': str(work), 'prompt_hashes': inventory(work / 'prompts'), 'input_hashes': {f.relative: digest(f.path) for f in actual.files}, 'config_hashes': inventory(work / 'config')})
    budget = len(cases) * 6
    record = {'status': 'prepared', 'live_execution_supported': True, 'evaluator_think': False, 'seed': seed, 'cases': cases, 'max_iterations': budget, 'first_batch_max_iterations': 12 if len(cases) == len(SAMPLES) else budget, 'prepared': prepared, 'runtime_hashes': runtime_hashes(), 'manifest_hash': digest(snapshot / 'manifest.json'), 'accounting': 'Reserve all three iterations per case permanently, including failures and interrupted cases; no automatic reruns.'}
    write_json(destination / 'preflight.json', record)
    write_json(destination / 'ledger.json', {'preflight_hash': digest(destination / 'preflight.json'), 'jobs': {}})
    return record


def verify(destination):
    record = read_json(destination / 'preflight.json')
    ledger = read_json(destination / 'ledger.json')
    if ledger['preflight_hash'] != digest(destination / 'preflight.json'):
        raise ValueError('Preflight record changed after preparation')
    if runtime_hashes() != record['runtime_hashes']:
        raise ValueError('Shared source runtime changed after preparation')
    for job in record['prepared']:
        work = Path(job['workdir'])
        if work != destination / job['bundle'] / job['case']:
            raise ValueError('Prepared work directory does not belong to this experiment')
        for folder, key in (('prompts', 'prompt_hashes'), ('input', 'input_hashes'), ('config', 'config_hashes')):
            if inventory(work / folder) != job[key]:
                raise ValueError(f"{job['bundle']}/{job['case']}: {folder} changed after preparation")
    if sum(job['reserved_iterations'] for job in ledger['jobs'].values()) > record['max_iterations']:
        raise ValueError('Iteration budget exceeded')
    return record, ledger


def reconcile(destination, ledger):
    with exclusive(destination / 'worker.lock'):
        for key, job in ledger['jobs'].items():
            if job['status'] != 'running':
                continue
            receipt = destination / job['bundle'] / job['case'] / 'receipt.json'
            if not receipt.exists():
                raise RuntimeError(f'{key} is incomplete; wait for its worker or explicitly abandon the case; its three iterations stay charged')
            result = read_json(receipt)
            if result['token'] != job['token']:
                raise ValueError(f'{key}: worker receipt does not match reservation')
            job.update(status='succeeded' if result['exit_code'] == 0 else 'failed', result=result)
        write_json(destination / 'ledger.json', ledger)


def execute(destination, phase='pilot', resume=False, pilot_reviewed=False, runner=subprocess.run):
    destination = Path(destination).resolve()
    with exclusive(destination / 'orchestrator.lock'):
        record, ledger = verify(destination)
        if ledger['jobs'] and not resume:
            raise RuntimeError('Experiment has prior reservations; use --resume to skip consumed cases')
        recorded = record.get('cases', list(SAMPLES))
        subset = len(recorded) < len(SAMPLES)
        if subset and phase != 'all':
            raise RuntimeError("Subset experiments run only with phase 'all'")
        if not subset and phase == 'all':
            raise RuntimeError("Phase 'all' requires a subset experiment")
        reconcile(destination, ledger)
        if phase == 'remaining':
            pilot_keys = [f'{bundle}/{name}' for name in PILOT for bundle in ('A', 'B')]
            if not pilot_reviewed or any(ledger['jobs'].get(key, {}).get('status') != 'succeeded' for key in pilot_keys):
                raise RuntimeError('Remaining cases require four successful pilot cases and --pilot-reviewed')
        names = recorded if phase == 'all' else PILOT if phase == 'pilot' else SAMPLES[2:]
        for name in names:
            for bundle in ('A', 'B'):
                key = f'{bundle}/{name}'
                if key in ledger['jobs']:
                    continue
                _, current = verify(destination)
                if current != ledger:
                    raise ValueError('Ledger changed while experiment was active')
                used = sum(job['reserved_iterations'] for job in ledger['jobs'].values())
                if used + 3 > record['max_iterations']:
                    raise RuntimeError('No iteration budget remains')
                if phase == 'pilot' and sum(job['reserved_iterations'] for job in ledger['jobs'].values() if job['case'] in PILOT) + 3 > record['first_batch_max_iterations']:
                    raise RuntimeError('Pilot iteration budget exceeded')
                job = {'bundle': bundle, 'case': name, 'reserved_iterations': 3, 'status': 'running', 'token': uuid.uuid4().hex, 'reserved_at': time.time()}
                ledger['jobs'][key] = job
                write_json(destination / 'ledger.json', ledger)
                work = destination / bundle / name
                if any((work / folder).exists() for folder in ('output', 'calls.jsonl', 'receipt.json', 'run.out')):
                    raise RuntimeError(f'{key}: output already exists; reservation remains consumed')
                command = [sys.executable, str(Path(__file__).resolve()), 'worker', str(destination), '--bundle', bundle, '--case', name, '--token', job['token']]
                env = os.environ.copy()
                env['PYTHONPATH'] = str(ROOT / 'src') + os.pathsep + env.get('PYTHONPATH', '')
                with (work / 'run.out').open('x', encoding='utf-8') as output:
                    completed = runner(command, cwd=work, env=env, stdout=output, stderr=subprocess.STDOUT)
                reconcile(destination, ledger)
                if completed.returncode or job['status'] != 'succeeded':
                    raise RuntimeError(f'{key} failed; inspect run.out/receipt.json before an explicit --resume; no case will be rerun')
        return ledger


def abandon(destination, bundle, case):
    destination = Path(destination).resolve()
    with exclusive(destination / 'orchestrator.lock'):
        _, ledger = verify(destination)
        with exclusive(destination / 'worker.lock'):
            key = f'{bundle}/{case}'
            job = ledger['jobs'].get(key)
            if job is None or job['status'] != 'running':
                raise ValueError('Only an incomplete reservation can be abandoned')
            if not (destination / bundle / case / 'receipt.json').exists():
                job['status'] = 'abandoned'
                job['abandoned_at'] = time.time()
                write_json(destination / 'ledger.json', ledger)
                return ledger
        reconcile(destination, ledger)
        return ledger


@contextmanager
def observe_calls(path):
    import ollama
    from wra.llm.ollama import OllamaClient
    logical = contextvars.ContextVar('bench_logical_call', default=None)
    original_logical, original_http = OllamaClient.generate, ollama.Client.generate

    def append(event):
        append_event(path, event)

    def generate(client, *args, **kwargs):
        frame = inspect.currentframe().f_back
        role = 'unknown'
        attempt = None
        function = None
        while frame:
            module = frame.f_globals.get('__name__', '')
            if module.startswith('wra.') and not module.startswith('wra.llm.'):
                role = module
                function = frame.f_code.co_name
                attempt = frame.f_locals.get('attempt')
                attempt = attempt if isinstance(attempt, int) else None
                break
            frame = frame.f_back
        del frame
        token = logical.set({'id': uuid.uuid4().hex, 'role': role, 'function': function, 'attempt_local': attempt})
        try:
            return original_logical(client, *args, **kwargs)
        finally:
            logical.reset(token)

    def http_generate(client, *args, **kwargs):
        started = time.monotonic()
        event = {'id': uuid.uuid4().hex, 'logical_call': logical.get(), 'started_at': time.time(), 'model': kwargs.get('model', args[0] if args else None), 'prompt_chars': len(kwargs.get('prompt') or ''), 'prompt_sha256': hashlib.sha256((kwargs.get('prompt') or '').encode()).hexdigest(), 'options': kwargs.get('options'), 'probe': inspect.currentframe().f_back.f_code.co_name == '_wait_for_recovery'}
        append({**event, 'event': 'started'})
        try:
            response = original_http(client, *args, **kwargs)
        except BaseException as error:
            append({**event, 'event': 'failed', 'elapsed_seconds': time.monotonic() - started, 'error': type(error).__name__ + ': ' + str(error)})
            raise
        append({**event, 'event': 'finished', 'elapsed_seconds': time.monotonic() - started, 'prompt_tokens': getattr(response, 'prompt_eval_count', None), 'eval_tokens': getattr(response, 'eval_count', None), 'done_reason': getattr(response, 'done_reason', None)})
        return response

    OllamaClient.generate, ollama.Client.generate = generate, http_generate
    try:
        yield
    finally:
        OllamaClient.generate, ollama.Client.generate = original_logical, original_http


def iteration_context():
    frame = inspect.currentframe().f_back
    try:
        while frame:
            if frame.f_globals.get('__name__') == 'wra.core.iteration' and frame.f_code.co_name == '_run_one_iteration':
                return frame.f_locals.get('iteration'), frame.f_locals.get('slug')
            frame = frame.f_back
        return None, None
    finally:
        del frame


@contextmanager
def observe_visual_inspections(path, required):
    if not required:
        yield
        return
    from wra.evaluator import visual_check
    from wra.evaluator.llm import LLMEvaluator
    from wra.llm.response import normalize_json_response
    original_inspect = visual_check.VisualInspector.inspect
    original_evaluate = LLMEvaluator.evaluate
    performed = set()

    def inspect_visual(inspector, browser_result, html, model, think=False, timeout=None):
        iteration, slug = iteration_context()
        event = {'event': 'visual_inspection', 'iteration': iteration, 'slug': slug, 'performed': False, 'model': model, 'started_at': time.time()}
        started = time.monotonic()
        state = {'valid_response': False, 'error': None}
        client = inspector._llm_client

        class Client:
            def generate(self, *args, **kwargs):
                state.update(valid_response=False, error=None)
                try:
                    response = client.generate(*args, **kwargs)
                except Exception as error:
                    state['error'] = type(error).__name__ + ': ' + str(error)
                    raise
                try:
                    payload = json.loads(normalize_json_response(response))
                    state['valid_response'] = isinstance(payload, dict) and isinstance(payload.get('defects'), list)
                except (ValueError, TypeError):
                    pass
                return response

        class Errors(logging.Handler):
            def emit(self, record):
                if record.levelno >= logging.ERROR:
                    state['error'] = record.getMessage()

        errors = Errors()
        visual_check.logger.addHandler(errors)
        inspector._llm_client = Client()
        try:
            if browser_result is None or not browser_result.enabled or not browser_result.viewports or not model:
                raise RuntimeError('Required visual inspection inputs are unavailable')
            if any(not viewport.screenshot_path or not Path(viewport.screenshot_path).is_file() for viewport in browser_result.viewports):
                raise RuntimeError('Required visual inspection screenshots are unavailable')
            findings = original_inspect(inspector, browser_result, html, model, think=think, timeout=timeout)
            if not isinstance(findings, list) or not state['valid_response'] or state['error']:
                raise RuntimeError('Required visual inspection did not complete: ' + str(state['error'] or 'no valid final response'))
            performed.add((iteration, slug))
            event.update(performed=True, findings_count=len(findings))
            return findings
        except BaseException as error:
            event['error'] = type(error).__name__ + ': ' + str(error)
            raise
        finally:
            inspector._llm_client = client
            visual_check.logger.removeHandler(errors)
            event['elapsed_seconds'] = time.monotonic() - started
            append_event(path, event)

    def evaluate_after_visual(evaluator, *args, **kwargs):
        iteration, slug = iteration_context()
        if (iteration, slug) not in performed:
            error = 'Required visual inspection was not performed before evaluation'
            append_event(path, {'event': 'visual_inspection', 'iteration': iteration, 'slug': slug, 'performed': False, 'error': error})
            raise RuntimeError(error)
        return original_evaluate(evaluator, *args, **kwargs)

    visual_check.VisualInspector.inspect = inspect_visual
    LLMEvaluator.evaluate = evaluate_after_visual
    try:
        yield
    finally:
        visual_check.VisualInspector.inspect = original_inspect
        LLMEvaluator.evaluate = original_evaluate


def execute_cli():
    from wra import cli
    original_prepare = cli.InputPreparer.prepare

    def prepare_one(preparer, *args, **kwargs):
        pages = original_prepare(preparer, *args, **kwargs)
        if len(pages) != 1:
            raise RuntimeError(f'Expected exactly one prepared page, got {len(pages)}')
        return pages

    cli.InputPreparer.prepare = prepare_one
    try:
        cli.main()
    finally:
        cli.InputPreparer.prepare = original_prepare


def resolved_browser(work):
    overrides = {name: yaml.safe_load((work / 'config' / name).read_text(encoding='utf-8')) for name in ('config.yaml', 'models.yaml', 'frameworks.yaml')}
    return WRAConfig.model_validate(load_raw(overrides=overrides)).browser


def validate_outputs(work):
    runs = list((work / 'output').glob('run_*'))
    if len(runs) != 1:
        raise RuntimeError('Expected exactly one output run')
    run = runs[0]
    result = read_json(run / 'result.json')
    if len(result['pages']) != 1:
        raise RuntimeError('Expected exactly one final page')
    slug = next(iter(result['pages']))
    if not (run / f'{slug}.html').is_file():
        raise RuntimeError('Final HTML missing')
    resolved = resolved_browser(work)
    if resolved.enabled and resolved.visual_inspection:
        events = [json.loads(line) for line in (work / 'calls.jsonl').read_text(encoding='utf-8').splitlines()] if (work / 'calls.jsonl').exists() else []
        inspections = [event for event in events if event['event'] == 'visual_inspection']
        if len(inspections) != 3 or any(not event['performed'] for event in inspections) or {(event['iteration'], event['slug']) for event in inspections} != {(iteration, slug) for iteration in range(1, 4)}:
            raise RuntimeError('Expected three completed required visual inspections')
    expected_viewports = {(viewport.name, viewport.width, viewport.height) for viewport in resolved.viewports}
    if {path.name for path in (run / 'iterations').glob('iter*')} != {'iter1', 'iter2', 'iter3'}:
        raise RuntimeError('Expected exactly three saved iterations')
    for iteration in range(1, 4):
        data = run / 'iterations' / f'iter{iteration}' / 'data' / slug
        for name in ('design.json', 'evaluation.json', 'validation.json', 'browser.json'):
            if not (data / name).is_file():
                raise RuntimeError(f'Missing iteration evidence: {data / name}')
        if not read_json(data / 'validation.json')['valid']:
            raise RuntimeError(f'Iteration {iteration} has invalid HTML')
        browser = read_json(data / 'browser.json')
        if resolved.enabled:
            actual_viewports = {(viewport['name'], viewport['width'], viewport['height']) for viewport in browser['viewports']}
            if not browser['enabled'] or browser.get('failed_viewports') or actual_viewports != expected_viewports:
                raise RuntimeError(f'Iteration {iteration} lacks complete configured browser evidence')
            for viewport in browser['viewports']:
                if not viewport.get('screenshot_path') or not (data / 'browser' / Path(viewport['screenshot_path']).name).is_file():
                    raise RuntimeError(f'Iteration {iteration} screenshot missing: {viewport["name"]}')
    return {'run_directory': str(run), 'result': result, 'observed_iterations': 3}


def call_summary(path):
    events = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []
    started = [event for event in events if event['event'] == 'started']
    ended = [event for event in events if event['event'] in {'finished', 'failed'}]
    groups = {}
    roles = {}
    for event in started:
        logical = event.get('logical_call')
        role = logical['role'] if logical else 'outside_pipeline'
        roles[role] = roles.get(role, 0) + 1
        if logical and not event['probe']:
            groups[logical['id']] = groups.get(logical['id'], 0) + 1
    inspections = [event for event in events if event['event'] == 'visual_inspection']
    return {'http_generation_attempts': len(started), 'logical_pipeline_calls': len(groups), 'transport_resends': sum(count - 1 for count in groups.values()), 'probe_attempts': sum(bool(event['probe']) for event in started), 'failed_http_attempts': sum(event['event'] == 'failed' for event in ended), 'unfinished_http_attempts': len(started) - len(ended), 'attempts_by_role': roles, 'http_elapsed_seconds': sum(event['elapsed_seconds'] for event in ended), 'performed_visual_inspections': sum(bool(event['performed']) for event in inspections), 'failed_visual_inspections': sum(not event['performed'] for event in inspections), 'semantic_retry_detail': 'Per-role caller attempt_local/function in calls.jsonl and original WRA logs; no retry inference from prompt keywords.'}


def worker(destination, bundle, case, token):
    destination = Path(destination).resolve()
    work = destination / bundle / case
    with exclusive(destination / 'worker.lock'):
        _, ledger = verify(destination)
        job = ledger['jobs'].get(f'{bundle}/{case}', {})
        if job.get('status') != 'running' or job.get('token') != token:
            raise RuntimeError('Worker reservation is no longer active')
        if Path.cwd().resolve() != work:
            raise RuntimeError('Worker must run in its isolated working directory')
        if (work / 'receipt.json').exists():
            raise RuntimeError('Worker receipt already exists; refusing duplicate execution')
        started = time.monotonic()
        receipt = {'token': token, 'exit_code': 1}
        try:
            resolved = resolved_browser(work)
            required_visual = resolved.enabled and resolved.visual_inspection
            with observe_calls(work / 'calls.jsonl'), observe_visual_inspections(work / 'calls.jsonl', required_visual):
                execute_cli()
            receipt.update(validate_outputs(work), exit_code=0)
        except BaseException as error:
            receipt['error'] = type(error).__name__ + ': ' + str(error)
            print(receipt['error'], flush=True)
        finally:
            receipt['elapsed_seconds'] = time.monotonic() - started
            receipt['calls'] = call_summary(work / 'calls.jsonl')
            write_json(work / 'receipt.json', receipt)
        return receipt['exit_code']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('prepare', 'run', 'abandon', 'worker'))
    parser.add_argument('destination', type=Path)
    parser.add_argument('--phase', choices=('pilot', 'remaining', 'all'), default='pilot')
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--cases', nargs='+')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--pilot-reviewed', action='store_true')
    parser.add_argument('--bundle', choices=('A', 'B'))
    parser.add_argument('--case')
    parser.add_argument('--token')
    args = parser.parse_args()
    if args.action == 'prepare':
        record = prepare(SNAPSHOT, CANDIDATE, args.destination, args.seed, args.cases)
        print(f"Prepared {len(record['prepared'])} isolated work directories (seed {record['seed']}); run --phase {'pilot' if len(record['cases']) == len(SAMPLES) else 'all'} starts at most {record['first_batch_max_iterations']} iterations.")
    elif args.action == 'run':
        ledger = execute(args.destination, args.phase, args.resume, args.pilot_reviewed)
        print(json.dumps(ledger, ensure_ascii=False, indent=2))
    else:
        if not args.bundle or not args.case:
            parser.error('--bundle and --case are required')
        if args.case not in read_json(args.destination / 'preflight.json').get('cases', SAMPLES):
            parser.error(f'--case must be one of the cases recorded for this experiment: {args.case}')
        if args.action == 'abandon':
            print(json.dumps(abandon(args.destination, args.bundle, args.case), ensure_ascii=False, indent=2))
        elif not args.token:
            parser.error('--token is required for internal workers')
        else:
            raise SystemExit(worker(args.destination, args.bundle, args.case, args.token))


if __name__ == '__main__':
    main()
