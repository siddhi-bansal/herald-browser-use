import json
from types import SimpleNamespace

import httpx
import pytest

from herald_browser_use import FrictionParams, herald_tools, report_failed_run


def recording_client(status: int = 201):
	sent: list[dict] = []

	def handler(request: httpx.Request) -> httpx.Response:
		sent.append({'url': str(request.url), 'body': json.loads(request.content)})
		return httpx.Response(status, json={'id': 'x'} if status < 400 else {'error': 'Invalid report'})

	return httpx.AsyncClient(transport=httpx.MockTransport(handler)), sent


def get_action(tools):
	return tools.registry.registry.actions['report_friction']


def test_registers_report_friction_with_page_url_injected():
	action = get_action(herald_tools())
	fields = set(action.param_model.model_fields)
	assert fields == {'goal', 'what_i_tried', 'element', 'what_went_wrong', 'error_shown', 'severity'}
	assert 'page_url' not in fields  # browser-use fills it from the current page
	assert 'site owners' in action.description or "site's owners" in action.description


def test_adds_to_existing_tools():
	from browser_use import Tools

	tools = Tools()
	assert herald_tools(tools) is tools
	assert 'report_friction' in tools.registry.registry.actions


async def test_action_posts_report_with_site_from_page():
	client, sent = recording_client()
	action = get_action(herald_tools(herald_url='https://h.test', client=client))
	params = FrictionParams(
		goal='Check out as a guest',
		what_i_tried='Filled the address and clicked Continue',
		element="'USE THIS ADDRESS' button",
		what_went_wrong='Nothing happened',
		severity='blocking',
	)
	result = await action.function(params=params, page_url='https://www.kuhl.com/checkout?step=2')
	assert result.error is None
	assert sent == [
		{
			'url': 'https://h.test/api/reports',
			'body': {
				'site': 'www.kuhl.com',
				'page_url': 'https://www.kuhl.com/checkout?step=2',
				'goal': 'Check out as a guest',
				'what_i_tried': 'Filled the address and clicked Continue',
				'element': "'USE THIS ADDRESS' button",
				'what_went_wrong': 'Nothing happened',
				'severity': 'blocking',
			},
		}
	]


async def test_action_returns_error_instead_of_raising():
	client, _ = recording_client(status=400)
	action = get_action(herald_tools(client=client))
	params = FrictionParams(goal='g', what_i_tried='t', what_went_wrong='w', severity='low')
	result = await action.function(params=params, page_url='https://acme.com/')
	assert result.error and '400' in result.error


def fake_agent(*, successful=False, validated=None, actions=('navigate', 'click', 'done'), urls=None, judgement=None):
	history = SimpleNamespace(
		is_validated=lambda: validated,
		is_successful=lambda: successful,
		is_done=lambda: True,
		final_result=lambda: 'Could not continue past the address step',
		action_names=lambda: list(actions),
		urls=lambda: urls if urls is not None else ['about:blank', 'https://acme.com/', 'https://acme.com/checkout'],
		number_of_steps=lambda: 12,
		judgement=lambda: judgement,
	)
	return SimpleNamespace(history=history, task='Buy a mug and ship it to Jane Doe, 1 Main St')


async def test_reports_failed_run_without_task_text():
	client, sent = recording_client()
	ok = await report_failed_run(fake_agent(), goal='Buy one item as a guest', client=client)
	assert ok
	body = sent[0]['body']
	assert body['site'] == 'acme.com'
	assert body['page_url'] == 'https://acme.com/checkout'
	assert body['goal'] == 'Buy one item as a guest'
	assert 'Jane' not in json.dumps(body)
	assert body['what_i_tried'] == 'Ran 12 steps. Last actions: navigate, click.'
	assert body['what_went_wrong'] == 'Could not continue past the address step'
	assert body['severity'] == 'blocking'


async def test_prefers_judge_verdict_and_reason():
	client, sent = recording_client()
	agent = fake_agent(successful=True, validated=False, judgement={'failure_reason': 'Quantity was 2, not 1'})
	assert await report_failed_run(agent, goal='g', client=client)
	assert sent[0]['body']['what_went_wrong'] == 'Quantity was 2, not 1'


@pytest.mark.parametrize(
	'agent',
	[
		fake_agent(successful=True),
		fake_agent(successful=False, validated=True),
		fake_agent(actions=('navigate', 'report_friction', 'done')),
		fake_agent(urls=[None, 'about:blank']),
	],
	ids=['succeeded', 'judge-approved', 'already-reported', 'no-website'],
)
async def test_skips_runs_that_need_no_report(agent):
	client, sent = recording_client()
	assert not await report_failed_run(agent, goal='g', client=client)
	assert sent == []


def test_schema_sent_to_the_model_has_no_length_limits():
	# Length constraints can be rejected by providers' strict modes or bloat their
	# tool grammars; Herald's limits are applied when sending instead.
	schema = json.dumps(FrictionParams.model_json_schema())
	assert 'maxLength' not in schema and 'minLength' not in schema


async def test_long_text_is_truncated_not_rejected():
	client, sent = recording_client()
	action = get_action(herald_tools(client=client))
	params = FrictionParams(goal='g' * 900, what_i_tried='t', what_went_wrong='w' * 5000, severity='low')
	await action.function(params=params, page_url='https://acme.com/')
	assert len(sent[0]['body']['goal']) == 500
	assert len(sent[0]['body']['what_went_wrong']) == 4000


def test_scrub_removes_personal_data_and_keeps_the_rest():
	from herald_browser_use import _redact_values, _scrub

	values = _redact_values({'name': 'Jane Doe', 'address': '1 Market St', 'shop.com': {'zip': '94105'}})
	text = (
		'Typed Jane Doe, 1 MARKET ST, 94105, jane@x.com, (415) 555-0123 and card 4242 4242 4242 4242. '
		'Price $120.00, qty 2, ZIP+4 94105-1234 on 2026-10-10.'
	)
	out = _scrub(text, values)
	assert out == (
		'Typed [redacted], [redacted], [redacted], [email], [phone] and card [card number]. '
		'Price $120.00, qty 2, ZIP+4 [redacted]-1234 on 2026-10-10.'
	)


async def test_reports_are_scrubbed_before_sending():
	client, sent = recording_client()
	action = get_action(herald_tools(client=client, redact=['Jane Doe']))
	params = FrictionParams(
		goal='Check out', what_i_tried='Entered Jane Doe and jane@x.com', what_went_wrong='w', severity='low'
	)
	await action.function(params=params, page_url='https://acme.com/?email=jane@x.com')
	body = json.dumps(sent[0]['body'])
	assert 'Jane' not in body and 'jane@x.com' not in body
	assert sent[0]['body']['site'] == 'acme.com'


def test_scrub_keeps_ids_that_are_not_card_numbers():
	from herald_browser_use import _scrub

	# 4242... passes the card checksum; a Shopify variant id usually doesn't.
	assert _scrub('variant 44012345678902 and card 4242424242424242', []) == "variant 44012345678902 and card [card number]"


async def test_page_url_keeps_its_ids_but_loses_emails():
	client, sent = recording_client()
	action = get_action(herald_tools(client=client, redact=['Jane Doe']))
	params = FrictionParams(goal='g', what_i_tried='t', what_went_wrong='w', severity='low')
	await action.function(params=params, page_url='https://acme.com/products/x?variant=4242424242424242&email=jane@x.com')
	assert sent[0]['body']['page_url'] == 'https://acme.com/products/x?variant=4242424242424242&email=[email]'
