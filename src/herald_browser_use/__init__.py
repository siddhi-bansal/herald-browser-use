"""Herald for browser-use: let your agent tell websites where it got stuck.

``herald_tools()`` gives the agent a ``report_friction`` action it calls
itself when it gets stuck, slowed down, or has to work around something.

Reports go to Herald (https://github.com/siddhi-bansal/herald-browser-use),
which groups them by website and passes them on to the site's owners.
"""

import os
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from browser_use import ActionResult, Tools
from pydantic import BaseModel, Field

__all__ = ['DEFAULT_HERALD_URL', 'FrictionParams', 'herald_tools']

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
	"""What the model fills in. The site and page URL are taken from the browser."""

	goal: str = Field(
		max_length=500,
		description='What you were trying to accomplish, e.g. "Find the shipping cost to Canada before buying".',
	)
	what_i_tried: str = Field(
		max_length=4000,
		description='The concrete steps you took, in order, e.g. "Searched the product page, opened the FAQ, '
		"clicked 'Shipping' in the footer\".",
	)
	element: str | None = Field(
		default=None,
		max_length=300,
		description='The exact button, link or form field involved, named as it appears on the page, e.g. '
		"\"'Continue to payment' button\" or \"'ZIP code' field\". Leave out if the problem wasn't about one "
		"element (e.g. information you couldn't find).",
	)
	what_went_wrong: str = Field(max_length=4000, description='What actually happened and why it was a problem.')
	error_shown: str | None = Field(
		default=None,
		max_length=1000,
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


async def _send(report: dict[str, Any], herald_url: str | None, client: httpx.AsyncClient | None) -> str | None:
	"""POST a report to Herald. Returns an error message, or None on success. Never raises."""
	body = {k: v for k, v in report.items() if v is not None}
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
