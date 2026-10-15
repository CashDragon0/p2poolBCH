"""HTTP-status handling in p2pool.util.jsonrpc.get_page / _http_do.

py2's _http_do used twisted.web.client.getPage (present in Twisted 18.9,
removed in 21). getPage raised web.error.Error(status, message, body) on
*every* non-2xx, attaching the full response body. The port replaces it with
a hand-rolled get_page (Agent + readBody), because readBody does not raise on
non-2xx. get_page re-raises error.Error(code, None, body) on non-2xx so
_http_do's `except error.Error` branch (which decodes the JSON-RPC error out
of e.response) still fires.

These tests pin the four (status, body-shape) combinations against a live
loopback server, because _http_do's behaviour is not a simple "non-2xx is an
error":

  500 + JSON-RPC error body   -> jsonrpc.NarrowError carrying the code (-22).
                                  This is bitcoind's submitblock contract
                                  (helper.py:180) and the case commit 46ef580
                                  fixed; before it, the body took the
                                  json.loads success path and surfaced as
                                  JSONDecodeError instead of -22.
  200 + JSON-RPC error body   -> same NarrowError. The JSON-RPC error is
                                  authoritative regardless of HTTP status.
  500 + JSON-RPC success body -> returns the result. _http_do only re-raises
                                  the HTTP error when the body does not parse
                                  as JSON; a well-formed body wins. (A
                                  bare 500 with a non-JSON body does surface
                                  the HTTP error -- see the last test.)
  200 + JSON-RPC success body -> returns the result (the happy path).

The 500-with-success-body case is included because it is the one that proves
the body, not the status, drives the outcome; asserting "500 must raise" there
would encode a rule the code deliberately does not follow.
"""
import json
import unittest

from twisted.internet import defer, reactor
from twisted.web import error as web_error
from twisted.web import resource, server

from p2pool.util import jsonrpc


class _FixedResource(resource.Resource):
    isLeaf = True

    def __init__(self, status, body):
        resource.Resource.__init__(self)
        self._status = status
        self._body = body

    def render_POST(self, request):
        request.setResponseCode(self._status)
        request.setHeader(b"Content-Type", b"application/json")
        return self._body


def _call(status, body, timeout=None):
    """POST to a one-shot loopback server that answers (status, body) and
    fire with _http_do's outcome as (kind, value) where kind is 'result' or
    'exc'.

    Written without an @inlineCallbacks/finally-yield wrapper on purpose:
    yielding port.stopListening() inside a finally block suspends the
    generator after the test's own Deferred has already fired, so trial
    closes it and logs "generator ignored GeneratorExit". The port teardown
    is therefore fire-and-forget here and left to the reactor."""
    site = server.Site(_FixedResource(status, body))
    port = reactor.listenTCP(0, site, interface="127.0.0.1")
    url = "http://127.0.0.1:%d/" % (port.getHost().port,)

    d = jsonrpc._http_do(url, {}, timeout, "getblockcount", [])

    def _ok(value):
        port.stopListening()
        return ("result", value)

    def _err(failure):
        port.stopListening()
        return ("exc", failure.value)

    return d.addCallbacks(_ok, _err)


def _jsonrpc_body(result, error, id=0):
    return json.dumps({"result": result, "error": error, "id": id}).encode("utf-8")


class TestHttpStatusHandling(unittest.TestCase):
    def tearDown(self):
        # _call tears its port down fire-and-forget; cancel anything the
        # reactor still has pending so it cannot leak into the next test.
        for call in reactor.getDelayedCalls():
            if call.active():
                call.cancel()

    @defer.inlineCallbacks
    def test_500_with_jsonrpc_error_body_surfaces_the_code(self):
        # bitcoind's submitblock contract: HTTP 500 + a JSON-RPC error body.
        # The code (-22) must reach the caller, not a JSONDecodeError.
        body = _jsonrpc_body(None, {"code": -22, "message": "Block decode failed"})
        kind, value = yield _call(500, body)
        self.assertEqual(kind, "exc")
        self.assertIsInstance(value, jsonrpc.Error)
        self.assertEqual(value.code, -22)
        self.assertEqual(value.message, "Block decode failed")

    @defer.inlineCallbacks
    def test_200_with_jsonrpc_error_body_still_surfaces_the_code(self):
        # The JSON-RPC error is authoritative regardless of HTTP status.
        body = _jsonrpc_body(None, {"code": -25, "message": "Bad block"})
        kind, value = yield _call(200, body)
        self.assertEqual(kind, "exc")
        self.assertIsInstance(value, jsonrpc.Error)
        self.assertEqual(value.code, -25)

    @defer.inlineCallbacks
    def test_500_with_jsonrpc_success_body_returns_the_result(self):
        # A well-formed JSON body wins over the HTTP status: _http_do only
        # re-raises the HTTP error when the body does not parse.
        body = _jsonrpc_body(12345, None)
        kind, value = yield _call(500, body)
        self.assertEqual((kind, value), ("result", 12345))

    @defer.inlineCallbacks
    def test_200_with_jsonrpc_success_body_returns_the_result(self):
        body = _jsonrpc_body(67890, None)
        kind, value = yield _call(200, body)
        self.assertEqual((kind, value), ("result", 67890))

    @defer.inlineCallbacks
    def test_500_with_non_json_body_surfaces_the_http_error(self):
        # get_page's _decode raises error.Error(500, None, body); _http_do's
        # `except error.Error` tries json.loads(e.response), and on failure
        # re-raises the original HTTP error (not a parse error, not the
        # JSONDecodeError). Pin the three fields so the re-raise is the same
        # exception object: status b"500", message b"Internal Server Error",
        # body intact in .response.
        #
        # get_page's _decode raises error.Error(code, None, body), but
        # Twisted's Error.__init__ does `message = message or
        # _codeToMessage(code)`, so the None becomes the phrase for 500; and
        # it normalises the int code to bytes. Pin both so the re-raise is the
        # same object, not a JSONDecodeError / parse error.
        body = b"<html>not json</html>"
        kind, value = yield _call(500, body)
        self.assertEqual(kind, "exc")
        self.assertIsInstance(value, web_error.Error)
        self.assertEqual(value.status, b"500")
        self.assertEqual(value.message, b"Internal Server Error")
        self.assertEqual(value.response, body)


class TestTimeoutUnwrap(unittest.TestCase):
    def tearDown(self):
        for call in reactor.getDelayedCalls():
            if call.active():
                call.cancel()

    @defer.inlineCallbacks
    def test_no_timeout_returns_result(self):
        # timeout=None skips the DeferredList wrapper entirely (the path the
        # live regtest run exercises).
        body = _jsonrpc_body(42, None)
        kind, value = yield _call(200, body, timeout=None)
        self.assertEqual((kind, value), ("result", 42))

    @defer.inlineCallbacks
    def test_timeout_allows_the_result_through(self):
        # With a generous timeout the request completes first; _first must
        # unwrap the fireOnOneCallback (result, index) tuple and return the
        # result, not the tuple.
        body = _jsonrpc_body(99, None)
        kind, value = yield _call(200, body, timeout=10)
        self.assertEqual((kind, value), ("result", 99))
