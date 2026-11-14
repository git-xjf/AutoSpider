# AutoSpider workspace guidance

## Repository and runtime

- This is the `git-xjf/AutoSpider` monorepo. Both AutoSpider and the customized
  `deer-flow/` source are tracked in the same Git repository; DeerFlow is not a
  submodule. Its reviewed upstream revision is recorded in `UPSTREAM.lock` and
  `deer-flow/UPSTREAM_VENDOR.json`. Preserve its MIT license and local changes.
- `deerflow/` contains AutoSpider's Agent, Skill and configuration templates.
  `deer-flow/` contains the actual framework. Read its `AGENTS.md` and the
  corresponding module guides before changing framework code.
- AutoSpider runs in Python 3.11; the DeerFlow backend uses its own uv-managed
  Python 3.12 environment. The native crawler does not require EasySpider Desktop.
- Start with `README.md`, `docs/GITHUB_WORKFLOW.md`, and the Work Buddy handover
  guide linked from the README.

## Two-computer collaboration

- Before editing, inspect `git status` and the current branch. If clean, update
  with `git pull --ff-only`; do not overwrite existing local work to make it pass.
- Keep related changes together. Review the exact diff and staged file list;
  never stage local credentials, generated configuration, databases, user
  histories, dependency directories or crawl outputs.
- Use a descriptive commit title and a body explaining the reason, changes and
  validation. A template is provided in `.github/commit_template.txt`.
- Commit and push when the user's active request authorizes it. Do not create
  code changes or commits for a discussion-only question. Do not force-push or
  rewrite shared history to resolve an ordinary synchronization conflict.
- Prefer a fresh clone on the second computer over overlaying the old zip.
  Local Python paths, environment variables and generated configuration must be
  bound separately on each computer through the existing bootstrap workflow.

## Verification and evidence

- Run checks appropriate to the change. For AutoSpider changes, use the
  AutoSpider interpreter and isolated pytest fixtures; a convenient smoke check
  is `python scripts/smoke_test.py`. Follow the stricter nested module rules when
  changing DeerFlow.
- Keep documentation, maintained templates and deployed user copies consistent
  when changing agent behavior. Do not commit the machine-specific deployed
  copies under `.deer-flow/`.
- Preserve historical experiment records and distinguish controlled fixtures,
  real websites and real-model measurements. Do not claim a new mechanism,
  patent advantage, success rate or API saving without supporting comparisons.
- Preserve existing domain, robots and private-network policies. Parameter or
  selector failures do not authorize bypassing access restrictions.
