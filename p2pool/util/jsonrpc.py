from __future__ import division

import json
import os
import weakref
from collections import OrderedDict

from twisted.internet import defer
from twisted.internet.task import deferLater
from twisted.protocols import basic
from twisted.python import failure, log
from twisted.web import client, error

from p2pool.util import deferral, deferred_resource, memoize

# Optional JSON-RPC 1.0 wire format for outgoing messages.  When set,
# the framework omits the "jsonrpc": "2.0" field from generated
# responses and request frames.  Strict CGMiner branches in some Bitmain
# stock firmware (Antminer S21+ FR-1.15 observed) parse only the legacy
# 1.0 layout and silently fail on the 2.0 marker — they handshake but
# never submit shares.  Kr1z1s (p2p-spb.xyz, version 77.0.0-12-g5493200)
# wire-traces show no "jsonrpc" field, and kr1z1s is observed handling
# FR-1.15 fine.  Default off = legacy (current) 2.0 behaviour.  Affects
# all jsonrpc.py callers (LineBasedPeer for stratum + HTTPServer for the
# legacy /getwork endpoint); BCHN's bitcoind RPC client does not go
# through this module so its requests are unaffected.  Scope is
# intentionally global since strict miners check both directions.
_LEGACY_JSONRPC = os.environ.get('STRATUM_LEGACY_JSONRPC', '').strip().lower() in ('1', 'true', 'yes', 'on')
if _LEGACY_JSONRPC:
    print('STRATUM: JSON-RPC 1.0 wire format ENABLED (no "jsonrpc": "2.0" field) via STRATUM_LEGACY_JSONRPC')

def _frame_response(id_, result, error):
    """Build the JSON for an outgoing JSON-RPC response, honouring the
    STRATUM_LEGACY_JSONRPC toggle.  Key order: id → result → error
    (→ jsonrpc), matching kr1z1s / ckpool layout exactly so a strict
    hand-rolled CGMiner parser that expects a fixed field sequence
    sees the same bytes.  PyPy/CPython 2.7 dict iteration is hash-based
    and undeterministic — without OrderedDict we'd emit fields in
    whatever order the dict happens to enumerate."""
    obj = OrderedDict()
    obj['id'] = id_
    obj['result'] = result
    obj['error'] = error
    if not _LEGACY_JSONRPC:
        obj['jsonrpc'] = '2.0'
    return json.dumps(obj)

_STRATUM_NOTIFICATION_METHODS = frozenset([
    'mining.notify',
    'mining.set_difficulty',
    'mining.set_version_mask',
    'mining.set_extranonce',
    'client.reconnect',
    'client.show_message',
])

def _frame_request(id_, method, params):
    """Build the JSON for an outgoing JSON-RPC request, honouring the
    STRATUM_LEGACY_JSONRPC toggle.

    Stratum has both true RPC requests (e.g. client.get_version, where the
    miner sends a response) and unsolicited notifications (mining.notify,
    mining.set_difficulty, mining.set_version_mask, etc.).  The JSON-RPC
    spec says notifications MUST have id=null; kr1z1s / ckpool / slush /
    NiceHash all do this.  The p2pool framework's GenericDeferrer assigns
    a sequential id to every outgoing call uniformly, which produces
    non-null ids for notifications too — strict CGMiner-derived parsers
    in stock Bitmain firmware (Antminer S21+ FR-1.15 suspected) check
    this and silently reject malformed notifications, which is a strong
    candidate for the 0-submit cycle behaviour.  This shim forces
    id=null for any method on the known-notification list."""
    if method in _STRATUM_NOTIFICATION_METHODS:
        id_ = None
    # Key order: id → method → params (→ jsonrpc), matching kr1z1s /
    # ckpool / NiceHash exactly.  Strict hand-rolled CGMiner parsers
    # in stock Bitmain firmware may have a fixed field sequence and
    # bail on differently-ordered JSON; PyPy 2.7 dict iteration is
    # hash-based, so without OrderedDict we'd emit unpredictable order.
    obj = OrderedDict()
    obj['id'] = id_
    obj['method'] = method
    obj['params'] = params
    if not _LEGACY_JSONRPC:
        obj['jsonrpc'] = '2.0'
    return json.dumps(obj)

