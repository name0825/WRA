# WRA

WRA (Web Redesigner AI) analyzes an existing HTML page, plans a new visual design, generates a redesigned HTML document, validates it, and optionally evaluates the result in a browser and with an LLM.

## Pipeline

1. **HTML Analyzer** — deterministic extraction plus optional semantic analysis
2. **Design Planner** — converts the Feature Model into a `DesignSpec` (design intent, not implementation code)
3. **HTML Generator** — implements the `DesignSpec` as standalone HTML
4. **HTML Validator** — checks generated document integrity and structural requirements
5. **Browser Evaluation** *(optional)* — captures rendered evidence at configured viewports
6. **LLM Evaluation** — scores the generated page and provides improvement suggestions
7. **Iteration / Recovery** — retries invalid or incomplete model output and can revise the design across iterations

## Model-output boundaries

WRA treats source-derived page content, Feature Model values, DesignSpec artifacts, generated HTML, browser results, and evaluator feedback as **data rather than instructions**. Prompt-injection-like directives embedded in page content must not override the role-specific prompts or output contracts.

The Design Planner produces only JSON `DesignSpec` data; it must never generate HTML/CSS/JS. The HTML Generator is the implementation stage and returns raw HTML only.

## Recovery

Analyzer semantic analysis retries invalid or unappliable LLM output using compact repair guidance. Designer and Generator use the configured recovery policy for schema, validation, and token-limit failures. Token-limit recovery does not reuse truncated generator output.

## Content preservation

`requirements.must_preserve` is limited to verbatim source strings. Semantic descriptions that do not exist in the extracted source are treated as optional hints rather than hard visible-text requirements.

## Configuration

Copy `config/config.yaml.sample` to `config/config.yaml` and configure models, recovery policy, browser evaluation, frameworks, and pipeline iteration settings.

## Development

Run the test suite with your configured Python environment using `pytest`.
