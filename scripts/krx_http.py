"""Bound pykrx's HTTP waits without changing its authentication or response data.

The pinned pykrx webio transport uses requests.Session for authenticated and
anonymous reads, but supplies no timeout. Keep that vendor adaptation here.
"""
from functools import wraps

from requests.adapters import HTTPAdapter

TIMEOUT = (10, 45)  # connect / read inactivity, seconds


class BoundedAdapter(HTTPAdapter):
    def send(self, request, **kwargs):
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = TIMEOUT
        return super().send(request, **kwargs)


def bound_session(session):
    for prefix in ("http://", "https://"):
        if not isinstance(session.get_adapter(prefix), BoundedAdapter):
            session.mount(prefix, BoundedAdapter())
    return session


class RequestsTransport:
    def __init__(self, original):
        self.original = original

    def Session(self):
        return bound_session(self.original.Session())

    def __getattr__(self, name):
        return getattr(self.original, name)


def configure(webio=None):
    if webio is None:
        from pykrx.website.comm import webio
    if isinstance(webio.requests, RequestsTransport):
        return
    original = webio.get_session

    @wraps(original)
    def current_session():
        krx = original()  # preserves cookie refresh and explicit login timeouts
        if krx is not None:
            bound_session(krx.session)
        return krx

    webio.get_session = current_session
    webio.requests = RequestsTransport(webio.requests)  # only this vendor module, not requests globally
