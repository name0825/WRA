from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
import logging
import shutil

from wra.browser.models import BrowserEvaluationResult
from wra.core.brief import BriefDirectives
from wra.core.models import EVALUATION_DIMENSIONS, ContentFreedom, FACTUAL_ACCURACY_RULE, FrameworkConfig, SOURCE_LANGUAGE_RULE, STRUCTURAL_DIMENSIONS, EvaluationResult, FeatureModel, content_freedom_policy, coverage_is_enforced
from wra.core.output import OutputManager, outranks
from wra.core.preparation import PreparedPage
from wra.generator.logo_insert import insert_missing_logo
from wra.generator.record_sync import sync_brief_records
from wra.designer.schema import DesignSpec
from wra.validator.adherence import find_directive_deviations, find_unapplied_design_tokens
from wra.validator.anchors import find_broken_anchors
from wra.validator.brief_facts import find_dropped_brief_facts, locate_brief_facts
from wra.validator.browser_errors import find_browser_errors
from wra.validator.claims import find_unsupported_claims
from wra.validator.craft import find_refinable_craft_defects
from wra.validator.dates import find_dropped_dates
from wra.validator.quantities import find_dropped_questions, find_dropped_quantities
from wra.validator.names import find_dropped_identity_name, find_misnamed_people
from wra.validator.facts import find_missing_business_facts
from wra.validator.forms import find_invented_forms
from wra.validator.html import HTMLValidationResult, validate_html
from wra.validator.language import find_language_drift, output_language_rule
from wra.validator.regression import find_regressions
from wra.validator.syntax import find_syntax_defects
from wra.validator.tokens import find_corrupted_tokens
from wra.validator.visual import browser_craft_metrics, find_refinable_browser_defects, find_stuck_reveal_defects, find_visual_defects
from wra.llm.model_selector import ModelSelector

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IterationResult:
    slug: str
    iteration: int
    features: FeatureModel
    design_spec: DesignSpec
    html: str
    validation: HTMLValidationResult
    browser: BrowserEvaluationResult
    evaluation: EvaluationResult | None
    html_path: Path | None
    validation_path: Path | None
    final_path: Path
    visual_inspection_findings: list[str] = field(default_factory=list)
    regressions: list[str] = field(default_factory=list)
    floor_violations: list[str] = field(default_factory=list)


def _floor_violations(result) -> list[str]:
    return getattr(result, "floor_violations", None) or []


@dataclass
class _PageRun:
    """One page's standing in the shared iteration loop.

    `best` is what the next revision starts from. Revising the most recent
    attempt instead lets one bad iteration become the foundation of every
    iteration after it, and the page never climbs back.
    """

    page: PreparedPage
    last: IterationResult | None = None
    best: IterationResult | None = None
    stalled: int = 0
    setback: dict | None = None
    done: bool = False

    @property
    def best_score(self) -> float | None:
        return self.best.evaluation.overall_score if self.best and self.best.evaluation else None

    def adopts(self, violations: int, regressions: Sequence[str]) -> bool:
        if not regressions:
            return True
        return self.best is not None and bool(_floor_violations(self.best)) and violations == 0

    def record(self, result: IterationResult, regressions: Sequence[str] = ()) -> bool:
        """Take the result as the new base if it improved on the best so far and lost nothing the best had."""
        self.last = result
        score = result.evaluation.overall_score if result.evaluation else None
        violations = len(_floor_violations(result))
        best_violations = len(_floor_violations(self.best)) if self.best is not None else 0
        improved = (self.best is None or outranks(score, violations, self.best_score, best_violations)) and self.adopts(violations, regressions)
        if improved:
            self.best = result
            self.stalled = 0
            self.setback = None
        else:
            self.stalled += 1
            self.setback = _describe_setback(result, self.best, regressions)
        return improved


def _brief_fact_findings(page: PreparedPage, html: str) -> list[str]:
    return find_missing_business_facts(html, page.features) if page.is_entry and page.created else []


def _dropped_date_findings(page: PreparedPage, html: str) -> list[str]:
    return [] if page.created else find_dropped_dates(html, page.html)


