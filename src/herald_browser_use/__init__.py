"""Herald for browser-use: let your agent tell websites where it got stuck.

Two ways to send reports, use either or both:

- ``herald_tools()`` gives the agent a ``report_friction`` action it calls
  itself when it gets stuck, slowed down, or has to work around something.
- ``report_failed_run()`` sends one report after a run that failed, built
  from the run's history, for failures the agent didn't report itself.

Reports are sent to Herald (https://github.com/siddhi-bansal/herald-browser-use).
"""

import os
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlparse

import httpx
from browser_use import ActionResult, Tools
from pydantic import BaseModel, Field

if TYPE_CHECKING:
	from browser_use import Agent

__all__ = ['DEFAULT_HERALD_URL', 'FrictionParams', 'herald_tools', 'report_failed_run']

DEFAULT_HERALD_URL = 'https://herald-api.siddhibansal2604.workers.dev'

# Kept in sync with report_friction in Herald's shared tool definition.
TOOL_DESCRIPTION = (
	"Report a problem you ran into while using a website, so the site's owners can fix it. "
	'Call this whenever you get stuck, are slowed down, or have to work around something on a website: '
	"information you couldn't find (e.g. shipping costs, return policy, sizes), a button or link that "
	"didn't work, a form that rejected valid input, a checkout or payment error, a confusing layout, or "
	'anything that made the task harder than it should be. Report even if you eventually succeeded. '
	'Be specific and factual: name the exact button or field involved and copy any error text word for word. '
	'Do not include personal data such as card numbers, passwords or addresses.'
)

Severity = Literal['low', 'medium', 'high', 'blocking']


class FrictionParams(BaseModel):
	"""What the model fills in. The site and page URL are taken from the browser.

	No length limits here on purpose: browser-use sends this schema to the model
	(in strict mode for OpenAI), and length constraints can be rejected by the
	provider or bloat strict tool grammars. _send() truncates to Herald's limits.
	"""

	goal: str = Field(
		description='What you were trying to accomplish, e.g. "Find the shipping cost to Canada before buying".',
	)
	what_i_tried: str = Field(
		description='The concrete steps you took, in order, e.g. "Searched the product page, opened the FAQ, '
		"clicked 'Shipping' in the footer\".",
	)
	element: str | None = Field(
		default=None,
		description='The exact button, link or form field involved, named as it appears on the page, e.g. '
		"\"'Continue to payment' button\" or \"'ZIP code' field\". Leave out if the problem wasn't about one "
		"element (e.g. information you couldn't find).",
	)
	what_went_wrong: str = Field(description='What actually happened and why it was a problem.')
	error_shown: str | None = Field(
		default=None,
		description='Any error or warning the page showed, copied word for word. Write "none" if the page showed no error.',
	)
	severity: Severity = Field(
		description='low = minor annoyance; medium = slowed you down; high = needed a workaround or guess; '
		'blocking = you could not complete the task.'
	)


def _herald_url(herald_url: str | None) -> str:
	return (herald_url or os.environ.get('HERALD_URL') or DEFAULT_HERALD_URL).rstrip('/')


def _site_for(page_url: str, site: str | None) -> str:
	return site or urlparse(page_url).hostname or page_url


# Herald's per-field limits; longer text is cut rather than rejected.
MAX_LENGTHS = {
	'site': 253,
	'page_url': 2048,
	'goal': 500,
	'what_i_tried': 4000,
	'element': 300,
	'what_went_wrong': 4000,
	'error_shown': 1000,
}


async def _send(report: dict[str, Any], herald_url: str | None, client: httpx.AsyncClient | None) -> str | None:
	"""POST a report to Herald. Returns an error message, or None on success. Never raises."""
	body = {k: v[: MAX_LENGTHS[k]] if k in MAX_LENGTHS else v for k, v in report.items() if v is not None}
	try:
		if client is not None:
			res = await client.post(f'{_herald_url(herald_url)}/api/reports', json=body)
		else:
			async with httpx.AsyncClient(timeout=10) as own_client:
				res = await own_client.post(f'{_herald_url(herald_url)}/api/reports', json=body)
	except httpx.HTTPError as e:
		return f'Could not reach Herald: {e}'
	if res.status_code >= 400:
		return f'Herald rejected the report ({res.status_code}): {res.text[:500]}'
	return None


