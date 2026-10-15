"""p2p handler-path regression test.

p2p_handler_parity.py drove every handle_* body under both interpreters and
diffed the outputs; this pins the paths that actually caught a port bug:
random.choice(dict.values()) raised TypeError on py3 because dict_values is
not subscriptable.  The fix wraps the view in list() (p2p.py handle_addrme /
handle_addrs / _think, node.py share parent fetch).  These tests exercise the
addrme and addrs forwarding paths end-to-end through dataReceived and assert
the peer actually receives the forwarded message -- which is exactly what
broke before the fix.

Expected values are py2's: the identical scenarios in p2p_handler_parity.py
ran under PyPy 2.7 against pristine source (worktree cf438b6) and diffed
byte-for-byte after normalizing bytes/str repr and long-L suffixes.
"""
import hashlib
import random
import struct
import unittest

from twisted.internet import defer, reactor

from p2pool import p2p
from p2pool.networks import bitcoincash_regtest as netmod
from p2pool.util import variable

net = netmod
PREFIX = net.PREFIX

H = 0x0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
H2 = 0x1111111111111111111111111111111111111111111111111111111111111111

VER = dict(version=3502, services=1, nonce=0x1122334455667788,
           sub_version=b'/P2Pool:test/', mode=1, best_share_hash=H2,
           addr_to=dict(services=0, address='10.9.8.7', port=9333),
           addr_from=dict(services=0, address='10.9.8.7', port=9333))


def build_frame(command, payload):
    return (PREFIX
            + struct.pack('<12sI', command.encode('utf-8'), len(payload))
            + hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
            + payload)


def frame_msg(command, payload2):
    t = getattr(p2p.Protocol, 'message_' + command)
    return build_frame(command, t.pack(payload2))


class Transport(object):
    def __init__(self, peer_host='10.9.8.7', peer_port=9333):
        self.buf = []
        self.aborted = False
        self.lost = False
        self._peer = type('P', (), {'host': peer_host, 'port': peer_port})()
        self._host = type('H', (), {'host': '127.0.0.1', 'port': 9348})()

    def write(self, data):
        self.buf.append(data)

    def writeSequence(self, seq):
        self.buf.append(b''.join(seq))

    def getPeer(self):
        return self._peer

    def getHost(self):
        return self._host

    def loseConnection(self):
        self.lost = True

    def abortConnection(self):
        self.aborted = True

    def setTcpNoDelay(self, v):
        pass

    def setTcpKeepAlive(self, v):
        pass


class StubPeerConn(object):
    def __init__(self, node, nonce=0xABCD):
        self.node = node
        self.nonce = nonce
        self.incoming = True
        self.addr = ('10.0.0.1', 9333)
        self.other_version = 3502
        self.other_sub_version = b'/Stub/'
        self.other_services = 1
        self.sent = []

    def send_addrme(self, **kw):
        self.sent.append(('addrme', dict(kw)))

    def send_addrs(self, **kw):
        addrs = kw.get('addrs', [])
        self.sent.append(('addrs', [
            dict(timestamp=a['timestamp'],
                 address=dict(services=a['address']['services'],
                              address=a['address']['address'],
                              port=a['address']['port']))
            for a in addrs]))

    def send_getaddrs(self, **kw):
        self.sent.append(('getaddrs', dict(kw)))


class StubNode(object):
    def __init__(self):
        self.net = net
        self.nonce = 0xCAFEF00DCAFEF00D
        self.peers = {}
        self.bans = {}
        self.banscores = {}
        self.traffic_happened = variable.Event()
        self.best_share_hash = H
        self.best_share_hash_func = lambda: self.best_share_hash
        self.known_txs_var = variable.VariableDict({})
        self.mining_txs_var = variable.VariableDict({})
        self.mining2_txs_var = variable.VariableDict({})
        self.advertise_ip = False
        self.external_ip = None
        self.addr_store = {}
        self.preferred_storage = 1000
        self.calls = []
        self.node = type('N', (), {'cur_share_ver': 35})()
        self.serverfactory = type('SF', (), {'listen_port': None})()

    def handle_share_hashes(self, hashes, peer):
        self.calls.append(('handle_share_hashes', peer.nonce))

    def got_addr(self, addr, services, timestamp):
        host, port = addr
        if (host, port) in self.addr_store:
            old_services, old_first_seen, old_last_seen = \
                self.addr_store[host, port]
            self.addr_store[host, port] = \
                services, old_first_seen, max(old_last_seen, timestamp)
        else:
            if len(self.addr_store) < 10000:
                self.addr_store[host, port] = services, timestamp, timestamp
        self.calls.append(('got_addr', host, port, services))

    def get_good_peers(self, max_count):
        return sorted(self.addr_store.keys())[:max_count]

    def got_conn(self, conn):
        if conn.nonce in self.peers:
            raise ValueError('already have peer')
        conn.connect_time = 0
        self.peers[conn.nonce] = conn

    def lost_conn(self, conn, reason):
        if conn.nonce not in self.peers:
            raise ValueError("don't have peer")
        del self.peers[conn.nonce]


class Factory(object):
    def __init__(self, node):
        self.node = node
        self.listen_port = None
        self.events = []

    def proto_made_connection(self, proto):
        self.events.append('made')

    def proto_connected(self, proto):
        self.events.append('connected')

    def proto_disconnected(self, proto, reason):
        self.events.append('disconnected')

    def proto_lost_connection(self, proto, reason):
        self.events.append('lost')