def _dropped_quantity_findings(page: PreparedPage, html: str, freedom: ContentFreedom) -> list[str]:
    if page.created or not coverage_is_enforced(freedom):
        return []
    return find_dropped_quantities(html, page.html) + find_dropped_questions(html, page.html)


def _misnamed_person_findings(page: PreparedPage, html: str) -> list[str]:
    return [] if page.created else find_misnamed_people(html, page.html, page.features.model_dump_json())


def collect_content_findings(page: PreparedPage, html: str, freedom: ContentFreedom) -> list[str]:
    return find_corrupted_tokens(html, page.features, content_freedom=freedom) + find_unsupported_claims(html, page.features) + _dropped_date_findings(page, html) + _dropped_quantity_findings(page, html, freedom) + _misnamed_person_findings(page, html)


def collect_fact_loss_findings(page: PreparedPage, html: str, freedom: ContentFreedom) -> list[str]:
    """Source facts the page dropped where coverage is not enforced: a soft penalty and refiner feedback, never a cap."""
    if coverage_is_enforced(freedom):
        return []
    if page.created:
        return find_dropped_brief_facts(html, page.brief) if page.brief and page.is_entry else []
    return find_dropped_quantities(html, page.html) + find_dropped_questions(html, page.html, reworded_ok=True) + find_dropped_identity_name(html, page.html, page.features)


def _source_fact_loss_findings(page: PreparedPage, html: str, freedom: ContentFreedom) -> list[str]:
    return [] if page.created else collect_fact_loss_findings(page, html, freedom)


def collect_missing_brief_facts(page: PreparedPage, html: str, freedom: ContentFreedom) -> list[dict]:
    """Brief dates, links and items the page dropped, each with the brief entry to restore it from."""
    if coverage_is_enforced(freedom) or not (page.created and page.brief and page.is_entry):
        return []
    return locate_brief_facts(page.brief, find_dropped_brief_facts(html, page.brief, limit=_MAX_MISSING_BRIEF_FACTS))


def _missing_facts_kwargs(page: PreparedPage, html: str, freedom: ContentFreedom) -> dict:
    facts = collect_missing_brief_facts(page, html, freedom)
    return {"missing_brief_facts": facts} if facts else {}


def _locked_language(page: PreparedPage | None) -> str | None:
    return page.features.metadata.language if page is not None and page.created and page.features.metadata.language else None


def _language_kwargs(page: PreparedPage) -> dict:
    language = _locked_language(page)
    return {"language": language} if language else {}


def _directive_and_language_findings(page: PreparedPage, html: str) -> list[str]:
    findings: list[str] = []
    directives = page.directives
    if directives is not None:
        findings += find_directive_deviations(html, list(directives.palette.values()), list(directives.fonts.values()), directives.logo, directives.nav)
    if page.created:
        findings += find_language_drift(html, page.features.metadata.language)
    return findings


def _adherence_findings(page: PreparedPage, html: str, design_spec, frameworks: FrameworkConfig) -> list[str]:
    """Where the page ignores the design it was built from or the brief's own directives and language."""
    findings = find_unapplied_design_tokens(html, design_spec, frameworks) if isinstance(design_spec, DesignSpec) else []
    return findings + _directive_and_language_findings(page, html)


def collect_floor_violations(page: PreparedPage, html: str, freedom: ContentFreedom) -> list[str]:
    violations: list[str] = []
    if page.created and page.brief and page.is_entry and not coverage_is_enforced(freedom):
        violations += find_dropped_brief_facts(html, page.brief, limit=_FLOOR_BRIEF_FACT_LIMIT)
    return violations + _directive_and_language_findings(page, html)


_MAX_MISSING_BRIEF_FACTS = 40
_FLOOR_BRIEF_FACT_LIMIT = 1000
_PALETTE_ROLES = {"background": "background", "bg": "background", "surface": "surface", "text": "text", "muted": "muted_text", "muted_text": "muted_text", "primary": "primary", "accent": "accent"}
_FONT_ROLES = ("heading", "body")