class Error(Exception):
    def __init__(self, code, message, data=None):
        if type(self) is Error:
            raise TypeError("can't directly instantiate Error class; use Error_for_code")
        if not isinstance(code, int):
            raise TypeError('code must be an int')
        #if not isinstance(message, unicode):
        #    raise TypeError('message must be a unicode')
        self.code, self.message, self.data = code, message, data
    def __str__(self):
        return '%i %s' % (self.code, self.message) + (' %r' % (self.data, ) if self.data is not None else '')
    def _to_obj(self):
        return {
            'code': self.code,
            'message': self.message,
            'data': self.data,
        }

@memoize.memoize_with_backing(weakref.WeakValueDictionary())
def Error_for_code(code):
    class NarrowError(Error):
        def __init__(self, *args, **kwargs):
            Error.__init__(self, code, *args, **kwargs)
    return NarrowError


class Proxy(object):
    def __init__(self, func, services=[]):
        self._func = func
        self._services = services
    
    def __getattr__(self, attr):
        if attr.startswith('rpc_'):
            return lambda *params: self._func('.'.join(self._services + [attr[len('rpc_'):]]), params)
        elif attr.startswith('svc_'):
            return Proxy(self._func, self._services + [attr[len('svc_'):]])
        else:
            raise AttributeError('%r object has no attribute %r' % (self.__class__.__name__, attr))

@defer.inlineCallbacks
def _handle(data, provider, preargs=(), response_handler=None):
        id_ = None
        
        try:
            try:
                try:
                    req = json.loads(data)
                except Exception:
                    raise Error_for_code(-32700)(u'Parse error')
                
                if 'result' in req or 'error' in req:
                    response_handler(req['id'], req['result'] if 'error' not in req or req['error'] is None else
                        failure.Failure(Error_for_code(req['error']['code'])(req['error']['message'], req['error'].get('data', None))))
                    defer.returnValue(None)
                
                id_ = req.get('id', None)
                method = req.get('method', None)
                if not isinstance(method, str):
                    raise Error_for_code(-32600)(u'Invalid Request')
                params = req.get('params', [])
                if not isinstance(params, list):
                    raise Error_for_code(-32600)(u'Invalid Request')
                
                for service_name in method.split('.')[:-1]:
                    provider = getattr(provider, 'svc_' + service_name, None)
                    if provider is None:
                        raise Error_for_code(-32601)(u'Service not found')
                
                method_meth = getattr(provider, 'rpc_' + method.split('.')[-1], None)
                if method_meth is None:
                    raise Error_for_code(-32601)(u'Method not found')
                
                result = yield method_meth(*list(preargs) + list(params))
                error = None
            except Error:
                raise
            except Exception:
                log.err(None, 'Squelched JSON error:')
                raise Error_for_code(-32099)(u'Unknown error')
        except Error as e:
            result = None
            error = e._to_obj()
        
        defer.returnValue(_frame_response(id_, result, error))

# HTTP

