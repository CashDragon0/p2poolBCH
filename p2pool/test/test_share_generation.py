"""Share generation regression test -- the coverage the golden gate cannot reach.

The golden vectors stop at primitives; generating a share needs a Tracker
populated with parents, so share construction in data.py/work.py was
untested.  That is how the work.py lazy-zip bug (which would have made the
node reject every share) passed the gate at 3/921.

This builds a real six-share chain through the production
generate_transaction()/get_share() path and pins every consensus-visible
byte.  The expected values were produced by running the identical harness
under PyPy 2.7 against pristine py2 source (git worktree cf438b6) and
diffing -- they are py2's output, not py3's.

Run under py2 as:
    PYTHONPATH=. ~/p2pool-port-work/pypy2/bin/pypy p2pool/test/test_share_generation.py
"""
import binascii
import unittest

import p2pool
p2pool.BENCH = False

from p2pool import data as p2pool_data
from p2pool.bitcoin import data as bitcoin_data
from p2pool.util import cash_addr

NET_NAME = 'bitcoincash_regtest'
SHARE_VERSION = 35
CHAIN_LENGTH = 6
PUBKEY_HASH = 0x112233445566778899aabbccddeeff0011223344

# (height, share.hash, share_type.pack(contents), packed gentx)
EXPECTED = [
        (1,
         '006e135f62aba69e00a478f7da5bf0c924192c5d4e3f0fe6042fb9335d305851',
         '0101000000000000000000000000000000000000000000000000000000000000008899aabbccddeeff1d000000000000000000000000000000000000000000000000000000000000000000000002030112111111316263687265673a7171676a7976367932346e38307a796534326175656830776c7571707a67336e6773683866637064663400f2052a01000000000000230000000000000000000000000000000000000000000000000000000000000000ffff7f20444404203c105e5f010000003c000000000000000000000000000000000000000000000000ad9276ef69c2587c8de6728aa7a4293a449fe68e2d3ce7acd726da7d9dcd662baa00',
         '01000000010000000000000000000000000000000000000000000000000000000000000000ffffffff020301ffffffff0340787d01000000001976a914112233445566778899aabbccddeeff001122334488acc079882801000000434104ffd03de44a6e11b9917f3a29f9443283d9871c9d743ef30d5eddcd37094b64d1b3d8090496b53256786bf5c82932ec23c3b74d9f05a6f95a8b5529352656664bac00000000000000002a6a285fdb4d25623737755be8ef154aa083445f8e685966dd8b8958a89efd95c727e5000000000000000000000000'),
        (2,
         '02044b64ddd211e4b4480e14a3b79dc4431e8b19f052713d179a9fe611ef03ad',
         '0101000000000000000000000000000000000000000000000000000000000000008899aabbccddeeff470000005158305d33b92f04e60f3f4e5d2c1924c9f05bdaf778a4009ea6ab625f136e0002030213111111316263687265673a7171676a7976367932346e38307a796534326175656830776c7571707a67336e6773683866637064663400f2052a01000000000000230000000000000000000000000000000000000000000000000000000000000000ffff7f204444042078105e5f0200000078000000000000000000000000000000000000000000000000a7f9741ed21c656a5436129b21fd7507c64cd84093cf291e00bfc898367e0309aa00',
         '01000000010000000000000000000000000000000000000000000000000000000000000000ffffffff020302ffffffff0340787d01000000001976a914112233445566778899aabbccddeeff001122334488acc079882801000000434104ffd03de44a6e11b9917f3a29f9443283d9871c9d743ef30d5eddcd37094b64d1b3d8090496b53256786bf5c82932ec23c3b74d9f05a6f95a8b5529352656664bac00000000000000002a6a287a48a658a31014e931a93e32be97c00265f5f5e51e52232eec5961d08cfaf748000000000000000000000000'),
        (3,
         '043ceaaabc7d5232222ecf168995bf50c87dbad25545935461ee5775e2192b4d',
         '0101000000000000000000000000000000000000000000000000000000000000008899aabbccddeeff60000000ad03ef11e69f9a173d7152f0198b1e43c49db7a3140e48b4e411d2dd644b040202030314111111316263687265673a7171676a7976367932346e38307a796534326175656830776c7571707a67336e6773683866637064663400f2052a01000000000000230000000000000000000000000000000000000000000000000000000000000000ffff7f2044440420b4105e5f03000000b4000000000000000000000000000000000000000000000000bee0a52ef15800657e7eb270d22bb0a9093fc68a2d0ec055bf6f5c1be204f427aa00',
         '01000000010000000000000000000000000000000000000000000000000000000000000000ffffffff020303ffffffff0300f2052a010000001976a914112233445566778899aabbccddeeff001122334488ac0000000000000000434104ffd03de44a6e11b9917f3a29f9443283d9871c9d743ef30d5eddcd37094b64d1b3d8090496b53256786bf5c82932ec23c3b74d9f05a6f95a8b5529352656664bac00000000000000002a6a28f9da05de3914130da04b1b56bdf467f9772322212ce18860f41ce00ff1450bd7000000000000000000000000'),
        (4,
         '0266e75ac67d58ea7090cb375bbaecc0dbcc92f5370a63fac6d934746265a401',
         '0101000000000000000000000000000000000000000000000000000000000000008899aabbccddeeff0e0000004d2b19e27557ee6154934555d2ba7dc850bf958916cf2e2232527dbcaaea3c0402030415111111316263687265673a7171676a7976367932346e38307a796534326175656830776c7571707a67336e6773683866637064663400f2052a01000000000000230000000000000000000000000000000000000000000000000000000000000000ffff7f2044440420f0105e5f04000000f000000000000000000000000000000000000000000000000020ceb5c8e67cfb6473e3e7eb83ebd82d3b0d837cf91a675ef2be2667d3768660aa00',
         '01000000010000000000000000000000000000000000000000000000000000000000000000ffffffff020304ffffffff0300f2052a010000001976a914112233445566778899aabbccddeeff001122334488ac0000000000000000434104ffd03de44a6e11b9917f3a29f9443283d9871c9d743ef30d5eddcd37094b64d1b3d8090496b53256786bf5c82932ec23c3b74d9f05a6f95a8b5529352656664bac00000000000000002a6a28b47eac9de0939362e9560c0a39a01ecb5d74c61293ce530b884321a758137f5d000000000000000000000000'),
        (5,
         '035f2311c49d42b034a645d77528fa57d3b7c9b59b2f2a0120bc48dc4f842da5',
         '0101000000000000000000000000000000000000000000000000000000000000008899aabbccddeeffd200000001a465627434d9c6fa630a37f592ccdbc0ecba5b37cb9070ea587dc65ae7660202030516111111316263687265673a7171676a7976367932346e38307a796534326175656830776c7571707a67336e6773683866637064663400f2052a01000000000000230000000000000000000000000000000000000000000000000000000000000000ffff7f20444404202c115e5f050000002c010000000000000000000000000000000000000000000000a2d000d5a16ee67131cc1c983f50b0f035de52ddec062d3931dbc39176c34aa7aa00',
         '01000000010000000000000000000000000000000000000000000000000000000000000000ffffffff020305ffffffff0300f2052a010000001976a914112233445566778899aabbccddeeff001122334488ac0000000000000000434104ffd03de44a6e11b9917f3a29f9443283d9871c9d743ef30d5eddcd37094b64d1b3d8090496b53256786bf5c82932ec23c3b74d9f05a6f95a8b5529352656664bac00000000000000002a6a2822f18a4efad6523bc47cc108a78de12ebbcb2a2307bc5505a53524ba89074c4b000000000000000000000000'),
        (6,
         '043d324198be2a0877cb74d976515c8f6bfb298121fe402f27f9a4b66b1e3af0',
         '0101000000000000000000000000000000000000000000000000000000000000008899aabbccddeeff31000000a52d844fdc48bc20012a2f9bb5c9b7d357fa2875d745a634b0429dc411235f0302030617111111316263687265673a7171676a7976367932346e38307a796534326175656830776c7571707a67336e6773683866637064663400f2052a01000000000000230000000000000000000000000000000000000000000000000000000000000000ffff7f204444042068115e5f060000006801000000000000000000000000000000000000000000000092f1996a7f608d2140452c8cde277869b86e22e1e213133c6d65e63d36d17551aa00',
         '01000000010000000000000000000000000000000000000000000000000000000000000000ffffffff020306ffffffff0300f2052a010000001976a914112233445566778899aabbccddeeff001122334488ac0000000000000000434104ffd03de44a6e11b9917f3a29f9443283d9871c9d743ef30d5eddcd37094b64d1b3d8090496b53256786bf5c82932ec23c3b74d9f05a6f95a8b5529352656664bac00000000000000002a6a2846b6d9d94d907d1ff3d08f006983b29563c7e4c975e0b904401b3fc6cc8cade5000000000000000000000000'),
]


