"""Stratum inbound-path regression test.

test_stratum.py pins the outbound _send_work flow; this drives the full
JSON-RPC dispatch through StratumProtocol.lineReceived — the path a miner
actually takes: mining.subscribe / mining.configure / mining.authorize /
mining.submit, plus the error paths (unknown jobid, invalid version mask,
malformed JSON).

Expected values are py2's: stratum_inbound_parity.py ran the identical
fixture under PyPy 2.7 against pristine source (worktree cf438b6) and the
outputs diffed byte-for-byte.
"""
import json
import os
import unittest

# Suppress latency ping / idle nudge so no timer drain is needed.
os.environ.setdefault('STRATUM_DISABLE_LATENCY_PING', '1')
os.environ.setdefault('STRATUM_DISABLE_IDLE_NUDGE', '1')

from twisted.internet import defer, reactor
from twisted.python import log

from p2pool import networks as pnet
from p2pool.bitcoin import data as bitcoin_data
from p2pool.bitcoin import stratum
from p2pool.util import variable

net = pnet.nets['bitcoincash_regtest']

WORK = dict(
    version=0x20000000,
    previous_block=0x0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef,
    bits=bitcoin_data.FloatingInteger(0x1d00ffff),
    timestamp=0x65000000,
    coinb1=b'\x03\x01\x02\x03',
    coinb2=b'\x00\x00\x00\x00',
    merkle_link=dict(branch=[0x1111111111111111111111111111111111111111111111111111111111111111], index=0),
    min_share_target=2 ** 225,
    share_target=2 ** 230,
)


class Handler(object):
    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, header, user, coinbase_nonce, pseudoshare_target):
        self.calls.append((dict(header), user, coinbase_nonce, pseudoshare_target))
        return self.result


def make_wb(work_value, handler):
    class WB(object):
        share_rate = 30.0
        net = net.PARENT
        COINBASE_NONCE_LENGTH = 8

        def __init__(self):
            self.new_work_event = variable.Event()
            self.worker_connected = variable.Event()
            self.worker_disconnected = variable.Event()
            self.connected_workers = {}
            self.worker_latency_history = {}

        def preprocess_request(self, username):
            return ()

        def get_work(self, *a):
            return work_value, handler

        def get_user_details(self, username):
            return (username, 'fakeaddr', None, None)

    return WB()


class Transport(object):
    def __init__(self):
        self.written = []

    def write(self, data):
        self.written.append(data)

    def writeSequence(self, seq):
        self.written.append(b''.join(seq))

    def getPeer(self):
        class P(object):
            host = '10.0.0.9'
            port = 3333
        return P()

    def getHost(self):
        class H(object):
            host = '127.0.0.1'
            port = 9348
        return H()

    def loseConnection(self):
        pass

    def abortConnection(self):
        pass

    def setTcpNoDelay(self, v):
        pass

    def setTcpKeepAlive(self, v):
        pass


class Factory(object):
    def __init__(self, wb):
        self.wb = wb


def flush_reactor():
    d = defer.Deferred()
    reactor.callLater(0, d.callback, None)
    if not d.called:
        reactor.iterate(0.01)


def make_proto(wb):
    proto = stratum.StratumProtocol()
    proto.factory = Factory(wb)
    proto.transport = Transport()
    proto.connectionMade()
    return proto


def send_line(proto, method, params, id_=1):
    frame = json.dumps({'id': id_, 'method': method, 'params': params,
                        'jsonrpc': '2.0'})
    proto.lineReceived(frame.encode('utf-8'))
    flush_reactor()


def frames(written):
    return [json.loads(raw.decode('utf-8').rstrip('\n')) for raw in written]