def get_page(url, method='GET', headers=None, postdata=None, timeout=None):
    """Replacement for the removed twisted.web.client.getPage (Twisted 21+).

    getPage was removed in Twisted 21; Agent + readBody is the documented
    replacement. Differences that matter here:
      - the URL and method must be bytes (Agent takes bytes, so encode below)
      - headers are a str -> [str] mapping; Headers normalises to bytes itself
      - the body must be an IBodyProducer, not raw bytes. Agent stores the
        argument as bodyProducer and later reads .length off it, so passing
        bytes raises "'bytes' object has no attribute 'length'".
      - readBody takes the *Response*, not the request Deferred, so it has to
        be chained off the request rather than handed the Deferred.
      - getPage returned str; readBody returns bytes. We decode to str so
        every existing caller (json.loads, logging) keeps working.
    Returns a Deferred firing with the response body as str.
    """
    from twisted.web.client import Agent, FileBodyProducer, readBody
    from twisted.web.http_headers import Headers
    from twisted.internet import reactor
    from io import BytesIO
    hdrs = {}
    for k, v in (headers or {}).items():
        if isinstance(k, bytes):
            k = k.decode('utf-8')
        if isinstance(v, bytes):
            v = v.decode('utf-8')
        hdrs[k] = [v]
    body = None
    if postdata is not None:
        raw = postdata.encode('utf-8') if isinstance(postdata, str) else postdata
        body = FileBodyProducer(BytesIO(raw))
    agent = Agent(reactor)
    req = agent.request(method.encode('utf-8'), url.encode('utf-8'),
                        Headers(hdrs), body)
    # getPage raised web Error on non-2xx; readBody does not. bitcoind
    # answers submitblock with HTTP 500 + a valid JSON-RPC error body, so
    # without this the body would take the json.loads success path and the
    # real -22 would surface as JSONDecodeError.
    #
    # The status rides the callback channel as (code, body): consumeErrors
    # in the DeferredList below swallows *errbacks*, so raising here would
    # be turned into a bogus TimeoutError. _http_do rebuilds error.Error
    # from it (twisted stores the status in .status and the body in
    # .response, not .code/.message).
    def _check_status(resp):
        def _tag(body):
            return resp.code, body
        return readBody(resp).addCallback(_tag)
    d = req.addCallback(_check_status)
    if timeout is not None:
        d = defer.DeferredList([d, deferLater(reactor, timeout, lambda: None)],
                               fireOnOneCallback=True, consumeErrors=True)
        def _first(results):
            # fireOnOneCallback fires with (result, index), NOT a list of
            # (succeeded, result) tuples; only the all-done path is a list.
            # consumeErrors turns a failed request into (None, 0) and the
            # timer into (None, 1), so distinguish by index, then re-raise
            # the real failure rather than reporting a bogus timeout.
            if not results:
                raise defer.TimeoutError()
            result, index = results
            if index == 1:
                raise defer.TimeoutError()  # timer won: real request pending
            return result
        d.addCallback(_first)
    # (code, body) -> str body; non-2xx becomes error.Error so _http_do's
    # except clause can decode the JSON-RPC error out of it.
    def _decode(tagged):
        if isinstance(tagged, tuple):
            code, body = tagged
            if code < 200 or code >= 300:
                raise error.Error(code, None, body)
            tagged = body
        return tagged.decode('utf-8', 'replace') if isinstance(tagged, bytes) else tagged
    return d.addCallback(_decode)


@defer.inlineCallbacks
def _http_do(url, headers, timeout, method, params):
    id_ = 0
    
    try:
        data = yield get_page(
            url=url,
            method='POST',
            headers=dict(headers, **{'Content-Type': 'application/json'}),
            postdata=json.dumps({
                'jsonrpc': '2.0',
                'method': method,
                'params': params,
                'id': id_,
            }),
            timeout=timeout,
        )
    except error.Error as e:
        try:
            resp = json.loads(e.response)
        except:
            raise e
    else:
        resp = json.loads(data)
    
    if resp['id'] != id_:
        raise ValueError('invalid id')
    if 'error' in resp and resp['error'] is not None:
        raise Error_for_code(resp['error']['code'])(resp['error']['message'], resp['error'].get('data', None))
    defer.returnValue(resp['result'])
HTTPProxy = lambda url, headers={}, timeout=5: Proxy(lambda method, params: _http_do(url, headers, timeout, method, params))

class HTTPServer(deferred_resource.DeferredResource):
    def __init__(self, provider):
        deferred_resource.DeferredResource.__init__(self)
        self._provider = provider
    
    @defer.inlineCallbacks
    def render_POST(self, request):
        data = yield _handle(request.content.read(), self._provider, preargs=[request])
        assert data is not None
        request.setHeader('Content-Type', 'application/json')
        raw = data.encode('utf-8') if isinstance(data, str) else data
        # twisted 24 Request.write is bytes-only; setHeader value too
        request.setHeader(b'Content-Length', str(len(raw)))
        request.write(raw)

class LineBasedPeer(basic.LineOnlyReceiver):
    delimiter = b'\n'
    
    def _send_line(self, line):
        # _frame_response/_frame_request return str; LineOnlyReceiver.sendLine
        # goes straight to transport.write(), which is bytes-only under py3.
        self.sendLine(line.encode('utf-8') if isinstance(line, str) else line)
    
    def __init__(self):
        #basic.LineOnlyReceiver.__init__(self)
        self._matcher = deferral.GenericDeferrer(max_id=2**30, func=lambda id, method, params: self._send_line(_frame_request(id, method, params)))
        self.other = Proxy(self._matcher)
    
    def lineReceived(self, line):
        _handle(line, self, response_handler=self._matcher.got_response).addCallback(lambda line2: self._send_line(line2) if line2 is not None else None)