def hx(b):
    return binascii.hexlify(b).decode('ascii')


def build_chain():
    from p2pool import networks as pnet
    net = pnet.nets[NET_NAME]
    share_type = p2pool_data.share_versions[SHARE_VERSION]
    tracker = p2pool_data.OkayTracker(net)

    block_target = bitcoin_data.FloatingInteger.from_target_upper_bound(
        0x1d00ffff * 2 ** (256 - 32) // 100000).target
    address = cash_addr.encode(net.PARENT.HUMAN_READABLE_PART, 0,
                               [(PUBKEY_HASH >> (8 * i)) & 0xff
                                for i in reversed(range(20))])

    parent = None
    out = []
    for height in range(1, CHAIN_LENGTH + 1):
        share_info, gentx, other_tx_hashes, get_share = \
            share_type.generate_transaction(
                tracker=tracker,
                share_data=dict(
                    previous_share_hash=parent,
                    coinbase=bytes(bytearray([3, height & 0xff])),
                    nonce=0x11111111 + height,   # fixed: no randomness
                    address=address,
                    subsidy=net.PARENT.SUBSIDY_FUNC(height),
                    donation=0,
                    stale_info=None,
                    desired_version=SHARE_VERSION,
                ),
                block_target=block_target,
                desired_timestamp=1600000000 + height * 60,
                desired_target=2 ** 224,
                ref_merkle_link=dict(branch=[], index=0),
                desired_other_transaction_hashes_and_fees=[],
                net=net,
            )
        packed_gentx = bitcoin_data.tx_id_type.pack(gentx)
        gentx_hash = bitcoin_data.hash256(packed_gentx)
        # BaseShare.__init__ recomputes merkle_root (from segwit txid_merkle_link
        # when segwit is on), so replicate that or we grind the wrong header.
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
        out.append((height, share, packed_gentx))
        tracker.add(share)
        parent = share.hash

    return net, tracker, out, parent


_BUILT = []


class ShareGenerationTest(unittest.TestCase):
    def setUp(self):
        # Built once and shared: trial does not run setUpClass here, and the
        # nonce grind is the slow part.
        if not _BUILT:
            _BUILT.append(build_chain())
        self.net, self.tracker, self.chain, self.head = _BUILT[0]

    def test_share_hashes_and_payloads_match_py2(self):
        self.assertEqual(len(self.chain), len(EXPECTED))
        for (height, share, packed_gentx), exp in zip(self.chain, EXPECTED):
            self.assertEqual(height, exp[0])
            self.assertEqual('%064x' % share.hash, exp[1])
            self.assertEqual(hx(share.share_type.pack(share.contents)), exp[2])
            self.assertEqual(hx(packed_gentx), exp[3])

    def test_share_pow_meets_target(self):
        for height, share, _ in self.chain:
            self.assertLessEqual(share.pow_hash, share.target)
            self.assertLessEqual(share.target, self.net.MAX_TARGET)

    def test_shares_chain(self):
        parent = None
        for height, share, _ in self.chain:
            self.assertEqual(share.previous_hash, parent)
            parent = share.hash
        self.assertEqual(self.tracker.get_height(self.head), CHAIN_LENGTH)

    def test_share_reload_roundtrip(self):
        # The golden gate cannot reach this: it exercises primitives with
        # str cashaddrs, never a share packed then re-unpacked. pack.VarStrType
        # encodes str to utf-8 but unpacks to bytes, so on reload the address
        # field is bytes -- under py2 that field was already str (str WAS
        # bytes) so the round-trip was invisible. Under py3 cash_addr's
        # ord(x) over the address raised TypeError, which the old blanket
        # `except Exception` in get_cashaddr_pubkey_hash masked as
        # "invalid addr", silently dropping every v34+ share from shares.0
        # and from peers. Pin the reload path that broke.
        for height, share, _ in self.chain:
            packed = p2pool_data.share_type.pack(share.as_share())
            reloaded = p2pool_data.load_share(
                p2pool_data.share_type.unpack(packed), self.net, None)
            self.assertEqual(reloaded.hash, share.hash,
                             'reload of height %s changed the hash' % height)
            # The hash is the real gate: it only matches if the payout
            # script decoded to the right 160-bit hash. The address field
            # legitimately changes type (str in memory, bytes after
            # VarStrType.unpack) -- that is py2-faithful, since py2's str
            # WAS bytes -- so compare it as text, not identity.
            self.assertEqual(reloaded.share_data['address'].decode('ascii')
                             if isinstance(reloaded.share_data['address'], bytes)
                             else reloaded.share_data['address'],
                             share.share_data['address'])

    def test_reloaded_share_check_does_not_mix_address_types(self):
        # Reload is not just a decode: generate_transaction sorts `amounts`
        # by address. Under py3 the reloaded share_data['address'] was bytes
        # while donation_script_to_address returns str, so sorted() raised
        # TypeError ('<' not supported between instances of 'str' and
        # 'bytes'). The original stage-5 soak never hit this because it ran
        # in-process with str addresses; only a restart against shares.0
        # mixed the types.
        #
        # We call generate_transaction directly (not check()) with the SAME
        # block_target the harness used to build each share. check() passes
        # self.header['bits'].target as block_target (py3 data.py:552, py2
        # data.py:533) which differs from the harness's fixed tiny regtest
        # block_target, so check() legitimately regenerates a different gentx
        # -- py2 never guaranteed that equality either. Direct
        # generate_transaction exercises the exact sort path that broke.
        from p2pool.util import cash_addr
        address = cash_addr.encode(
            self.net.PARENT.HUMAN_READABLE_PART, 0,
            [(PUBKEY_HASH >> (8 * i)) & 0xff for i in reversed(range(20))])
        block_target = bitcoin_data.FloatingInteger.from_target_upper_bound(
            0x1d00ffff * 2 ** (256 - 32) // 100000).target

        for height, share, _ in self.chain:
            packed = p2pool_data.share_type.pack(share.as_share())
            reloaded = p2pool_data.load_share(
                p2pool_data.share_type.unpack(packed), self.net, None)
            # Rebuild the same share_data the harness used, but with the
            # reloaded share's address (now bytes after VarStrType.unpack).
            share_data = dict(
                previous_share_hash=share.share_data['previous_share_hash'],
                coinbase=bytes(bytearray([3, height & 0xff])),
                nonce=0x11111111 + height,
                address=reloaded.share_data['address'],
                subsidy=self.net.PARENT.SUBSIDY_FUNC(height),
                donation=0,
                stale_info=None,
                desired_version=SHARE_VERSION,
            )
            # Before the address_to_str fix this raised TypeError from
            # sorted() inside generate_transaction: share_data['address']
            # was bytes while donation_script_to_address returns str.
            try:
                p2pool_data.share_versions[SHARE_VERSION].generate_transaction(
                    tracker=self.tracker,
                    share_data=share_data,
                    block_target=block_target,
                    desired_timestamp=1600000000 + height * 60,
                    desired_target=2 ** 224,
                    ref_merkle_link=dict(branch=[], index=0),
                    desired_other_transaction_hashes_and_fees=[],
                    net=self.net,
                )
            except TypeError:
                self.fail(
                    'height %s: generate_transaction raised TypeError '
                    '(mixed bytes/str address keys)' % (height,))
            # The payout address field legitimately stays bytes (re-serialized
            # from share_data). Normalizing it would change the share's packed
            # bytes and break the hash; the fix is at the read sites
            # (generate_transaction, BaseShare.address, get_user_stale_props).
            self.assertEqual(reloaded.address, share.address,
                             'height %s: reload altered the payout address'
                             % (height,))

    def test_subsidy_is_conserved(self):
        _, _, _, _ = None, None, None, None
        weights, total_weight, donation_weight = \
            self.tracker.get_cumulative_weights(
                self.head, CHAIN_LENGTH, 65535 * self.net.SPREAD)
        payouts = p2pool_data.get_expected_payouts(
            self.tracker, self.head,
            bitcoin_data.FloatingInteger.from_target_upper_bound(
                0x1d00ffff * 2 ** (256 - 32) // 100000).target,
            self.net.PARENT.SUBSIDY_FUNC(1), self.net)
        self.assertEqual(sum(payouts.values()),
                         self.net.PARENT.SUBSIDY_FUNC(1))

    def test_coinbase_scriptsig_is_always_consensus_valid(self):
        # BCHN consensus (src/consensus/tx_check.cpp:62-65) rejects any
        # coinbase whose scriptSig is not 2..100 bytes with 'bad-cb-length'.
        # On regtest GBT reports coinbaseaux.flags == '', so the assembled
        # scriptSig is just the BIP34 height push -- one byte for heights
        # 1..16 (OP_1..OP_16 == 0x51..0x60).  The old [:100] slice also cut
        # the middle of a push and could leave a 1-byte fragment.  Both
        # cases made BCHN reject every p2pool block.  Pin the padding.
        from p2pool.work import _pad_coinbase_script
        # 1-byte height push (e.g. 0x5b == OP_11 at height 11) is padded to 2.
        self.assertEqual(_pad_coinbase_script(b'\x5b'), b'\x5b\x00')
        # 0-byte input is padded to 2.
        self.assertEqual(_pad_coinbase_script(b''), b'\x00\x00')
        # 2..99 bytes pass through unchanged.
        self.assertEqual(_pad_coinbase_script(b'\x5b\x00'), b'\x5b\x00')
        self.assertEqual(_pad_coinbase_script(b'\x02\x01\x01'), b'\x02\x01\x01')
        # 100 bytes is the cap: the trailing byte is dropped so the result
        # stays parseable and still >= 2 bytes (never a lone length prefix).
        self.assertEqual(len(_pad_coinbase_script(b'\x00' * 100)), 99)
        self.assertEqual(_pad_coinbase_script(b'\x01' * 100), b'\x01' * 99)


if __name__ == '__main__':
    unittest.main()
