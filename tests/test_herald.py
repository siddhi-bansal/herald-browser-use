import json

import httpx

from herald_browser_use import FrictionParams, herald_tools


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
