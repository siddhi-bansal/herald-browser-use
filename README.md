# herald-browser-use

Let your [browser-use](https://github.com/browser-use/browser-use) agent report where it got stuck on a website.

## Install

```bash
pip install herald-browser-use
```

## Use

```python
from browser_use import Agent, ChatAnthropic
from herald_browser_use import herald_tools, report_failed_run

agent = Agent(
    task="Find a ceramic mug, add it to the cart and check out as a guest",
    llm=ChatAnthropic(model="claude-sonnet-5-5"),
    tools=herald_tools(),  # or herald_tools(your_tools) to add to tools you already have
)
await agent.run()

# Optional: also report the run if it failed and the agent didn't report it itself.
await report_failed_run(agent, goal="Buy one item as a guest")
```

That's it. There's nothing to sign up for.

### What the agent gets

`herald_tools()` adds one action, `report_friction`. The model decides when to call it, from its description: whenever it gets stuck, slowed down, or has to work around something on a site. It fills in:

| Field | Example |
|---|---|
| `goal` | Check out as a guest |
| `what_i_tried` | Filled in the address and clicked Continue |
| `element` (optional) | 'USE THIS ADDRESS' button |
| `what_went_wrong` | Nothing happened; checkout didn't move on |
| `error_shown` (optional) | none |
| `severity` | `low`, `medium`, `high` or `blocking` |

The site and page URL come from the browser, not the model. To make reporting more reliable, add a line to your task, e.g. *"If you get stuck on a website, call report_friction."*

### Reporting failed runs

`report_failed_run(agent, goal=...)` sends one `blocking` report after a run that failed. It uses the judge's verdict and failure reason when you run with `use_judge=True`, and otherwise whether the agent finished successfully. It skips runs where the agent already called `report_friction`.

You pass `goal` yourself, in general terms, because the agent's task text often contains names and addresses. The task text is never sent.

## What gets sent

Each report is a small JSON object with the fields above plus the site and page URL. It goes to `https://herald-api.siddhibansal2604.workers.dev/api/reports`. Nothing else is sent: no screenshots, page content, browsing history or task text.

Personal data is removed before anything is sent:

- The model is told not to include personal data such as card numbers, passwords or addresses.
- Emails, phone numbers and card numbers are replaced automatically.
- Pass the values your agent types into forms, and they're replaced too:

  ```python
  herald_tools(redact=["Jane Doe", "1 Market St", "jane@example.com"])
  # or the same dict you give Agent(sensitive_data=...)
  herald_tools(redact=sensitive_data)
  ```

Reporting never interrupts your agent. If Herald can't be reached, the action returns an error to the model and the run continues.

## Options

```python
herald_tools(tools=None, *, site=None, herald_url=None, redact=None)
report_failed_run(agent, *, goal, site=None, herald_url=None, skip_if_reported=True, redact=None)
```

- `site`: report under a name of your choice instead of the page's hostname.
- `herald_url`: send reports somewhere else. The `HERALD_URL` environment variable also works.
- `redact`: values to remove from reports (see above).

### Strict tool schemas

If you make Anthropic's API enforce tool schemas strictly (`"strict": true`), every extra action adds to a compiled grammar with a size limit, and browser-use's full default action set can exceed it on its own. If you see *"The compiled grammar is too large"*, exclude a few actions you don't need, e.g. `Tools(exclude_actions=["switch", "close", "go_back"])`, before passing them to `herald_tools()`. browser-use doesn't use strict tool schemas with Anthropic by default.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e ".[test]"
.venv/bin/python -m pytest
```

MIT licensed.