def _apply_user_design(design_spec: DesignSpec, directives: BriefDirectives) -> DesignSpec:
    """The brief's palette and fonts are not the designer's to change, so they overwrite whatever it chose."""
    colors = {_PALETTE_ROLES[key]: value for key, value in directives.palette.items() if key in _PALETTE_ROLES}
    fonts = {key: value for key, value in directives.fonts.items() if key in _FONT_ROLES}
    design = design_spec.design
    update = {}
    if colors:
        update["color_system"] = design.color_system.model_copy(update=colors)
    if fonts:
        update["typography"] = design.typography.model_copy(update=fonts)
    return design_spec.model_copy(update={"design": design.model_copy(update=update)}) if update else design_spec


def _invented_form_findings(page: PreparedPage, html: str) -> list[str]:
    return [] if page.created else find_invented_forms(html, page.features)


def _describe_setback(rejected: IterationResult, best: IterationResult | None, regressions: Sequence[str] = ()) -> dict:
    """What the discarded attempt got wrong, for the next attempt to avoid."""
    note: dict = {
        "iteration": rejected.iteration,
        "score": round(rejected.evaluation.overall_score, 2) if rejected.evaluation else None,
        "best_score": round(best.evaluation.overall_score, 2) if best and best.evaluation else None,
        "instruction": (
            "An attempt from this same starting point scored lower and was discarded; you are revising the better "
            "page again, not that one. Keep what the better page does well and address the weaknesses below without "
            "repeating the regression."
        ),
    }
    if regressions:
        note["regressions"] = list(regressions)
        if note["score"] is not None and note["best_score"] is not None and note["score"] >= note["best_score"]:
            note["instruction"] = (
                "An attempt from this same starting point lost something the better page had and was discarded, "
                "whatever it scored; you are revising the better page again, not that one. Keep what the better page "
                "does well and do not repeat the losses listed under regressions."
            )
    floor_violations = _floor_violations(rejected)
    if floor_violations:
        note["floor_violations"] = list(floor_violations[:10])
        if note["score"] is not None and note["best_score"] is not None and note["score"] >= note["best_score"]:
            note["instruction"] = (
                "An attempt from this same starting point failed the acceptance floor and was discarded, whatever it "
                "scored; you are revising the better page again, not that one. Keep what the better page does well and "
                "do not repeat the failures listed under floor_violations."
            )
    if rejected.evaluation is None:
        note["regressed"] = ["the attempt produced no valid HTML to score"]
        return note
    if best is None or best.evaluation is None:
        return note
    regressed = []
    for dimension in EVALUATION_DIMENSIONS:
        was = getattr(best.evaluation, dimension).score
        now = getattr(rejected.evaluation, dimension).score
        if now < was:
            regressed.append(f"{dimension}: {was:.0f} -> {now:.0f}")
    if regressed:
        note["regressed"] = regressed
    if rejected.evaluation.summary:
        note["rejected_summary"] = rejected.evaluation.summary
    return note


