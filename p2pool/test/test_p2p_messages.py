"""p2pool-on-p2pool message-type parity.

wire_parity.py proved the framing; this pins the payload types.  Expected bytes
are py2's: the identical harness run under PyPy 2.7 against pristine source
(git worktree cf438b6) and diffed -- every packed hex line and every repack
check matched.

The negative vectors only assert that pack raises.  The exception class drifts
between interpreters (py2 raises ValueError where py3 raises TypeError); the
rejection itself is the contract.

sub_version unpacks as bytes here and as str under py2 -- that is the intended
py2->py3 mapping (py2 str *is* bytes) and is why the hex is what's pinned.
"""
import binascii
import unittest

from p2pool import p2p
from p2pool.bitcoin import data as bitcoin_data

H = 0x0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
BITS = bitcoin_data.FloatingInteger(0x1d00ffff)


def addr(port=8333):
    return dict(services=1, address='127.0.0.1', port=port)


HEADER = dict(version=1, previous_block=H, merkle_root=H,
              timestamp=1700000000, bits=BITS, nonce=7)

CASES = [
    ('ping', dict()),
    ('version', dict(version=1400, services=1, nonce=0x1122334455667788,
                     sub_version='/P2Pool:test/', mode=1, best_share_hash=H,
                     addr_to=addr(1), addr_from=addr(2))),
    ('bestblock', dict(header=HEADER)),
    ('addrme', dict(port=8333)),
    ('addrs', dict(addrs=[dict(timestamp=1700000000, address=addr()),
                          dict(timestamp=1700000001, address=addr(8334))])),
    ('getaddrs', dict(count=10)),
    ('sharereq', dict(id=0x1122334455667788, hashes=[H], parents=5, stops=[H])),
    ('sharereply', dict(id=0x1122334455667788, result='good', shares=[])),
    ('have_tx', dict(tx_hashes=[H])),
    ('losing_tx', dict(tx_hashes=[H])),
    ('remember_tx', dict(tx_hashes=[H], txs=[])),
    ('forget_tx', dict(tx_hashes=[H])),
]

# (name, length, packed hex) -- py2's output
EXPECTED = {
    'ping': (0, ''),
    'version': (122, '780500000100000000000000010000000000000000000000000000000000ffff7f0000010001010000000000000000000000000000000000ffff7f000001000288776655443322110d2f5032506f6f6c3a746573742f01000000efcdab8967452301efcdab8967452301efcdab8967452301efcdab8967452301'),
    'bestblock': (80, '01000000efcdab8967452301efcdab8967452301efcdab8967452301efcdab8967452301efcdab8967452301efcdab8967452301efcdab8967452301efcdab896745230100f15365ffff001d07000000'),
    'addrme': (2, '8d20'),
    'addrs': (69, '0200f1536500000000010000000000000000000000000000000000ffff7f000001208d01f1536500000000010000000000000000000000000000000000ffff7f000001208e'),
    'getaddrs': (4, '0a000000'),
    'sharereq': (99, '887766554433221100000000000000000000000000000000000000000000000001efcdab8967452301efcdab8967452301efcdab8967452301efcdab89674523010501efcdab8967452301efcdab8967452301efcdab8967452301efcdab8967452301'),
    'sharereply': (34, '88776655443322110000000000000000000000000000000000000000000000000000'),
    'have_tx': (33, '01efcdab8967452301efcdab8967452301efcdab8967452301efcdab8967452301'),
    'losing_tx': (33, '01efcdab8967452301efcdab8967452301efcdab8967452301efcdab8967452301'),
    'remember_tx': (34, '01efcdab8967452301efcdab8967452301efcdab8967452301efcdab896745230100'),
    'forget_tx': (33, '01efcdab8967452301efcdab8967452301efcdab8967452301efcdab8967452301'),
}


def hx(b):
    return binascii.hexlify(b).decode('ascii')


class P2PMessageParityTest(unittest.TestCase):
    def test_packed_bytes_match_py2(self):
        self.assertEqual(len(CASES), len(EXPECTED))
        for name, payload in CASES:
            t = getattr(p2p.Protocol, 'message_' + name)
            raw = t.pack(payload)
            elen, ehex = EXPECTED[name]
            self.assertEqual(len(raw), elen, name)
            self.assertEqual(hx(raw), ehex, name)

    def test_unpack_repacks_identically(self):
        for name, payload in CASES:
            t = getattr(p2p.Protocol, 'message_' + name)
            raw = t.pack(payload)
            self.assertEqual(t.pack(t.unpack(raw)), raw, name)

    def test_version_sub_version_is_bytes(self):
        # py2 str == py3 bytes; pack encodes str on the way out.
        t = p2p.Protocol.message_version
        raw = t.pack(dict(CASES[1][1]))
        self.assertEqual(t.unpack(raw)['sub_version'], b'/P2Pool:test/')

    def test_bad_payloads_are_rejected(self):
        for name, payload in [('addrme', dict(port='x')),
                              ('sharereq', dict(id=H, hashes='notalist',
                                                parents=1, stops=[])),
                              ('forget_tx', dict(tx_hashes=[H, 'x']))]:
            t = getattr(p2p.Protocol, 'message_' + name)
            # Exception class drifts py2 ValueError -> py3 TypeError; only the
            # rejection is contractual.
            self.assertRaises(Exception, t.pack, payload)


if __name__ == '__main__':
    unittest.main()