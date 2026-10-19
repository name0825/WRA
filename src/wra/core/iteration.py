from dataclasses import dataclass, field
from pathlib import Path
import logging
import shutil

from wra.browser.models import BrowserEvaluationResult
from wra.core.models import EVALUATION_DIMENSIONS, FACTUAL_ACCURACY_RULE, SOURCE_LANGUAGE_RULE, STRUCTURAL_DIMENSIONS, EvaluationResult, FeatureModel, content_freedom_policy, coverage_is_enforced
from wra.core.output import OutputManager
from wra.core.preparation import PreparedPage
from wra.designer.schema import DesignSpec
from wra.validator.anchors import find_broken_anchors
from wra.validator.browser_errors import find_browser_errors
from wra.validator.claims import find_unsupported_claims
from wra.validator.facts import find_missing_business_facts
from wra.validator.html import HTMLValidationResult, validate_html
from wra.validator.syntax import find_syntax_defects
from wra.validator.tokens import find_corrupted_tokens
from wra.validator.visual import find_visual_defects
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

    def record(self, result: IterationResult) -> bool:
        """Take the result as the new base if it improved on the best so far."""
        self.last = result
        score = result.evaluation.overall_score if result.evaluation else None
        best = self.best_score
        improved = self.best is None or (score is not None and (best is None or score > best))
        if improved:
            self.best = result
            self.stalled = 0
            self.setback = None
        else:
            self.stalled += 1
            self.setback = _describe_setback(result, self.best)
        return improved


def _brief_fact_findings(page: PreparedPage, html: str) -> list[str]:
    return find_missing_business_facts(html, page.features) if page.is_entry and page.created else []


