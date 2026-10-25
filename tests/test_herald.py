import json
from importlib.metadata import version
from types import SimpleNamespace

import httpx
import pytest

from herald_browser_use import FrictionParams, __version__, herald_tips, herald_tools, report_failed_run


@pytest.fixture(autouse=True)
def no_herald_env(monkeypatch):
	for name in ('HERALD_URL', 'HERALD_API_KEY'):
		monkeypatch.delenv(name, raising=False)


def recording_client(status: int = 201):
	sent: list[dict] = []

	def handler(request: httpx.Request) -> httpx.Response:
		sent.append(
			{
				'url': str(request.url),
				'body': json.loads(request.content),
				'auth': request.headers.get('authorization'),
				'client': request.headers.get('x-herald-client'),
			}
		)
		return httpx.Response(status, json={'id': 'x'} if status < 400 else {'error': 'Invalid report'})

	return httpx.AsyncClient(transport=httpx.MockTransport(handler)), sent


def get_action(tools):
	return tools.registry.registry.actions['report_friction']


def test_registers_report_friction_with_page_url_injected():
	action = get_action(herald_tools())
	fields = set(action.param_model.model_fields)
	assert fields == {'goal', 'what_i_tried', 'element', 'what_went_wrong', 'error_shown', 'what_worked', 'severity'}
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
			'auth': None,
			'client': f'herald-browser-use/{__version__}',
			'body': {
				'site': 'www.kuhl.com',
				'page_url': 'https://www.kuhl.com/checkout?step=2',
				'channel': 'browser',
				'goal': 'Check out as a guest',
				'what_i_tried': 'Filled the address and clicked Continue',
				'element': "'USE THIS ADDRESS' button",
				'what_went_wrong': 'Nothing happened',
				'severity': 'blocking',
				'agent': f'browser-use {version("browser-use")}',
			},
		}
	]


async def test_reports_say_which_agent_and_browser_sent_them():
	client, sent = recording_client()
	action = get_action(herald_tools(herald_url='https://h.test', client=client))
	params = FrictionParams(goal='g', what_i_tried='t', what_went_wrong='w', severity='high')
	llm = SimpleNamespace(model='claude-sonnet-5-5')

	def session(**profile):
		return SimpleNamespace(browser_profile=SimpleNamespace(**{'use_cloud': False, 'cdp_url': None, 'headless': None, **profile}))

	for profile, expected in [({'headless': True}, 'cloud'), ({'use_cloud': True}, 'cloud'), ({'headless': False}, 'user'), ({'cdp_url': 'ws://x'}, 'unknown')]:
		await action.function(params=params, page_url='https://acme.com/', browser_session=session(**profile), page_extraction_llm=llm)
		assert sent[-1]['body']['browser'] == expected
	assert sent[-1]['body']['agent'] == f'browser-use {version("browser-use")}, claude-sonnet-5-5'


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
		goal='Check out',
		what_i_tried='Entered Jane Doe and jane@x.com',
		what_went_wrong='w',
		what_worked='Retyped Jane Doe and clicked Continue again',
		severity='low',
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


async def test_on_report_sees_the_exact_body_and_errors_never_escape():
	client, sent = recording_client(status=400)
	seen = []

	async def log(body, error):
		seen.append((body, error))
		raise RuntimeError('a broken hook must not break the run')

	action = get_action(herald_tools(client=client, on_report=log))
	params = FrictionParams(goal='g', what_i_tried='mail jane@x.com', what_went_wrong='w', severity='low')
	result = await action.function(params=params, page_url='https://acme.com/')
	assert seen[0][0] == sent[0]['body']
	assert seen[0][0]['what_i_tried'] == 'mail [email]'
	assert '400' in seen[0][1]
	assert result.error and '400' in result.error


async def test_api_key_is_sent_so_reports_are_credited(monkeypatch):
	client, sent = recording_client()
	params = FrictionParams(goal='g', what_i_tried='t', what_went_wrong='w', severity='low')
	await get_action(herald_tools(client=client, api_key='k1')).function(params=params, page_url='https://acme.com/')
	monkeypatch.setenv('HERALD_API_KEY', 'k2')
	await get_action(herald_tools(client=client)).function(params=params, page_url='https://acme.com/')
	await report_failed_run(fake_agent(), goal='g', client=client)
	assert [s['auth'] for s in sent] == ['Bearer k1', 'Bearer k2', 'Bearer k2']


def tips_client(status: int = 200, tips=None):
	seen: list[httpx.Request] = []

	def handler(request: httpx.Request) -> httpx.Response:
		seen.append(request)
		if status >= 400:
			return httpx.Response(status, json={'error': 'Unauthorized'})
		prompt = 'Known problems other AI agents reported on kuhl.com in the last 30 days.\n- [blocking] ...'
		return httpx.Response(200, json={'site': 'kuhl.com', 'tips': tips or [], 'prompt': prompt})

	return httpx.AsyncClient(transport=httpx.MockTransport(handler)), seen


async def test_tips_returns_prompt_to_append_to_the_task():
	client, seen = tips_client(tips=[{'severity': 'blocking'}])
	text = await herald_tips('https://www.kuhl.com/checkout', api_key='k', herald_url='https://h.test', client=client)
	assert text.startswith('\n\nKnown problems other AI agents reported on kuhl.com')
	assert str(seen[0].url) == 'https://h.test/api/tips?site=www.kuhl.com'
	assert seen[0].headers['authorization'] == 'Bearer k'


async def test_tips_are_empty_when_there_are_none_or_on_errors(caplog):
	client, _ = tips_client(tips=[])
	assert await herald_tips('kuhl.com', api_key='k', client=client) == ''
	client, _ = tips_client(status=401)
	assert await herald_tips('kuhl.com', api_key='bad', client=client) == ''
	client, seen = tips_client(tips=[{'severity': 'blocking'}])
	assert await herald_tips('kuhl.com', client=client) == ''  # no key: don't even ask
	assert seen == []
	assert 'no API key' in caplog.text and '401' in caplog.text


async def test_failed_run_reports_say_where_they_came_from():
	client, sent = recording_client()
	await report_failed_run(fake_agent(), goal='g', client=client)
	assert sent[0]['client'] == f'herald-browser-use/{__version__} (report_failed_run)'


def test_get_api_key_signs_up_and_returns_the_key(monkeypatch):
	import herald_browser_use

	calls = []

	def fake_post(url, json, headers, timeout):
		calls.append((url, json, headers))
		return httpx.Response(201, json={'key': 'hk_abc', 'name': 'shop-1a2b'}, request=httpx.Request('POST', url))

	monkeypatch.setattr(herald_browser_use.httpx, 'post', fake_post)
	assert herald_browser_use.get_api_key('me@x.com', 'Shop', herald_url='https://h.test') == 'hk_abc'
	assert calls == [('https://h.test/api/keys', {'email': 'me@x.com', 'project': 'Shop'}, {'X-Herald-Client': herald_browser_use.CLIENT})]