class IterationEngine:
    """Execute generation, validation, browser evaluation, scoring, and revision."""

    def __init__(self, config, designer, generator, evaluator, browser_evaluator=None, refiner=None, visual_inspector=None):
        self._config = config
        self._designer = designer
        self._generator = generator
        self._evaluator = evaluator
        self._browser_evaluator = browser_evaluator
        self._refiner = refiner
        self._visual_inspector = visual_inspector

    def run_pages(self, pages: list[PreparedPage], iteration: int = 1) -> list[IterationResult]:
        """Redesign every page in lockstep: one iteration across all of them at a time.

        Every page reaches iteration 1 before any page reaches iteration 2.
        Running one page to completion first would strand the rest behind a loop
        that, with termination disabled, never ends.
        """
        site_context = self._build_site_context(pages) if self._config.input.mode == "site" else None
        run_dir = pages[0].run_root
        output_manager = OutputManager(run_dir, self._config.pipeline.output.save_mode, self._config.input.asset_directory)
        browser_tmp = run_dir / ".browser_tmp"
        states = [self._start_page(page) for page in pages]
        output_manager.register_pages([page.slug for page in pages])
        try:
            self._iterate(states, iteration, site_context, output_manager, browser_tmp)
            # Finalizing reads the best iteration's browser artifacts, so it has
            # to happen before the temporary directory holding them is removed.
            return self._finalize_pages(states, output_manager)
        finally:
            if browser_tmp.exists():
                shutil.rmtree(browser_tmp, ignore_errors=True)

    def _iterate(self, states, iteration, site_context, output_manager, browser_tmp) -> None:
        try:
            while True:
                pending = [state for state in states if not state.done]
                if not pending:
                    logger.info("All %d page(s) finished after iteration %d", len(states), iteration - 1)
                    break
                models = self._select_models()
                logger.info("Iteration %d started: pages=%d models: designer=%s generator=%s evaluator=%s", iteration, len(pending), models["designer"].name, models["generator"].name, models["evaluator"].name)
                for index, state in enumerate(pending, start=1):
                    logger.info("Iteration %d, page %d/%d: slug=%s", iteration, index, len(pending), state.page.slug)
                    design_system = self._design_system(states, state)
                    result = self._run_one_iteration(state, iteration, site_context, output_manager, browser_tmp, models, design_system)
                    regressions = getattr(result, "regressions", [])
                    if state.record(result, regressions):
                        logger.info("Page '%s' improved at iteration %d: best=%s", state.page.slug, iteration, f"{state.best_score:.2f}" if state.best_score is not None else "n/a")
                    else:
                        logger.info("Page '%s' did not improve at iteration %d (%s vs best %s%s); reverting to the best result as the base", state.page.slug, iteration,
                                    f"{result.evaluation.overall_score:.2f}" if result.evaluation else "invalid", f"{state.best_score:.2f}" if state.best_score is not None else "n/a",
                                    f", {len(regressions)} regression(s)" if regressions else "")
                    if self._should_terminate(iteration, state):
                        state.done = True
                iteration += 1
        except KeyboardInterrupt:
            logger.warning("Pipeline interrupted by user")
            if all(state.last is None for state in states):
                raise
        except Exception:
            logger.exception("Pipeline failed")
            raise

    @staticmethod
    def _regressions(state: _PageRun, iteration: int, html: str, browser: BrowserEvaluationResult | None, evaluation: EvaluationResult | None) -> list[str]:
        if state.best is None or state.best.evaluation is None or evaluation is None:
            return []
        regressions = find_regressions(state.best.html, getattr(state.best, "browser", None), html, browser, state.page.features, check_brief_facts=state.page.is_entry and state.page.created)
        if regressions:
            logger.warning("Page '%s' iteration %d regressed against the best: %s", state.page.slug, iteration, regressions)
        return regressions

    def _start_page(self, page: PreparedPage) -> _PageRun:
        data_dir = Path(page.data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Page queued: page=%s data=%s", page.relative, data_dir)
        features_path = data_dir / "features.json"
        if not features_path.exists():
            features_path.write_text(page.features.model_dump_json(indent=2), encoding="utf-8")
        logger.info("Feature Model ready: path=%s", features_path)
        return _PageRun(page=page)

    def _finalize_pages(self, states: list[_PageRun], output_manager: OutputManager) -> list[IterationResult]:
        results = []
        for state in states:
            if state.last is None:
                logger.warning("Page '%s' produced no result to finalize", state.page.slug)
                continue
            chosen = state.best or state.last
            violations = _floor_violations(chosen)
            if violations:
                logger.warning("floor not met: page=%s iteration=%d violations=%d: %s", chosen.slug, chosen.iteration, len(violations), violations[:5])
                chosen = self._repair_unmet_floor(state, chosen, output_manager)
            results.append(self._finalize(chosen, output_manager))
        return results

    def _repair_unmet_floor(self, state: _PageRun, result: IterationResult, output_manager: OutputManager) -> IterationResult:
        page = state.page
        if not (page.created and page.is_entry and page.features.records):
            return result
        freedom = self._config.content.freedom
        html, repairs = sync_brief_records(result.html, page.features.records, freedom)
        if not repairs:
            return result
        validation = validate_html(html, page.features, content_freedom=freedom)
        violations = collect_floor_violations(page, html, freedom)
        if not validation.valid or len(violations) >= len(_floor_violations(result)):
            logger.warning("Brief record sync did not meet the floor: page=%s valid=%s violations=%d -> %d", page.slug, validation.valid, len(_floor_violations(result)), len(violations))
            return result
        logger.info("Brief record sync repaired the floor: page=%s repairs=%d violations=%d -> %d", page.slug, len(repairs), len(_floor_violations(result)), len(violations))
        output_manager.replace_best_html(page.slug, html, violations)
        return replace(result, html=html, validation=validation, floor_violations=violations)

    @staticmethod
    def _build_site_context(pages: list[PreparedPage]) -> dict:
        """The sibling pages each page is designed to sit beside."""
        return {
            "pages": [
                {
                    "file": f"{page.slug}.html",
                    "title": page.features.metadata.title or page.slug,
                    "entry": page.is_entry,
                    **({"carries": page.content_focus} if page.content_focus else {}),
                }
                for page in pages
            ],
            "instruction": "These pages form one site. Keep navigation, header, footer, and visual language consistent across them, and link between them using the listed file names.",
        }

    def _run_one_iteration(self, state: _PageRun, iteration: int, site_context: dict | None, output_manager: OutputManager, browser_tmp: Path, models: dict | None = None, design_system: dict | None = None) -> IterationResult:
        features = state.page.features
        slug = state.page.slug
        previous_result = state.best
        models = models or self._select_models()
        designer_model, generator_model, evaluator_model = models["designer"], models["generator"], models["evaluator"]
        refiner_model = models["refiner"]

        design_constraints = self._build_design_constraints(iteration, site_context, state.page, design_system, state.setback)
        logger.debug("Design constraints: %s", design_constraints)
        html = None
        route = None
        refine_failed = False
        design_spec = previous_result.design_spec if previous_result is not None else None

        if self._refines(state):
            logger.info("Current task: refining the best page (score=%.2f)", previous_result.evaluation.overall_score)
            try:
                html = self._refiner.refine(features, previous_result.html, previous_result.evaluation, design_spec=previous_result.design_spec, content_findings=collect_content_findings(state.page, previous_result.html, self._config.content.freedom) + _source_fact_loss_findings(state.page, previous_result.html, self._config.content.freedom),ux_findings=find_broken_anchors(previous_result.html) + find_browser_errors(previous_result.browser) + find_syntax_defects(previous_result.html) + _brief_fact_findings(state.page, previous_result.html) + _invented_form_findings(state.page, previous_result.html) +find_refinable_craft_defects(previous_result.html, features) + find_refinable_browser_defects(previous_result.browser), visual_findings=find_visual_defects(previous_result.html, features, previous_result.browser) + previous_result.visual_inspection_findings + _adherence_findings(state.page, previous_result.html, previous_result.design_spec, self._config.frameworks), model=refiner_model.name, think=refiner_model.think, timeout=refiner_model.timeout, asset_root=output_manager.run_dir, regressions=(state.setback or {}).get("regressions"), **_missing_facts_kwargs(state.page, previous_result.html, self._config.content.freedom), **_language_kwargs(state.page))
                route = "refine"
            except Exception as exc:
                # A refinement that will not come out valid is not worth an
                # iteration; fall through and rebuild the page properly.
                logger.warning("Refinement failed, falling back to a full redesign: %s", exc)
                html = None
                refine_failed = True

        if html is None:
            if previous_result is None or previous_result.evaluation is None:
                logger.info("Current task: design planning")
                route = "initial"
                design_spec = self._designer.plan(features, design_constraints=design_constraints, model=designer_model.name, think=designer_model.think, timeout=designer_model.timeout, allow_forms=state.page.created)
            else:
                logger.info("Current task: design revision based on score=%.2f", previous_result.evaluation.overall_score)
                route = "redesign"
                design_spec = self._designer.revise(features=features, previous_design_spec=previous_result.design_spec, previous_html=previous_result.html, evaluation=previous_result.evaluation, design_constraints=design_constraints, model=designer_model.name, think=designer_model.think, timeout=designer_model.timeout, allow_forms=state.page.created)

            if state.page.directives is not None:
                design_spec = _apply_user_design(design_spec, state.page.directives)
            logger.info("Current task: HTML generation")
            html = self._generator.generate(features, design_spec, generation_rules=self._build_generation_rules(state.page), model=generator_model.name, think=generator_model.think, timeout=generator_model.timeout, asset_root=output_manager.run_dir, preserve_facts=iteration > 1, **(_missing_facts_kwargs(state.page, previous_result.html, self._config.content.freedom) if previous_result is not None else {}))
        brief_logo = state.page.directives.logo if state.page.directives is not None else None
        if brief_logo:
            html, logo_repairs = insert_missing_logo(html, brief_logo, (features.business.company_name if features.business else None) or features.metadata.title or "")
            if logo_repairs:
                logger.info("Repaired missing brief logo: %s", logo_repairs)
        logger.info("Current task: HTML validation")
        validation = validate_html(html, features, content_freedom=self._config.content.freedom)
        browser_result = BrowserEvaluationResult(enabled=False)
        evaluation = None
        evaluation_error = None
        inspection_findings: list[str] = []
        if validation.valid:
            if self._browser_evaluator is not None and self._config.browser.enabled:
                logger.info("Current task: browser evaluation")
                browser_result = self._browser_evaluator.evaluate(html=html, output_dir=browser_tmp / slug, iteration=iteration, asset_root=output_manager.run_dir)
                logger.info("Craft browser metrics: %s", browser_craft_metrics(browser_result))
            try:
                if self._visual_inspector is not None and self._browser_evaluator is not None and self._config.browser.enabled:
                    logger.info("Current task: visual inspection")
                    inspection_findings = self._visual_inspector.inspect(browser_result, html, model=evaluator_model.name, think=evaluator_model.think, timeout=evaluator_model.timeout)
                logger.info("Current task: LLM evaluation")
                evaluation = self._evaluator.evaluate(features, design_spec, html, browser_result=browser_result, content_findings=collect_content_findings(state.page, html, self._config.content.freedom),ux_findings=find_broken_anchors(html) + find_browser_errors(browser_result) + find_syntax_defects(html) + _brief_fact_findings(state.page, html) + _invented_form_findings(state.page, html), visual_findings=find_visual_defects(html, features, browser_result) + inspection_findings + _adherence_findings(state.page, html, design_spec, self._config.frameworks), model=evaluator_model.name, think=evaluator_model.think, timeout=evaluator_model.timeout, fact_loss_findings=collect_fact_loss_findings(state.page, html, self._config.content.freedom))
            except Exception as exc:
                logger.exception("Evaluation failed for iteration %d, page '%s'", iteration, slug)
                evaluation_error = exc
        else:
            logger.warning("Skipping browser/evaluation because HTML validation failed")

        logger.info("Current task: saving iteration %d for page '%s'", iteration, slug)
        regressions = self._regressions(state, iteration, html, browser_result, evaluation)
        floor_violations = collect_floor_violations(state.page, html, self._config.content.freedom)
        saved = output_manager.save_iteration(iteration=iteration, slug=slug, html=html, design_spec=design_spec, validation=validation, evaluation=evaluation, browser=browser_result, score=evaluation.overall_score if evaluation else None, path=route, refine_failed=refine_failed, adoptable=state.adopts(len(floor_violations), regressions), floor_violations=floor_violations)
        logger.info("Iteration %d completed for page '%s': validation=%s score=%s", iteration, slug, validation.valid, f"{evaluation.overall_score:.2f}" if evaluation else "n/a")
        if evaluation_error is not None:
            # The page was saved above; re-raise so an evaluator failure still
            # surfaces instead of silently looping unscored pages forever.
            raise evaluation_error
        return IterationResult(slug=slug, iteration=iteration, features=features, design_spec=design_spec, html=html, validation=validation, browser=browser_result, evaluation=evaluation, html_path=saved.html_path, validation_path=saved.validation_path, final_path=output_manager.run_dir / f"{slug}.html", visual_inspection_findings=inspection_findings, regressions=regressions, floor_violations=floor_violations)

    def _select_models(self) -> dict:
        """One model choice per iteration.

        Selecting per page would let two pages of the same iteration be designed
        and generated by different models, which is a large part of why they
        came out looking unrelated.
        """
        return {
            "designer": ModelSelector.select(self._config.models.designer, "designer"),
            "generator": ModelSelector.select(self._config.models.generator, "generator"),
            # Refinement edits a finished page and is where a weak model quietly
            # damages the source text, so it gets its own, stronger pool.
            "refiner": ModelSelector.select(self._config.models.refiner or self._config.models.generator, "refiner"),
            "evaluator": ModelSelector.select(self._config.models.evaluator, "evaluator"),
        }

    @staticmethod
    def _design_system(states: list[_PageRun], current: _PageRun) -> dict | None:
        """The design system every other page of the site must adopt.

        The entry page leads: it is designed first each iteration, and its
        colours, type, framework, and motion become fixed for its siblings. Left
        to themselves, sibling pages each invent their own and the site reads as
        several sites.
        """
        lead = states[0]
        if len(states) < 2 or lead is current:
            return None
        # The entry page publishes its *best* attempt, not its latest one. A
        # sibling that follows `last` after the lead has just regressed locks
        # onto a design system the finished site never shows.
        source = lead.best or lead.last
        if source is None:
            return None
        spec = source.design_spec
        if spec is None:
            return None
        return {
            "source_page": f"{lead.page.slug}.html",
            "color_system": spec.design.color_system.model_dump(),
            "typography": spec.design.typography.model_dump(),
            "framework": spec.design.framework.model_dump(),
            "motion": spec.design.motion.model_dump(),
            "breakpoints": [breakpoint.model_dump() for breakpoint in spec.responsive.breakpoints],
            "instruction": (
                "These values are already fixed for this site by the page named in source_page. "
                "Copy them into your DesignSpec exactly as given. Compose the layout and sections freely, "
                "but do not introduce a different palette, type family, framework, motion level, or breakpoint set."
            ),
        }

    def _refines(self, state: _PageRun) -> bool:
        """Whether this iteration edits the best page instead of rebuilding it.

        There must be a scored page to improve; refinement must not have
        stalled, because a page that several small edits failed to move needs a
        different design rather than another edit; and the page's worst
        dimension must be one a local edit can reach. A page held back by its
        composition or its design system cannot be edited into a better score,
        only redesigned. The exception is a freshly improved page with content stuck
        invisible to real visitors, where the low structural score comes from a
        reveal script that a local edit can fix.
        """
        refine = self._config.pipeline.refine
        if not refine.enabled or self._refiner is None:
            return False
        if state.best is None or state.best.evaluation is None:
            return False
        if refine.redesign_after_stalls and state.stalled >= refine.redesign_after_stalls:
            logger.info("Page '%s' stalled %d time(s): redesigning instead of refining", state.page.slug, state.stalled)
            return False
        if state.stalled == 0 and find_stuck_reveal_defects(state.best.browser):
            logger.info("Page '%s' has content stuck invisible: refining to fix the reveal script", state.page.slug)
            return True
        worst = min(EVALUATION_DIMENSIONS, key=lambda name: getattr(state.best.evaluation, name).score)
        if worst in STRUCTURAL_DIMENSIONS:
            logger.info("Page '%s' is weakest at '%s', which a local edit cannot move: redesigning instead of refining", state.page.slug, worst)
            return False
        return True

    def _build_design_constraints(self, iteration: int, site_context: dict | None = None, page: PreparedPage | None = None, design_system: dict | None = None, setback: dict | None = None) -> dict:
        mode = self._config.design.mode
        constraints = {"mode": mode}
        if setback:
            constraints["rejected_attempt"] = setback
        if site_context:
            constraints["site"] = site_context
        if design_system:
            constraints["design_system"] = design_system
        if page is not None and page.content_focus:
            # This page was cut from a restructured site, so its Feature Model
            # may carry material that belongs to a sibling page.
            constraints["page_scope"] = {
                "file": f"{page.slug}.html",
                "carries": page.content_focus,
                "instruction": "The Feature Model may hold material belonging to sibling pages. Build this page around what it carries and leave the rest to them.",
            }
        if page is not None and page.created:
            constraints["page_origin"] = {"kind": "brief", "instruction": "This page is built from a client brief; an honest `mailto:` form is allowed."}
        if page is not None and page.features.records:
            constraints["brief_records"] = {
                "sections": [{"id": section.id, "heading": section.heading, "records": len(section.records)} for section in page.features.records],
                "instruction": (
                    "Plan a home on the page for every section listed in the Feature Model `records`; several may share one section. Never drop a record. "
                    "These are the only content sections: add no other (a hero, navigation, footer and contact built from listed content are fine); "
                    "invent no job title, role, tagline, philosophy or strengths."
                ),
            }
        language = _locked_language(page)
        if language:
            constraints["output_language"] = output_language_rule(language)
        if page is not None and page.directives is not None:
            directives = page.directives
            constraints["user_design"] = {
                "palette": directives.palette,
                "typography": directives.fonts,
                "logo": directives.logo,
                "layout": directives.layout,
                "must": directives.must,
                "avoid": directives.avoid,
                "instruction": "Fixed by the client's brief even in auto mode: copy palette into color_system and fonts into typography exactly; keep the logo in the header; layout, must and avoid are binding.",
            }
        if mode != "auto":
            constraints["available_presets"] = self._config.design.presets
        exploration_axes = (
            "Explore composition and information hierarchy; avoid defaulting to a conventional business landing-page structure.",
            "Explore typography, density, and spatial rhythm; allow a substantially different reading flow when content supports it.",
            "Explore color relationships, surfaces, contrast, and visual texture; do not assume a dark corporate palette.",
            "Explore interaction and motion as appropriate; consider different interaction models without sacrificing clarity or accessibility.",
        )
        constraints["variation_policy"] = {"type": "soft_guidance", "instruction": exploration_axes[(iteration - 1) % len(exploration_axes)], "do_not_force": True}
        freedom = self._config.content.freedom
        policy = content_freedom_policy(freedom)
        constraints["content_freedom"] = {"level": freedom, "instruction": policy["planning"], "coverage_required": coverage_is_enforced(freedom), "factual_accuracy": FACTUAL_ACCURACY_RULE, "source_language": SOURCE_LANGUAGE_RULE}
        return constraints

    def _build_generation_rules(self, page: PreparedPage | None = None) -> list[str]:
        freedom = self._config.content.freedom
        rules = [
            f"Content freedom `{freedom}`: {content_freedom_policy(freedom)['authoring']}",
            f"Factual accuracy: {FACTUAL_ACCURACY_RULE}",
            f"Source language: {SOURCE_LANGUAGE_RULE}",
        ]
        language = _locked_language(page)
        if language:
            rules.append(output_language_rule(language))
        if page is not None and page.directives is not None and page.directives.logo:
            rules.append(f"Use the logo asset `{page.directives.logo}` in the header via <img>.")
        return rules

    def _finalize(self, result, output_manager):
        logger.info("Finalizing page '%s' from iteration %d", result.slug, result.iteration)
        final_path = output_manager.finalize(result.slug, fallback_html=result.html)
        logger.info("Final output ready: %s", final_path)
        return IterationResult(slug=result.slug, iteration=result.iteration, features=result.features, design_spec=result.design_spec, html=result.html, validation=result.validation, browser=result.browser, evaluation=result.evaluation, html_path=result.html_path, validation_path=result.validation_path, final_path=final_path, floor_violations=_floor_violations(result))

    def _should_terminate(self, iteration, state: _PageRun) -> bool:
        termination = self._config.pipeline.termination
        slug = state.page.slug
        if iteration >= self._config.pipeline.max_iterations:
            logger.info("Page '%s': maximum iterations reached: %d", slug, iteration)
            return True
        if not termination.enabled:
            logger.info("Early termination disabled: continuing to next iteration")
            return False
        best = state.best_score
        if best is not None and best >= self._config.pipeline.evaluation_threshold and not _floor_violations(state.best):
            logger.info("Page '%s': evaluation threshold reached: %.2f >= %.2f", slug, best, self._config.pipeline.evaluation_threshold)
            return True
        if termination.patience and state.stalled >= termination.patience:
            logger.info("Page '%s': no improvement in %d iteration(s); stopping at best %.2f", slug, state.stalled, best if best is not None else float("nan"))
            return True
        return False