def _describe_setback(rejected: IterationResult, best: IterationResult | None) -> dict:
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
                    if state.record(result):
                        logger.info("Page '%s' improved at iteration %d: best=%s", state.page.slug, iteration, f"{state.best_score:.2f}" if state.best_score is not None else "n/a")
                    else:
                        logger.info("Page '%s' did not improve at iteration %d (%s vs best %s); reverting to the best result as the base", state.page.slug, iteration,
                                    f"{result.evaluation.overall_score:.2f}" if result.evaluation else "invalid", f"{state.best_score:.2f}" if state.best_score is not None else "n/a")
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
            results.append(self._finalize(state.best or state.last, output_manager))
        return results

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
        design_spec = previous_result.design_spec if previous_result is not None else None

        if self._refines(state):
            logger.info("Current task: refining the best page (score=%.2f)", previous_result.evaluation.overall_score)
            try:
                html = self._refiner.refine(features, previous_result.html, previous_result.evaluation, design_spec=previous_result.design_spec, content_findings=find_corrupted_tokens(previous_result.html, features, content_freedom=self._config.content.freedom) + find_unsupported_claims(previous_result.html, features),ux_findings=find_broken_anchors(previous_result.html) + find_browser_errors(previous_result.browser) + find_syntax_defects(previous_result.html) + _brief_fact_findings(state.page, previous_result.html), visual_findings=find_visual_defects(previous_result.html, features, previous_result.browser) + previous_result.visual_inspection_findings, model=refiner_model.name, think=refiner_model.think, timeout=refiner_model.timeout, asset_root=output_manager.run_dir)
            except Exception as exc:
                # A refinement that will not come out valid is not worth an
                # iteration; fall through and rebuild the page properly.
                logger.warning("Refinement failed, falling back to a full redesign: %s", exc)
                html = None

        if html is None:
            if previous_result is None or previous_result.evaluation is None:
                logger.info("Current task: design planning")
                design_spec = self._designer.plan(features, design_constraints=design_constraints, model=designer_model.name, think=designer_model.think, timeout=designer_model.timeout)
            else:
                logger.info("Current task: design revision based on score=%.2f", previous_result.evaluation.overall_score)
                design_spec = self._designer.revise(features=features, previous_design_spec=previous_result.design_spec, previous_html=previous_result.html, evaluation=previous_result.evaluation, design_constraints=design_constraints, model=designer_model.name, think=designer_model.think, timeout=designer_model.timeout)

            logger.info("Current task: HTML generation")
            html = self._generator.generate(features, design_spec, generation_rules=self._build_generation_rules(), model=generator_model.name, think=generator_model.think, timeout=generator_model.timeout, asset_root=output_manager.run_dir)
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
            try:
                if self._visual_inspector is not None and self._browser_evaluator is not None and self._config.browser.enabled:
                    logger.info("Current task: visual inspection")
                    inspection_findings = self._visual_inspector.inspect(browser_result, html, model=evaluator_model.name, think=evaluator_model.think, timeout=evaluator_model.timeout)
                logger.info("Current task: LLM evaluation")
                evaluation = self._evaluator.evaluate(features, design_spec, html, browser_result=browser_result, content_findings=find_corrupted_tokens(html, features, content_freedom=self._config.content.freedom) + find_unsupported_claims(html, features),ux_findings=find_broken_anchors(html) + find_browser_errors(browser_result) + find_syntax_defects(html) + _brief_fact_findings(state.page, html), visual_findings=find_visual_defects(html, features, browser_result) + inspection_findings, model=evaluator_model.name, think=evaluator_model.think, timeout=evaluator_model.timeout)
            except Exception as exc:
                logger.exception("Evaluation failed for iteration %d, page '%s'", iteration, slug)
                evaluation_error = exc
        else:
            logger.warning("Skipping browser/evaluation because HTML validation failed")

        logger.info("Current task: saving iteration %d for page '%s'", iteration, slug)
        saved = output_manager.save_iteration(iteration=iteration, slug=slug, html=html, design_spec=design_spec, validation=validation, evaluation=evaluation, browser=browser_result, score=evaluation.overall_score if evaluation else None)
        logger.info("Iteration %d completed for page '%s': validation=%s score=%s", iteration, slug, validation.valid, f"{evaluation.overall_score:.2f}" if evaluation else "n/a")
        if evaluation_error is not None:
            # The page was saved above; re-raise so an evaluator failure still
            # surfaces instead of silently looping unscored pages forever.
            raise evaluation_error
        return IterationResult(slug=slug, iteration=iteration, features=features, design_spec=design_spec, html=html, validation=validation, browser=browser_result, evaluation=evaluation, html_path=saved.html_path, validation_path=saved.validation_path, final_path=output_manager.run_dir / f"{slug}.html", visual_inspection_findings=inspection_findings)

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
        only redesigned.
        """
        refine = self._config.pipeline.refine
        if not refine.enabled or self._refiner is None:
            return False
        if state.best is None or state.best.evaluation is None:
            return False
        if refine.redesign_after_stalls and state.stalled >= refine.redesign_after_stalls:
            logger.info("Page '%s' stalled %d time(s): redesigning instead of refining", state.page.slug, state.stalled)
            return False
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

    def _build_generation_rules(self) -> list[str]:
        freedom = self._config.content.freedom
        return [
            f"Content freedom `{freedom}`: {content_freedom_policy(freedom)['authoring']}",
            f"Factual accuracy: {FACTUAL_ACCURACY_RULE}",
            f"Source language: {SOURCE_LANGUAGE_RULE}",
        ]

    def _finalize(self, result, output_manager):
        logger.info("Finalizing page '%s' from iteration %d", result.slug, result.iteration)
        final_path = output_manager.finalize(result.slug, fallback_html=result.html)
        logger.info("Final output ready: %s", final_path)
        return IterationResult(slug=result.slug, iteration=result.iteration, features=result.features, design_spec=result.design_spec, html=result.html, validation=result.validation, browser=result.browser, evaluation=result.evaluation, html_path=result.html_path, validation_path=result.validation_path, final_path=final_path)

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
        if best is not None and best >= self._config.pipeline.evaluation_threshold:
            logger.info("Page '%s': evaluation threshold reached: %.2f >= %.2f", slug, best, self._config.pipeline.evaluation_threshold)
            return True
        if termination.patience and state.stalled >= termination.patience:
            logger.info("Page '%s': no improvement in %d iteration(s); stopping at best %.2f", slug, state.stalled, best if best is not None else float("nan"))
            return True
        return False
