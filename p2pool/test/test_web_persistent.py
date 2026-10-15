"""Web persistent/background-path regression tests.

The 56 GET handlers are pinned by test_web.py; these cover the paths that
keep running after the request ends: the background loops, the files they
write, and the resources they feed. web_persistent_parity.py proved every
scenario here byte-identical against PyPy 2.7 against pristine source
(worktree cf438b6); the expectations below are py2's behavior.

What runs: update_stat_log (preload, 24h prune, append, stats rewrite,
/web/log), the network-difficulty history load/save + period filters, the
graph_db save + graph_data serving, static.File 200/404, and the
_atomic_read/_atomic_write helpers (the str->bytes encode fix).

Needs a 120-share chain: update_stat_log early-returns below
3600//SHARE_PERIOD == 120, so the append+write would never fire.
"""
import json
import os
import tempfile
import time
import unittest

import p2pool
p2pool.BENCH = False

from p2pool import web
from p2pool.util import variable

from p2pool.test.test_web import _build, _make_node, _make_wb, _StubPeer

from twisted.internet import defer, reactor
from twisted.web import resource, server
from twisted.web.test.requesthelper import DummyRequest

STAT_KEYS = set([
    'time', 'pool_hash_rate', 'pool_stale_prop', 'local_hash_rates',
    'local_dead_hash_rates', 'shares', 'stale_shares',
    'stale_shares_breakdown', 'current_payout', 'peers',
    'attempts_to_share', 'attempts_to_block', 'block_value',
])


