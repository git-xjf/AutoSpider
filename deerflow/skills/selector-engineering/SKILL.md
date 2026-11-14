---
name: selector-engineering
description: Discover, test, rank, and repair robust XPath selectors for crawl fields, repeated list items, detail links, and pagination.
allowed-tools:
  - autospider_probe_http_select
  - autospider_selector_test
  - autospider_probe_snapshot
  - autospider_network_jsonpath_test
---

# Selector Engineering

For HTML, rank candidates by stable semantic ID, data/ARIA attributes, stable class relations, relative XPath, attribute/text XPath, then structural XPath. Reject absolute `/html/body/...` paths unless no tested alternative exists. For JSON APIs, use deterministic JSONPath only: dotted keys, bracket keys, numeric indexes and `[*]`; filters or executable expressions are forbidden. A field selector must be tested in its real scope. Record match count and samples; never infer success from syntax validity alone.

HTTP HTML selectors use XPath 1.0 or standard CSS. Do not use `re:test`, `fn:`, `matches`, `ends-with`, variables or browser-only pseudo selectors. For XPath choose tested structural predicates, `contains`, `starts-with` or `substring`; do not silently approximate regex semantics. Set one matching selector_type; scope_selector must select item elements, not attributes, text or scalars.

Read status/code/parameter/field_errors/hints. Partial valid fields remain useful evidence, not complete validation. Correct only failed parameters once; never retry unchanged selectors, reload all schemas or change transport for syntax errors. A valid zero count means no match, not successful field coverage. JSON responses require public-API JSONPath evidence. Bound previews retain counts over the complete source; do not load full bodies to compensate. Expired sessions allow one bounded probe reopen, not execution restart; policy/internal failures stop repairs.
