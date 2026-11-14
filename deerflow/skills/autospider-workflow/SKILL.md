---
name: autospider-workflow
description: Compile, register, execute, observe, and stop deterministic HTTP HTML, HTTP API, or Playwright workflows through the AutoSpider MCP boundary.
allowed-tools:
  - autospider_memory_lookup_site
  - autospider_crawl_reuse_workflow
  - autospider_probe_http_select
  - autospider_crawl_draft_create
  - autospider_crawl_draft_execute
  - autospider_crawl_plan_build
  - autospider_task_compile
  - autospider_task_compile_register
  - autospider_crawl_execute_validated
  - autospider_task_register
  - autospider_task_get
  - autospider_execution_create
  - autospider_execution_start
  - autospider_execution_logs
  - autospider_execution_stop
  - autospider_execution_result
  - autospider_execution_result_summary
  - autospider_qa_validate_execution
---

# AutoSpider Workflow

Load only when execution semantics need clarification, not together with all other skills. Crawler Lead's six compact tools are pre-bound; no `tool_search` is needed for memory/reuse, HTTP overview/select or draft create/execute.

Known site: query memory once with `include_page_model=false`. Compatible same-domain/path fields may reuse changed query parameters or a field subset via one `crawl_reuse_workflow` call. Omit `field_names` to keep saved fields. Do not fetch saved models or invent a record limit for a page-only request; use a draft when necessary. Reuse performs sample/QA/full internally; PASS needs no second execution or memory commit.

New site: create a draft from verified compact evidence. `probe_http_select` checks the COMPLETE source; test details from a list article link directly, with another overview only if structure evidence is missing. HTML uses XPath/CSS; `http_api` uses tested JSONPath and non-secret `http_request`; Playwright handles browser-only behavior. Usual names are title/url/publish_time/content; custom fields remain supported. `field_selectors` includes EVERY requested field. `detail_fields` contains unique field NAMES already present there, never selectors. Structural example only (replace placeholders with verified selectors, not a website preset):

```json
{"item_selector":"<verified list item selector>","field_selectors":{"title":"<item-relative title selector>","url":"<item-relative link selector>","content":"<verified detail-body selector>"},"detail_fields":["content"],"detail_link_selector":"<item-relative link selector>"}
```

List and link selectors are item-relative; the content selector runs on the opened detail page. HTML URL fields default to href extraction. `page_limit=2` without `item_limit` means two observed list pages, not 50 guessed records. A structured `validation_error` permits one correction using its listed parameter/example, not schema reverse-engineering by trial.

HTML selectors use XPath 1.0/standard CSS, not re:test/fn: prefixes, XPath 2.0 or variables. Read status/code/parameter/field_errors/next_action; keep valid partial evidence and correct only failed parameters once. Do not switch mode for syntax errors, repeat unchanged calls, treat zero matches as coverage or load full artifacts because previews are bounded. Eligible GET probes may retry once; POST never automatically. Expired browser probes permit one reopen with the original verified URL and new session_id, never a new crawl execution. Stop on internal errors and preserve durable IDs/cache semantics.

`crawl_draft_execute(draft_id)` builds/compiles/registers, samples (default 5), validates, runs full after PASS, performs final QA and commits validated memory. Explicit record caps no greater than the sample may run once; page-only requests remain sample-first. Returns compact IDs/QA/paths. Completed or failed repeats return saved results; timeout resumes the SAME saved execution/phase. One execution owner; no concurrent same-ID calls. QA FAIL repairs create a new draft. Never rerun/revalidate/recommit PASS.

Pagination uses observed link/button elements. `{{page_number}}` is current page, starting 1; default templates navigate before extraction. Special homepages use `pagination_after_extract=true`; a page-2 filename uses `{{next_page_number}}`, while observed `index_1.shtml` after the homepage may use current page numbering. Final iteration skips unused next navigation. A 404 permits at most one evidence-driven alternative, never template guesses.

Advanced typed compiler/lifecycle/QA tools remain available: discover only the specifically needed tool by exact name. Keep task and execution IDs separate; wait on existing legacy executions, never restart them. Use bounded result summaries/server QA, not full `.tool-results`, plans, compiled tasks or result bodies.

Policy/domain blocks are not selector or QA repair failures. Stop all transport retries, request explicit authority and label partial records incomplete; do not drop off-domain articles to claim all pages completed. Do not search typed repair tools for a policy block.

The server's `site` scope permits same registrable-site subdomains, respecting public/private suffix boundaries. Trusted Clash Fake-IP is checked automatically in `verified_public` mode; do not request repetitive per-host authorization for accepted siblings. Unrelated sites and failed private/public-DNS checks remain policy blocks. Explicit custom domain whitelists stay authoritative; the agent cannot change proxy mode or disable guards.

`pages_visited` counts total visits including details, not completed list pages. Failed drafts are immutable cached terminals; changed domain authority needs a new authorized plan/draft, not retries of that ID. Timeout resumes are different: only a still-running saved execution can continue.

Advanced `execution_create.parameters` supports URL overrides, delays, time budgets/user agents and non-secret HTTP headers/params/JSON/form. Playwright also supports browser, locale, timezone, proxy and storage-state references. Prefer a storage-state file under `AUTOSPIDER_RUNTIME_ROOT`; never persist credentials, cookies, keys or signatures in PageModel/Site Memory. Policy and private-network blocks must remain enabled in every mode.
