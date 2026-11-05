"""In-app browsers (Instagram, TikTok, Facebook, Android WebView) get `disallowed_useragent` from Google."""

from __future__ import annotations

import re

_IN_APP = re.compile(
    r"Instagram|FBAN|FBAV|FB_IAB|musical_ly|TikTok|Bytedance|trill_|Snapchat|Line/|; ?wv\)",
    re.IGNORECASE,
)


def is_in_app_browser(user_agent: str | None) -> bool:
    return bool(user_agent) and _IN_APP.search(user_agent) is not None
