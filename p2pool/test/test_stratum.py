"""Stratum notify-flow regression test.

stratum_parity.py verified the hex helpers in isolation; this drives the real
_send_work() against a stub WorkerInterface and pins the exact JSON frames a
miner receives, plus the provider state machine around them (target
selection, FR-1.15 jobid monotonicity, set_difficulty suppression).

Expected values are py2's: the identical harness was run under PyPy 2.7
against pristine source (git worktree cf438b6) and the outputs diffed
byte-for-byte -- no repr normalisation needed.
"""
import json
import unittest

from twisted.internet import defer

from p2pool import networks as pnet
from p2pool.bitcoin import data as bitcoin_data
from p2pool.bitcoin import stratum
from p2pool.util import jsonrpc, variable

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

# Exact wire frames.  Key order is OrderedDict insertion order -- jsonrpc is
# appended last -- because strict CGMiner-derived parsers in some stock
# firmware key off field sequence.  These are py2's bytes.
NOTIFY_1 = ('{"id": null, "method": "mining.notify", "params": ["1", '
            '"89abcdef0123456789abcdef0123456789abcdef0123456789abcdef01234567", '
            '"03010203", "00000000", '
            '["1111111111111111111111111111111111111111111111111111111111111111"], '
            '"20000000", "1d00ffff", "65000000", true], "jsonrpc": "2.0"}')
SETDIFF_1 = ('{"id": null, "method": "mining.set_difficulty", '
             '"params": [0.015624761581420898], "jsonrpc": "2.0"}')
NOTIFY_2 = ('{"id": null, "method": "mining.notify", "params": ["2", '
            '"89abcdf00123456789abcdef0123456789abcdef0123456789abcdef01234567", '
            '"03010203", "00000000", '
            '["1111111111111111111111111111111111111111111111111111111111111111"], '
            '"20000000", "1d00ffff", "65000001", true], "jsonrpc": "2.0"}')
SETDIFF_EASY = ('{"id": null, "method": "mining.set_difficulty", '
                '"params": [1.5258556231856346e-05], "jsonrpc": "2.0"}')


def make_wb(work_value):
    class WB(object):
        share_rate = 30.0
        net = net.PARENT

        def __init__(self):
            self.new_work_event = variable.Event()

        def preprocess_request(self, username):
            return ()

        def get_work(self, *a):
            return work_value, lambda *a, **kw: None

    return WB()


class Transport(object):
    def getPeer(self):
        class P(object):
            host = '127.0.0.1'
            port = 3333
        return P()

    def loseConnection(self):
        pass

    def abortConnection(self):
        pass


def make_service(work):
    sent = []

    def func(method, params):
        # Exactly what GenericDeferrer ends up calling: the frame jsonrpc
        # would write to the wire, newline included.
        sent.append(jsonrpc._frame_request(0, method, params).encode('utf-8') + b'\n')
        return defer.succeed(None)

    svc = stratum.StratumRPCMiningProvider(make_wb(work), jsonrpc.Proxy(func),
                                           Transport())
    return svc, sent


def frames(sent):
    return [raw.decode('utf-8').rstrip('\n') for raw in sent]


def dump(obj):
    return json.dumps(obj, sort_keys=True)


class StratumNotifyFlowTest(unittest.TestCase):
    def test_initial_send_matches_py2(self):
        svc, sent = make_service(WORK)
        svc._send_work()
        self.assertEqual(svc.target, 2 ** 230)
        self.assertFalse(svc.fixed_target)
        self.assertEqual(svc._jobid_counter, 1)
        self.assertEqual(len(sent), 2)
        self.assertEqual(frames(sent), [SETDIFF_1, NOTIFY_1])

    def test_jobid_increments_and_diff_is_not_repeated(self):
        # FR-1.15 quirk #1: jobids must be monotonically increasing, and
        # set_difficulty must not repeat when the diff has not changed.
        svc, sent = make_service(WORK)
        svc._send_work()
        del sent[:]
        w2 = dict(WORK, previous_block=WORK['previous_block'] + 1,
                  timestamp=WORK['timestamp'] + 1)
        svc.wb = make_wb(w2)
        svc._send_work()
        self.assertEqual(svc._jobid_counter, 2)
        self.assertEqual(len(sent), 1)   # notify only, no repeated set_difficulty
        self.assertEqual(frames(sent), [NOTIFY_2])

    def test_easier_target_changes_difficulty(self):
        svc, sent = make_service(dict(WORK, share_target=2 ** 240))
        svc._send_work()
        self.assertEqual(len(sent), 2)
        self.assertEqual(frames(sent)[0], SETDIFF_EASY)

    def test_pseudoshare_target_is_fixed(self):
        svc, sent = make_service(WORK)
        svc.desired_pseudoshare_target = 2 ** 228
        svc._send_work()
        self.assertTrue(svc.fixed_target)
        self.assertEqual(svc.target, 2 ** 228)

    def test_notify_frames_are_json_strings_not_bytes(self):
        # The flagged stratum risk: outbound must be hex str, not bytes,
        # because these values go straight into a JSON document.
        svc, sent = make_service(WORK)
        svc._send_work()
        obj = json.loads(frames(sent)[1])
        self.assertEqual(obj['params'][0], '1')            # jobid str
        self.assertIsInstance(obj['params'][1], str)       # prevhash hex str
        self.assertIsInstance(obj['params'][4][0], str)    # merkle branch str
        self.assertIs(obj['params'][8], True)              # clean_jobs bool


if __name__ == '__main__':
    unittest.main()