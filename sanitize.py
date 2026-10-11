"""Defence against exfiltration through the answer text.

If a prompt injection makes the model emit a Markdown image or link, the browser
would fetch it and leak data in the URL. So the answer is sanitised before display:

* Markdown images are removed;
* Markdown links are reduced to their text;
* a bare URL is kept only when its host is ``lumenpay.example`` or a subdomain;
* HTML is escaped.

Clickable links are rendered only in the citation cards, whose URLs the UI builds
from document metadata — never from model output.
"""
import html
import re
from urllib.parse import urlsplit

IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
BARE_URL = re.compile(r"https?://[^\s)>\]]+")
ALLOWED_HOST = "lumenpay.example"


def _keep_url(match):
    host = (urlsplit(match.group(0)).hostname or "").lower()
    return match.group(0) if host == ALLOWED_HOST or host.endswith("." + ALLOWED_HOST) else ""


def sanitize_answer(text):
    text = IMAGE.sub("", text or "")
    text = LINK.sub(r"\1", text)
    text = BARE_URL.sub(_keep_url, text)
    return html.escape(text)