def herald_tools(
	tools: Tools | None = None,
	*,
	site: str | None = None,
	herald_url: str | None = None,
	client: httpx.AsyncClient | None = None,
) -> Tools:
	"""Add a ``report_friction`` action to ``tools`` (or to a new ``Tools()``) and return it.

	Args:
		tools: Your existing Tools, if you have one.
		site: Report every page under this site name instead of the page's hostname.
		herald_url: Where to send reports. Defaults to $HERALD_URL, then Herald's public API.
		client: An httpx.AsyncClient to send with (mainly for tests).
	"""
	tools = tools if tools is not None else Tools()

	# No `from __future__ import annotations` in this file: browser-use matches
	# injected parameters like page_url by their real (non-string) annotation.
	@tools.action(TOOL_DESCRIPTION, param_model=FrictionParams)
	async def report_friction(params: FrictionParams, page_url: str) -> ActionResult:
		report = {'site': _site_for(page_url, site), 'page_url': page_url, **params.model_dump()}
		error = await _send(report, herald_url, client)
		if error:
			return ActionResult(error=f'{error}. Continue your task.')
		return ActionResult(
			extracted_content="Thanks, your report was recorded and will be shared with the site's owners. "
			'You can continue your task.',
			long_term_memory=f'Reported friction on {page_url}: {params.what_went_wrong[:200]}',
		)

	return tools


def _failed(agent: "Agent") -> bool:
	history = agent.history
	validated = history.is_validated()  # the judge's verdict, when use_judge is on
	if validated is not None:
		return not validated
	return history.is_successful() is not True


async def report_failed_run(
	agent: "Agent",
	*,
	goal: str,
	site: str | None = None,
	herald_url: str | None = None,
	skip_if_reported: bool = True,
	client: httpx.AsyncClient | None = None,
) -> bool:
	"""After ``await agent.run()``, report the run to Herald if it failed.

	A run counts as failed if the judge says so (with ``use_judge=True``) or,
	without a judge, if the agent didn't finish successfully.

	Args:
		agent: The agent, after its run.
		goal: The task in general terms, without personal details, e.g.
			"Buy one item as a guest and reach the payment page". The agent's own
			task text isn't sent, because it often contains names and addresses.
		site: Report under this site name instead of the last page's hostname.
		herald_url: Where to send reports. Defaults to $HERALD_URL, then Herald's public API.
		skip_if_reported: Don't send if the agent already called report_friction.
		client: An httpx.AsyncClient to send with (mainly for tests).

	Returns:
		True if a report was sent.
	"""
	history = agent.history
	if not _failed(agent):
		return False
	if skip_if_reported and 'report_friction' in history.action_names():
		return False

	page_url = next((u for u in reversed(history.urls()) if u and u.startswith('http')), None)
	if page_url is None:
		return False  # never reached a website

	actions = [a for a in history.action_names() if a != 'done']
	what_i_tried = f'Ran {history.number_of_steps()} steps. Last actions: {", ".join(actions[-8:]) or "none"}.'

	judgement = history.judgement() or {}
	what_went_wrong = (
		judgement.get('failure_reason')
		or (history.final_result() if history.is_done() else None)
		or 'The agent stopped before finishing the task.'
	)

	error = await _send(
		{
			'site': _site_for(page_url, site),
			'page_url': page_url,
			'goal': goal,
			'what_i_tried': what_i_tried,
			'what_went_wrong': what_went_wrong,
			'severity': 'blocking',
		},
		herald_url,
		client,
	)
	return error is None