class WebPersistentTest(unittest.TestCase):
    def setUp(self):
        net, tracker, chain, address = _build(120)
        self.chain = chain
        self.address = address
        node = _make_node(net, tracker, chain[-1].hash)
        node.p2p_node.peers = {
            ('10.0.0.2', 19448): _StubPeer('10.0.0.2', 19448, False),
            ('10.0.0.3', 19449): _StubPeer('10.0.0.3', 19449, True),
        }
        wb = _make_wb(node, address, chain)

        self.stop_event = variable.Event()
        self.datadir = tempfile.mkdtemp(prefix='p2pool-webp-datadir-')
        self.static_dir = tempfile.mkdtemp(prefix='p2pool-webp-static-')

        self.now = time.time()
        # one entry past the 24h prune window, one inside it
        seed_stats = [
            {'time': self.now - 25 * 3600, 'marker': 'stale',
             'pool_hash_rate': 1.0},
            {'time': self.now - 3600, 'marker': 'fresh',
             'pool_hash_rate': 2.0},
        ]
        with open(os.path.join(self.datadir, 'stats'), 'wb') as f:
            f.write(json.dumps(seed_stats).encode('utf-8'))
        # one entry inside the day window only, one inside the year only
        seed_nd = [
            {'ts': self.now - 2 * 86400, 'network_diff': 2.5,
             'source': 'block'},
            {'ts': self.now - 7200, 'network_diff': 1.5, 'source': 'block'},
        ]
        with open(os.path.join(self.datadir,
                               'network_difficulty_history'), 'wb') as f:
            f.write(json.dumps(seed_nd).encode('utf-8'))
        # graph_db only as .new: exercises _atomic_read's fallback read
        with open(os.path.join(self.datadir, 'graph_db.new'), 'wb') as f:
            f.write(b'{}')
        with open(os.path.join(self.static_dir, 'test.txt'), 'wb') as f:
            f.write(b'static parity body\n')

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
        for call in reactor.getDelayedCalls():
            call.cancel()

    @defer.inlineCallbacks
    def _request(self, segments, args=None):
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
        code = request.responseCode or 200
        try:
            ct = request.responseHeaders.getRawHeaders('content-type')
        except Exception:
            ct = None
        defer.returnValue((code, out, ct))

    def _read_json(self, name):
        with open(os.path.join(self.datadir, name), 'rb') as f:
            return json.loads(f.read())

    def test_update_stat_log_prunes_appends_and_writes(self):
        # get_web_root runs update_stat_log once: the stale entry is popped,
        # a fresh entry appended, and the file rewritten as utf-8 bytes.
        entries = self._read_json('stats')
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].get('marker'), 'fresh')
        self.assertNotIn('marker', entries[1])
        self.assertEqual(set(entries[1]), STAT_KEYS)
        self.assertLess(abs(entries[1]['time'] - self.now), 120)
        self.assertEqual(entries[1]['peers'],
                         {'incoming': 1, 'outgoing': 1})
        self.assertEqual(entries[1]['block_value'], 50.0)
        self.assertEqual(entries[1]['shares'], 120)

    @defer.inlineCallbacks
    def test_web_log_matches_stats_file(self):
        code, body, ct = yield self._request(['web', 'log'])
        self.assertEqual(code, 200)
        self.assertEqual(ct, ['application/json'])
        self.assertEqual(json.loads(body.decode('utf-8')),
                         self._read_json('stats'))

    @defer.inlineCallbacks
    def test_network_difficulty_history_load_save_and_filters(self):
        # the save loop ran during construction: file must hold the two
        # preloaded samples (a failed load would have rewritten it empty)
        saved = self._read_json('network_difficulty_history')
        self.assertEqual([e['source'] for e in saved],
                         ['block', 'block'])
        self.assertEqual(len(saved), 2)

        # hour: preloaded periodic sample + the live 'current' sample
        code, body, ct = yield self._request(['network_difficulty'],
                                             args={'period': ['hour']})
        samples = json.loads(body.decode('utf-8'))
        self.assertEqual(len(samples), 2)
        self.assertEqual(set(e['source'] for e in samples),
                         set(['periodic', 'current']))

        # no args and a bogus period both fall back to the hour cutoff
        code, body, _ = yield self._request(['network_difficulty'])
        self.assertEqual(len(json.loads(body.decode('utf-8'))), 2)
        code, body, _ = yield self._request(['network_difficulty'],
                                            args={'period': ['bogus']})
        self.assertEqual(len(json.loads(body.decode('utf-8'))), 2)

    @defer.inlineCallbacks
    def test_network_difficulty_day_and_year_windows(self):
        code, body, ct = yield self._request(['network_difficulty'],
                                             args={'period': ['day']})
        samples = json.loads(body.decode('utf-8'))
        self.assertEqual(len(samples), 3)
        self.assertEqual(samples[0]['source'], 'block')
        self.assertEqual(samples[0]['network_diff'], 1.5)

        code, body, ct = yield self._request(['network_difficulty'],
                                             args={'period': ['year']})
        samples = json.loads(body.decode('utf-8'))
        self.assertEqual(len(samples), 4)
        self.assertEqual([e['network_diff'] for e in samples[:2]],
                         [2.5, 1.5])

    @defer.inlineCallbacks
    def test_graph_db_written_and_graph_data_served(self):
        # the save loop wrote graph_db during construction (preloaded via
        # the .new fallback); add_point populated the streams
        with open(os.path.join(self.datadir, 'graph_db'), 'rb') as f:
            graph_db = json.loads(f.read())
        self.assertIn('pool_rates', graph_db)
        self.assertFalse(os.path.exists(
            os.path.join(self.datadir, 'graph_db.new')))

        code, body, ct = yield self._request(
            ['web', 'graph_data', 'pool_rates', 'last_hour'])
        self.assertEqual(code, 200)
        self.assertEqual(ct, ['application/json'])
        bins = json.loads(body.decode('utf-8'))
        self.assertIsInstance(bins, list)
        self.assertTrue(bins)

        # unknown stream serves [] instead of raising
        code, body, ct = yield self._request(
            ['web', 'graph_data', 'does_not_exist', 'last_hour'])
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body.decode('utf-8')), [])

    @defer.inlineCallbacks
    def test_static_file_200_and_404(self):
        code, body, ct = yield self._request(['static', 'test.txt'])
        self.assertEqual(code, 200)
        self.assertEqual(ct, ['text/plain'])
        self.assertEqual(body, b'static parity body\n')

        code, body, ct = yield self._request(['static', 'missing.txt'])
        self.assertEqual(code, 404)

    def test_atomic_helpers_round_trip(self):
        tmp = os.path.join(self.datadir, 'atomic_probe')
        self.assertIsNone(web._atomic_read(tmp))

        # .new fallback: only file.new exists, no file
        with open(tmp + '.new', 'wb') as f:
            f.write(b'from-new-file')
        self.assertEqual(web._atomic_read(tmp), b'from-new-file')

        # bytes pass through untouched and .new is renamed away
        web._atomic_write(tmp, b'raw-bytes-\x00\xff')
        with open(tmp, 'rb') as f:
            self.assertEqual(f.read(), b'raw-bytes-\x00\xff')
        self.assertFalse(os.path.exists(tmp + '.new'))

        # str is encoded to utf-8 before the binary write (the py3 fix);
        # ascii keeps py2's implicit encode identical
        web._atomic_write(tmp, u'text-cafe')
        with open(tmp, 'rb') as f:
            self.assertEqual(f.read(), b'text-cafe')
        self.assertEqual(web._atomic_read(tmp), b'text-cafe')