class StratumInboundTest(unittest.TestCase):
    def setUp(self):
        self.handler = Handler((True, True))
        self.wb = make_wb(WORK, self.handler)
        self.proto = make_proto(self.wb)

    def tearDown(self):
        try:
            self.proto.svc_mining.close()
        except Exception:
            pass
        for call in reactor.getDelayedCalls():
            try:
                call.cancel()
            except Exception:
                pass

    def test_subscribe_returns_flat_form_and_schedules_notify(self):
        del self.proto.transport.written[:]
        send_line(self.proto, 'mining.subscribe', ['ccminer/2.0', None], id_=1)
        msgs = frames(self.proto.transport.written)
        # First frame is the JSON-RPC reply; the rest are server notifications.
        reply = msgs[0]
        self.assertEqual(reply['id'], 1)
        self.assertEqual(reply['result'], [['mining.notify', 'p2p1'], '', 8])
        self.assertEqual(reply['error'], None)
        methods = [m.get('method') for m in msgs[1:]]
        self.assertIn('mining.set_difficulty', methods)
        self.assertIn('mining.notify', methods)

    def test_authorize_returns_true_and_populates_handler_map(self):
        del self.proto.transport.written[:]
        send_line(self.proto, 'mining.authorize', ['worker1', 'x'], id_=3)
        msgs = frames(self.proto.transport.written)
        reply = msgs[0]
        self.assertEqual(reply['id'], 3)
        self.assertIs(reply['result'], True)
        self.assertEqual(self.proto.svc_mining.username, 'worker1')
        self.assertEqual(self.proto.svc_mining.authorized, 'worker1')
        self.assertGreaterEqual(len(self.proto.svc_mining.handler_map), 1)

    def test_configure_returns_version_rolling_mask(self):
        del self.proto.transport.written[:]
        send_line(self.proto, 'mining.configure',
                  [['version-rolling'],
                   {'version-rolling.mask': '1fffe000',
                    'version-rolling.min-bit-count': '2'}],
                  id_=2)
        msgs = frames(self.proto.transport.written)
        reply = msgs[0]
        self.assertEqual(reply['id'], 2)
        self.assertEqual(reply['result'],
                         {'version-rolling': True,
                          'version-rolling.mask': '1fffe000'})

    def test_submit_unknown_jobid_returns_false(self):
        del self.proto.transport.written[:]
        send_line(self.proto, 'mining.submit',
                  ['worker1', 'nosuchjob', '00000000', '65000000', '12345678'],
                  id_=10)
        msgs = frames(self.proto.transport.written)
        self.assertIs(msgs[0]['result'], False)
        self.assertEqual(len(self.handler.calls), 0)

    def test_submit_accepted_calls_handler_with_reconstructed_header(self):
        send_line(self.proto, 'mining.subscribe', ['cgminer/4.10', None], id_=1)
        send_line(self.proto, 'mining.authorize', ['worker1', 'x'], id_=3)
        self.handler.result = (True, True)
        del self.proto.transport.written[:]
        send_line(self.proto, 'mining.submit',
                  ['worker1', '1', '0000000000000000', '65000000', '12345678'],
                  id_=11)
        msgs = frames(self.proto.transport.written)
        self.assertIs(msgs[0]['result'], True)
        self.assertEqual(len(self.handler.calls), 1)
        h, user, nonce, target = self.handler.calls[-1]
        self.assertEqual(h['version'], 0x20000000)
        self.assertEqual(h['nonce'], 0x12345678)
        self.assertEqual(h['timestamp'], 0x65000000)
        self.assertEqual(h['merkle_root'],
                         0x95ccde1b0c6247daf2549057ee2def86a8ee3887b7c88781ad1dab16fb32dd0a)
        self.assertEqual(user, 'worker1')
        self.assertEqual(nonce, b'\0' * 8)
        self.assertEqual(int(target), 2 ** 230)

    def test_submit_rejected_returns_false(self):
        send_line(self.proto, 'mining.subscribe', ['cgminer/4.10', None], id_=1)
        send_line(self.proto, 'mining.authorize', ['worker1', 'x'], id_=3)
        self.handler.result = (False, False)
        del self.proto.transport.written[:]
        send_line(self.proto, 'mining.submit',
                  ['worker1', '1', '0000000000000000', '65000000', '12345678'],
                  id_=12)
        msgs = frames(self.proto.transport.written)
        self.assertIs(msgs[0]['result'], False)

    def test_submit_doa_but_accepted_returns_accepted(self):
        # Pre-fix bug returned on_time (False) for DOA shares; post-fix
        # returns accepted so the miner doesn't count a perceived reject.
        send_line(self.proto, 'mining.subscribe', ['cgminer/4.10', None], id_=1)
        send_line(self.proto, 'mining.authorize', ['worker1', 'x'], id_=3)
        self.handler.result = (False, True)
        del self.proto.transport.written[:]
        send_line(self.proto, 'mining.submit',
                  ['worker1', '1', '0000000000000000', '65000000', '12345678'],
                  id_=13)
        msgs = frames(self.proto.transport.written)
        self.assertIs(msgs[0]['result'], True)

    def test_submit_invalid_version_mask_returns_error(self):
        # The ValueError is squelched by jsonrpc._handle into an error
        # response; twisted's log.err records it, which trial would flag.
        # Suppress log.err for the duration of the trigger.
        orig_err = log.err
        log.err = lambda *a, **kw: None
        try:
            send_line(self.proto, 'mining.subscribe', ['cgminer/4.10', None], id_=1)
            send_line(self.proto, 'mining.authorize', ['worker1', 'x'], id_=3)
            del self.proto.transport.written[:]
            send_line(self.proto, 'mining.submit',
                      ['worker1', '1', '0000000000000000', '65000000', '12345678',
                       'ffffffff'],
                      id_=14)
        finally:
            log.err = orig_err
        msgs = frames(self.proto.transport.written)
        self.assertIsNotNone(msgs[0]['error'])
        self.assertEqual(msgs[0]['error']['message'], 'Unknown error')

    def test_full_handshake_produces_two_notifies_one_setdiff(self):
        del self.proto.transport.written[:]
        send_line(self.proto, 'mining.subscribe', ['cgminer/4.10', None], id_=1)
        send_line(self.proto, 'mining.authorize', ['w.a', 'x'], id_=2)
        msgs = frames(self.proto.transport.written)
        notifies = [m for m in msgs if m.get('method') == 'mining.notify']
        setdiffs = [m for m in msgs if m.get('method') == 'mining.set_difficulty']
        # Legacy behaviour (NOTIFY_AFTER_AUTH off): subscribe and authorize
        # each schedule _send_work -> two notifies, one set_difficulty (the
        # second notify has the same diff so set_diff is suppressed).
        self.assertEqual(len(notifies), 2)
        self.assertEqual(len(setdiffs), 1)

    def test_close_cleans_up(self):
        send_line(self.proto, 'mining.authorize', ['worker1', 'x'], id_=3)
        self.proto.svc_mining.close()
        self.assertFalse(self.proto.svc_mining._ping_active)

    def test_malformed_json_returns_parse_error(self):
        del self.proto.transport.written[:]
        self.proto.lineReceived(b'not-json\n')
        msgs = frames(self.proto.transport.written)
        self.assertEqual(msgs[0]['error']['message'], 'Parse error')
        self.assertEqual(msgs[0]['error']['code'], -32700)


if __name__ == '__main__':
    unittest.main()
