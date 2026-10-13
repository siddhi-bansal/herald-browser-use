"""python -m herald_browser_use signup EMAIL [PROJECT]: get a free Herald API key."""

import sys

import httpx

from . import get_api_key


def main(argv: list[str]) -> int:
	if len(argv) < 2 or argv[0] != 'signup':
		print('Usage: python -m herald_browser_use signup EMAIL ["what you\'re building"]', file=sys.stderr)
		return 2
	try:
		key = get_api_key(argv[1], argv[2] if len(argv) > 2 else None)
	except httpx.HTTPStatusError as e:
		print(f'Herald refused the sign-up ({e.response.status_code}): {e.response.text}', file=sys.stderr)
		return 1
	except httpx.HTTPError as e:
		print(f'Could not reach Herald: {e}', file=sys.stderr)
		return 1
	print(key)
	print('Save this key now (it is shown only once), e.g. export HERALD_API_KEY=' + key, file=sys.stderr)
	return 0


if __name__ == '__main__':
	sys.exit(main(sys.argv[1:]))
