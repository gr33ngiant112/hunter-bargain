# Purchase Links in Alerts and CLI

Status: done. Shipped in 0.3.0 (commit 85d9ee1, 2026-03-03); this was the spec, formerly `FEATURES.md`.

## Description
When an alert fires, the email should include a prominent styled "Buy Now" call-to-action button instead of only a plain hyperlink in the HTML body. The existing plain text fallback in the email should keep the raw URL for mail clients that do not render HTML. The CLI `hb check` command should also print the best-result URL so users can copy or click it from terminal output.

## Current State
- `PriceCheckResult` already exposes `lowest_url`, so no schema changes are needed.
- Email notification code already includes `result.url` in HTML as a plain `<a>` link and in text fallback.
- CLI `check` output currently prints name, lowest price, source, and result count but does not print `lowest_url`.
- Search and filtering behavior is out of scope for this feature.

## Files To Modify
- `src/hunter_bargain/services/notifier.py`
  - Replace current plain HTML link rendering with a button-style CTA.
  - Keep text fallback output unchanged, including the URL line.
  - Guard rendering so CTA only appears when a URL exists.
- `src/hunter_bargain/cli.py`
  - In `check`, after the lowest-price summary line, print `  -> <url>` when `lowest_url` is present.
  - Do not print a URL line when `lowest_url` is missing.
- `tests/test_notifier.py`
  - Update assertions to validate CTA button HTML is present (for example text "Buy Now" and styled anchor/button attributes).
  - Preserve existing assertions for plain text fallback behavior.
- `tests/test_cli.py`
  - Update `check` command output tests to verify URL line is printed when API response has `lowest_url`.
  - Add or update test coverage for absence of URL line when `lowest_url` is `None`.

## Testing Requirements
- Run full test suite from project root in venv:
  - `. .venv/bin/activate`
  - `python -m pytest -v`
- Ensure notifier tests assert CTA content and do not regress plain text behavior.
- Ensure CLI tests assert both URL-present and URL-absent paths.
- Run lint checks after implementation:
  - `ruff check .`
  - `ruff format --check .`

## Acceptance Criteria
- Alert emails render a clear, styled, green, centered "Buy Now" CTA button in HTML body when a URL is available.
- Plain text email part still includes the URL exactly as fallback.
- `hb check` output prints a second indented URL line under the price line only when `lowest_url` is not `None`.
- Existing functionality remains unchanged outside purchase-link presentation.
- Test suite passes fully (59+ tests).
- Ruff lint and format checks pass with no new violations.

## Implementation Order
1. Create feature branch from `develop`.
2. Update notifier HTML template logic to render CTA button.
3. Update CLI `check` output to print URL line conditionally.
4. Update notifier and CLI tests to cover new output.
5. Run full pytest suite and fix any regressions.
6. Commit feature changes on branch and merge back to `develop` with `--no-ff`.
7. Run lint/format verification and commit lint-only fixes separately only if needed.
8. Clean up merged feature branches and rebuild Docker container.
