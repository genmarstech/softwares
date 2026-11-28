"""The one place bots talk to Telegram and Meta. Tests patch ``post_json``."""

import json
import urllib.error
import urllib.request


class ChatAPIError(Exception):
    pass


def post_json(url, payload, headers=None, timeout=15):
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json', 'User-Agent': 'velora-bots', **(headers or {})},
        method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read() or b'{}')
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors='replace')[:500]
        raise ChatAPIError(f'{exc.code}: {detail}') from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise ChatAPIError(str(exc)) from exc
