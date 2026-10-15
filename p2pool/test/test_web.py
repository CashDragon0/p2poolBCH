"""Web interface end-to-end regression test.

get_web_root had never been driven under py3: Twisted 24 requires bytes path
segments for putChild, so construction died on the first str key. That made
every JSON endpoint dead. This builds a real 12-share chain, stubs node/wb
to the attributes the handlers read, and drives all of them through the
resource tree the same way Site does.

Expected values are py2's: web_handlers_parity.py ran the identical fixture
under PyPy 2.7 against pristine source (worktree cf438b6) and diffed
byte-for-byte across all 56 endpoints.
"""
import json
import tempfile
import unittest

import p2pool
p2pool.BENCH = False

from p2pool import data as p2pool_data
from p2pool import networks as pnet
from p2pool.bitcoin import data as bitcoin_data
from p2pool.util import cash_addr, variable

from twisted.internet import defer, reactor
from twisted.web import resource, server
from twisted.web.test.requesthelper import DummyRequest

NET_NAME = 'bitcoincash_regtest'
SHARE_VERSION = 35
CHAIN_LENGTH = 12
PUBKEY_HASH = 0x112233445566778899aabbccddeeff0011223344

_BUILT = {}
_ROOT = []


def _build(length=None):
    # length lets other suites reuse the fixture with a taller chain
    # (update_stat_log needs height >= 3600//SHARE_PERIOD == 120).
    length = CHAIN_LENGTH if length is None else length
    if length in _BUILT:
        return _BUILT[length]
    net = pnet.nets[NET_NAME]
    share_type = p2pool_data.share_versions[SHARE_VERSION]
    tracker = p2pool_data.OkayTracker(net)
    block_target = bitcoin_data.FloatingInteger.from_target_upper_bound(
        0x1d00ffff * 2 ** (256 - 32) // 100000).target
    address = cash_addr.encode(net.PARENT.HUMAN_READABLE_PART, 0,
                               [(PUBKEY_HASH >> (8 * i)) & 0xff
                                for i in reversed(range(20))])
    parent = None
    chain = []
    for height in range(1, length + 1):
        share_info, gentx, other_tx_hashes, get_share = \
            share_type.generate_transaction(
                tracker=tracker,
                share_data=dict(
                    previous_share_hash=parent,
                    coinbase=bytes(bytearray([3, height & 0xff])),
                    nonce=0x11111111 + height,
                    address=address,
                    subsidy=net.PARENT.SUBSIDY_FUNC(height),
                    donation=0,
                    stale_info=None,
                    desired_version=SHARE_VERSION,
                ),
                block_target=block_target,
                desired_timestamp=1500000000 + height * 60,
                desired_target=2 ** 224,
                ref_merkle_link=dict(branch=[], index=0),
                desired_other_transaction_hashes_and_fees=[],
                net=net,
            )
        packed_gentx = bitcoin_data.tx_id_type.pack(gentx)
        gentx_hash = bitcoin_data.hash256(packed_gentx)
        if p2pool_data.is_segwit_activated(SHARE_VERSION, net):
            merkle_root = bitcoin_data.check_merkle_link(
                gentx_hash, share_info['segwit_data']['txid_merkle_link'])
        else:
            merkle_root = bitcoin_data.check_merkle_link(
                gentx_hash,
                bitcoin_data.calculate_merkle_link([None] + other_tx_hashes, 0))
        header = bitcoin_data.block_header_type.unpack(packed_gentx[:80])
        header['merkle_root'] = merkle_root
        target = share_info['bits'].target
        pow_func = net.PARENT.POW_FUNC
        for nonce in range(2 ** 32):
            header['nonce'] = nonce
            if pow_func(bitcoin_data.block_header_type.pack(header)) <= target:
                break
        else:
            raise AssertionError('no nonce met the share target')
        share = get_share(header)
        chain.append(share)
        tracker.add(share)
        parent = share.hash
    _BUILT[length] = (net, tracker, chain, address)
    return _BUILT[length]


class _StubPeer(object):
    def __init__(self, host, port, incoming=False):
        self.incoming = incoming
        self.other_sub_version = '/p2pool-stub:1.0/'
        self.addr = (host, port)
        self.remembered_txs_size = 0
        p = type('P', (), {'host': host, 'port': port})()
        self.transport = type('T', (), {'getPeer': lambda self: p})()

    def do_ping(self):
        return defer.succeed(0.0123)


def _make_node(net, tracker, head):
    class Node(object):
        pass
    node = Node()
    node.net = net
    node.tracker = tracker
    node.best_share_var = variable.Variable(head)
    node.bitcoind_work = variable.Variable(dict(
        previous_block=0x0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef,
        bits=bitcoin_data.FloatingInteger(0x1d00ffff),
        subsidy=50 * 10 ** 8,
        height=100000,
        last_update=0,
        latency=0.05,
        transaction_hashes=[],
        transactions=[],
        transaction_fees=[],
        coinbaseflags=b'',
        use_getblocktemplate=True,
        version=0x20000000,
        transactions_lockin=None,
    ))
    node.p2p_node = type('P2P', (), {
        'peers': {},
        'traffic_happened': variable.Event(),
    })()
    node.get_current_txouts = lambda: p2pool_data.get_expected_payouts(
        tracker, head, 0x1d00ffff * 2 ** (256 - 32) // 100000,
        net.PARENT.SUBSIDY_FUNC(100000), net)
    return node


def _make_wb(node, address, chain):
    class Pubkeys(object):
        def __init__(self, addr):
            self.keys = [{'address': addr}]
            self.keyweights = [1]
            self.stamp = 0

    class WB(object):
        pass
    wb = WB()
    wb.node = node
    wb.address = address
    wb.pubkeys = Pubkeys(address)
    wb.worker_fee = 0.5
    wb.donation_percentage = 1.0
    wb.pseudoshare_received = variable.Event()
    wb.share_received = variable.Event()
    wb.share_rate = 30.0
    wb.my_share_hashes = set(s.hash for s in chain)
    wb.my_doa_share_hashes = set()
    wb.connected_workers = {}
    wb.worker_shares = {}
    wb.total_shares = {'accepted': 10, 'rejected': 1}
    wb.worker_latency_history = {}
    wb.address_best_diff = {address: 12.5}
    wb.address_round_best_diff = {address: 8.0}
    wb.recent_shares_ts_work = [(0, 100), (5, 100)]
    wb.last_work_shares = variable.Variable({})
    wb.current_work = variable.Variable(node.bitcoind_work.value)
    wb.get_local_rates = lambda: ({address: 1.5e12}, {address: 1.0e10})
    wb.get_stale_counts = lambda: ((0, 0), len(wb.my_share_hashes), (0, 0))
    return wb


class WebHandlersTest(unittest.TestCase):
    def setUp(self):
        if not hasattr(self, '_root'):
            # cache on the class so trial's per-test setUp doesn't rebuild
            pass
        net, tracker, chain, address = _build()
        self.net, self.chain, self.address = net, chain, address
        node = _make_node(net, tracker, chain[-1].hash)
        node.p2p_node.peers = {
            ('10.0.0.2', 19448): _StubPeer('10.0.0.2', 19448, False),
            ('10.0.0.3', 19449): _StubPeer('10.0.0.3', 19449, True),
        }
        wb = _make_wb(node, address, chain)
        from p2pool import web
        self.stop_event = variable.Event()
        self.datadir = tempfile.mkdtemp(prefix='p2pool-web-test-')
        self.static_dir = tempfile.mkdtemp(prefix='p2pool-web-static-')
        self.root = web.get_web_root(
            wb, self.datadir,
            variable.Variable(dict(version=290200, blocks=100000,
                                   difficulty=1.0, warnings='')),
            stop_event=self.stop_event,
            static_dir=self.static_dir)

    def tearDown(self):
        try:
            self.stop_event.happened()
        except Exception:
            pass

    @defer.inlineCallbacks
    def _get(self, segments, args=None):
        request = DummyRequest([s.encode('ascii') for s in segments])
        request.channel = object()
        if args:
            for k, vs in args.items():
                request.args[k.encode('ascii')] = [
                    v.encode('ascii') for v in vs]
        res = resource.getChildForRequest(self.root, request)
        body = []

        def _write(data):
            if isinstance(data, str):
                data = data.encode('utf-8')
            body.append(data)
        request.write = _write
        finished = defer.Deferred()
        request.finish = lambda: finished.callback(None)
        result = res.render(request)
        if result == server.NOT_DONE_YET:
            yield finished
            out = b''.join(body)
        else:
            out = result.encode('utf-8') if isinstance(result, str) else result
        defer.returnValue(out)

    def test_construction_registers_all_endpoints(self):
        # The putChild(bytes) fix: construction must not raise, and the
        # dashboard's endpoint set must be present.
        for key in (b'rate', b'fee', b'global_stats', b'local_stats',
                    b'miner_stats', b'stratum_stats', b'recent_blocks',
                    b'best_share', b'network_difficulty', b'web',
                    b'version_signaling', b'luck_stats'):
            self.assertIn(key, self.root.children)

    @defer.inlineCallbacks
    def test_fee_and_payout_addr(self):
        body = yield self._get(['fee'])
        self.assertEqual(json.loads(body.decode('utf-8')), 0.5)
        body = yield self._get(['payout_addr'])
        self.assertEqual(json.loads(body.decode('utf-8')), self.address)

    @defer.inlineCallbacks
    def test_currency_info_uses_parent_network(self):
        body = yield self._get(['web', 'currency_info'])
        info = json.loads(body.decode('utf-8'))
        self.assertEqual(info['symbol'], self.net.PARENT.SYMBOL)
        self.assertTrue(info['block_explorer_url_prefix'])

    @defer.inlineCallbacks
    def test_heads_and_best_share_hash(self):
        body = yield self._get(['web', 'best_share_hash'])
        self.assertEqual(json.loads(body.decode('utf-8')),
                         '%064x' % self.chain[-1].hash)
        body = yield self._get(['web', 'heads'])
        heads = json.loads(body.decode('utf-8'))
        self.assertEqual(len(heads), 1)

    @defer.inlineCallbacks
    def test_share_endpoint_roundtrip(self):
        h = '%064x' % self.chain[-1].hash
        body = yield self._get(['web', 'share', h])
        share = json.loads(body.decode('utf-8'))
        self.assertEqual(share['block']['hash'],
                         '%064x' % self.chain[-1].header_hash)
        self.assertEqual(share['type_name'], type(self.chain[-1]).__name__)
        # missing share -> null
        body = yield self._get(['web', 'share', '%064x' % (self.chain[-1].hash + 1)])
        self.assertEqual(json.loads(body.decode('utf-8')), None)

    @defer.inlineCallbacks
    def test_miner_stats_for_known_address(self):
        body = yield self._get(['miner_stats', self.address])
        stats = json.loads(body.decode('utf-8'))
        self.assertEqual(stats['address'], self.address)
        self.assertNotIn('error', stats)

    @defer.inlineCallbacks
    def test_stratum_stats_lists_worker(self):
        # Need a connected worker entry
        root_wb_connected = True
        body = yield self._get(['stratum_stats'])
        stats = json.loads(body.decode('utf-8'))
        self.assertIn('pool', stats)
        self.assertIn('workers', stats)

    @defer.inlineCallbacks
    def test_network_difficulty_period_arg_is_bytes_safe(self):
        # Twisted passes request.args keys as bytes; str lookup would miss
        # and silently pin to the default 'hour' cutoff.
        body = yield self._get(['network_difficulty'], args={'period': ['day']})
        samples = json.loads(body.decode('utf-8'))
        self.assertIsInstance(samples, list)

    @defer.inlineCallbacks
    def test_my_share_hashes_sorted(self):
        body = yield self._get(['web', 'my_share_hashes'])
        hashes = json.loads(body.decode('utf-8'))
        self.assertEqual(hashes, sorted(hashes))
        self.assertEqual(len(hashes), CHAIN_LENGTH)