def make_proto(node, peer_host='10.9.8.7', peer_port=9333):
    factory = Factory(node)
    proto = p2p.Protocol(node, incoming=False)
    proto.factory = factory
    proto.makeConnection(Transport(peer_host, peer_port))
    proto.dataReceived(frame_msg('version', VER))
    d = defer.Deferred()
    reactor.callLater(0, d.callback, None)
    if not d.called:
        reactor.iterate(0.01)
    return proto


def cancel_delayed():
    for call in list(reactor.getDelayedCalls()):
        try:
            call.cancel()
        except Exception:
            pass


class HandlerPathTests(unittest.TestCase):
    def tearDown(self):
        cancel_delayed()

    def test_addrme_forwards_to_peer(self):
        """handle_addrme from non-localhost with a peer present must forward
        addrs.  This crashed on py3 before the list() fix."""
        random.seed(42)  # keep random.random() < .8 true
        node = StubNode()
        proto = make_proto(node, peer_host='10.9.8.7')
        stub = StubPeerConn(node, nonce=0xABCD)
        node.peers[0xABCD] = stub
        del proto.transport.buf[:]

        proto.dataReceived(frame_msg('addrme', dict(port=9333)))

        self.assertFalse(proto.transport.aborted)
        self.assertEqual(len(stub.sent), 1)
        kind, payload = stub.sent[0]
        self.assertEqual(kind, 'addrs')
        self.assertEqual(payload[0]['address']['address'], '10.9.8.7')
        self.assertEqual(payload[0]['address']['port'], 9333)
        self.assertEqual(payload[0]['address']['services'], 1)
        self.assertIn(('got_addr', '10.9.8.7', 9333, 1), node.calls)

    def test_addrme_localhost_forwards_addrme(self):
        """handle_addrme from 127.0.0.1 with a peer present forwards the
        addrme itself (not addrs) and does not call got_addr."""
        random.seed(42)
        node = StubNode()
        proto = make_proto(node, peer_host='127.0.0.1', peer_port=18444)
        stub = StubPeerConn(node, nonce=0xABCD)
        node.peers[0xABCD] = stub
        del proto.transport.buf[:]

        proto.dataReceived(frame_msg('addrme', dict(port=9333)))

        self.assertFalse(proto.transport.aborted)
        self.assertEqual(stub.sent, [('addrme', {'port': 9333})])
        self.assertEqual(node.addr_store, {})

    def test_addrs_forwards_to_peer(self):
        """handle_addrs stores each address and forwards to a peer."""
        random.seed(42)
        node = StubNode()
        proto = make_proto(node, peer_host='10.9.8.7')
        stub = StubPeerConn(node, nonce=0xABCD)
        node.peers[0xABCD] = stub
        del proto.transport.buf[:]

        proto.dataReceived(frame_msg('addrs', dict(addrs=[
            dict(timestamp=1700000000,
                 address=dict(services=1, address='198.51.100.1', port=9333)),
        ])))

        self.assertFalse(proto.transport.aborted)
        self.assertIn(('198.51.100.1', 9333), node.addr_store)
        self.assertEqual(len(stub.sent), 1)
        self.assertEqual(stub.sent[0][0], 'addrs')

    def test_version_decodes_sub_version_to_str(self):
        """handle_version must store other_sub_version as str, not bytes.

        py2's VarStrType.unpack returned str; py3's returns bytes. Left
        undecoded, the value leaks into the /peer_list and /peer_versions
        JSON handlers and json.dumps raises
        "Object of type bytes is not JSON serializable" (hit live during the
        two-node relay test). Pin the decode at the source.

        make_proto already drives the VER frame (sub_version=b'/P2Pool:test/'),
        so this inspects the result rather than sending a second version
        message, which handle_version would reject as a duplicate.
        """
        proto = make_proto(StubNode(), peer_host='10.9.8.7')

        self.assertEqual(proto.other_sub_version, '/P2Pool:test/')
        self.assertIsInstance(proto.other_sub_version, str)

    def test_version_decodes_then_truncates_sub_version(self):
        """The 512 cap must be applied after the decode, so a multi-byte
        tail is never split mid-character. Uses a protocol stub because
        make_proto pre-sends VER, and a second version message is rejected
        as a duplicate before other_sub_version is reassigned."""
        proto = p2p.Protocol(StubNode(), incoming=False)
        proto.factory = Factory(StubNode())
        proto.makeConnection(Transport('10.9.8.7', 9333))
        long_ver = dict(VER, sub_version=b'/P2Pool:' + b'x' * 600 + b'/')

        proto.dataReceived(frame_msg('version', long_ver))

        self.assertIsInstance(proto.other_sub_version, str)
        self.assertEqual(len(proto.other_sub_version), 512)
        self.assertTrue(proto.other_sub_version.startswith('/P2Pool:'))

    def test_getaddrs_clamps_count(self):
        node = StubNode()
        node.addr_store = {
            ('198.51.100.1', 9333): (1, 1000, 2000),
            ('198.51.100.2', 9334): (1, 1001, 2001),
            ('198.51.100.3', 9335): (1, 1002, 2002),
        }
        proto = make_proto(node, peer_host='10.9.8.7')
        del proto.transport.buf[:]

        proto.dataReceived(frame_msg('getaddrs', dict(count=200)))

        commands = []
        for raw in proto.transport.buf:
            command = raw[len(PREFIX):len(PREFIX) + 12].rstrip(b'\0')
            commands.append(command.decode('ascii'))
        self.assertIn('addrs', commands)
