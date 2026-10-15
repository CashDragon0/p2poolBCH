#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Phase 0 -- py2 golden-vector capture for p2poolBCH.

Emits a deterministic JSON artifact of every consensus-critical computation.
The py3 port must reproduce this file byte-for-byte.

Contract encoding (identical on both interpreters):
  binary  -> lowercase hex string
  integer -> JSON integer
  text    -> JSON string
  failure -> {"ok": false, "err": "<ExceptionTypeName>"}   <- type only.
             Messages deliberately EXCLUDED: py2/py3 wording differs and
             would cause spurious mismatches.  Messages go to stderr.

IMPORTANT INPUT NOTE: several helpers take ints or int-lists, NOT bytes:
  merkle_hash / calculate_merkle_link -> list[int]
  cash_addr.encode(prefix, ver, data) -> data is list[int]
  segwit_addr.encode(hrp, wv, prog)   -> prog is list[int]
  script.create_push_script(datums)   -> datums is a list
A wrong input type raises TypeError here on py2 exactly as it would on py3,
so these vectors also pin the call signatures.

Run:
  cd <repo> && PYTHONPATH=. pypy2 capture_vectors.py > golden.json
"""
from __future__ import print_function

import binascii
import json
import os
import sys

sys.path.insert(0, '.')

from p2pool.util import pack, math as p2math, cash_addr, segwit_addr
from p2pool.bitcoin import data as bdata, script as bscript
from p2pool.bitcoin.data import FloatingIntegerType
from p2pool import networks as p2net
from p2pool.bitcoin import networks as bnet
import p2pool.data as p2pdata

# Cross-interpreter text/binary type aliases. On py2 `bytes is str`; on py3
# they are distinct. The capture MUST classify identically on both.
try:
    _TEXT = unicode          # noqa: F821  (py2)
except NameError:
    _TEXT = str              # py3

V = {}
ERRS = []


def cap(fn, *a, **kw):
    """Capture a call. Never raises; failures are part of the contract."""
    try:
        return {'ok': True, 'v': fn(*a, **kw)}
    except Exception as e:
        ERRS.append('%s: %s' % (type(e).__name__, str(e)[:140]))
        return {'ok': False, 'err': type(e).__name__}


def H(b):
    """bytes -> hex. Type-checked so str/bytes divergence surfaces LOUD."""
    if isinstance(b, bytes):
        return b.hex()
    return {'NOT_BYTES': type(b).__name__}


def T(s):
    """Text, normalised across py2 str / py2 unicode / py3 str."""
    if isinstance(s, _TEXT):
        return s
    if isinstance(s, bytes):
        try:
            return s.decode('ascii')
        except Exception:
            return {'NON_ASCII': H(s)}
    return {'NOT_TEXT': type(s).__name__}


def IL(lst):
    """list of ints, normalised."""
    return [int(x) for x in lst]


# ---------------------------------------------------------------- hashes ----
V['hash'] = {}
V['hash']['ripemd_available'] = cap(
    lambda: H(__import__('hashlib').new('ripemd160', b'x').digest()))
V['hash']['ripemd_abc'] = cap(
    lambda: H(__import__('hashlib').new('ripemd160', b'abc').digest()))
for i, blob in enumerate([b'', b'\x00', b'\x01', b'\x80', b'\xff', b'abc',
                          b'\xde\xad\xbe\xef' * 8, b'\x00' * 32,
                          b'\xff' * 65]):
    V['hash']['hash256_%d' % i] = cap(lambda b=blob: H(bdata.hash256(b)))
    V['hash']['hash256d_%d' % i] = cap(lambda b=blob: H(bdata.hash256d(b)))
    V['hash']['hash160_%d' % i] = cap(lambda b=blob: H(bdata.hash160(b)))


# ----------------------------------------------------------------- pack ----
V['pack'] = {}


def _rt(label, typ, val, conv):
    def go():
        p = typ.pack(val)
        return {'packed': H(p), 'unpacked': conv(typ.unpack(p))}
    V['pack'][label] = cap(go)


for bits in (8, 16, 32, 64, 128, 256):
    for endian in ('little', 'big'):
        for val in (0, 1, 2, 127, 128, 255, 256, 65535, 65536,
                    2 ** 32 - 1, 2 ** 32, 2 ** 64 - 1, 2 ** 128, 2 ** 255):
            if val >= 2 ** bits:
                continue
            _rt('int%d_%s_%d' % (bits, endian, val),
                pack.IntType(bits, endian), val, int)

for n in list(range(0, 130)) + [200, 1000, 2 ** 16 - 1, 2 ** 20]:
    V['pack']['varint_%d' % n] = cap(lambda v=n: H(pack.VarIntType().pack(v)))
    V['pack']['varint_%d_rt' % n] = cap(
        lambda v=n: int(pack.VarIntType().unpack(pack.VarIntType().pack(v))))

for i, s in enumerate([b'', b'\x00', b'\x01\x02\x03', b'\xff' * 100,
                       b'p2pool', b'\x7f' * 252, b'\x00' * 3]):
    V['pack']['varstr_%d' % i] = cap(lambda x=s: H(pack.VarStrType().pack(x)))
    V['pack']['varstr_%d_rt' % i] = cap(
        lambda x=s: H(pack.VarStrType().unpack(pack.VarStrType().pack(x))))

for i, lst in enumerate([[], [0], [1, 2, 3], [2 ** 255], list(range(50))]):
    t = pack.ListType(pack.IntType(32))
    V['pack']['listint32_%d' % i] = cap(lambda x=lst: H(t.pack(x)))
    V['pack']['listint32_%d_rt' % i] = cap(lambda x=lst: IL(t.unpack(t.pack(x))))
    t2 = pack.ListType(pack.IntType(256))
    V['pack']['listint256_%d' % i] = cap(lambda x=lst: H(t2.pack(x)))
    V['pack']['listint256_%d_rt' % i] = cap(lambda x=lst: IL(t2.unpack(t2.pack(x))))

# NOTE: pack.py defines only IPV6AddressType -- there is no IPV4AddressType.
for i, ip in enumerate(['0000:0000:0000:0000:0000:0000:0000:0001',
                        '2001:0db8:0000:0000:0000:0000:0000:0001',
                        'fe80:0000:0000:0000:0202:b3ff:fe1e:8329',
                        'ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff',
                        '0000:0000:0000:0000:0000:ffff:192.168.1.1',
                        '::', '2001:db8::1']):
    t = pack.IPV6AddressType()
    V['pack']['ipv6_%d' % i] = cap(lambda a=ip: H(t.pack(a)))
    V['pack']['ipv6_%d_rt' % i] = cap(lambda a=ip: T(t.unpack(t.pack(a))))

for n in (1, 2, 3, 8, 12, 16, 20, 32):
    t = pack.FixedStrType(n)
    V['pack']['fixedstr_%d' % n] = cap(lambda k=n: H(t.pack(b'A' * k)))
    V['pack']['fixedstr_%d_rt' % n] = cap(lambda k=n: H(t.unpack(t.pack(b'A' * k))))

_e = pack.EnumType(pack.IntType(8), {0: None, 1: 'a', 253: 'orphan', 254: 'doa'})
for v in (0, 1, 2, 252, 253, 254, 255):
    V['pack']['enum8_%d' % v] = cap(lambda x=v: T(_e.pack(x)))

# PossiblyNoneType(none_value, inner) -- the sentinel is encoded as the inner
# value and reads back as None.
_pn = pack.PossiblyNoneType(0, pack.IntType(32))
for v in (0, 1, 2 ** 32 - 1, None):
    V['pack']['possnone_%s' % v] = cap(lambda x=v: H(_pn.pack(x)))
    V['pack']['possnone_%s_rt' % v] = cap(
        lambda x=v: (None if _pn.unpack(_pn.pack(x)) is None
                     else int(_pn.unpack(_pn.pack(x)))))


# --------------------------------------------------------------- merkle ----
# NOTE: takes list[int], NOT bytes.
V['merkle'] = {}


def _merkle_link(hashes, index):
    link = bdata.calculate_merkle_link(hashes, index)
    tip = bdata.merkle_hash(hashes)
    ok = bdata.check_merkle_link(tip, link)
    return {'branch': [H(b) for b in link['branch']],
            'index': int(link['index']),
            'tip': H(tip),
            'check_ok': bool(ok == tip)}


MERS = [
    [0],
    [1],
    [1, 2],
    [0, 1, 2],
    [0xdeadbeef, 0xfeedface, 0x12345678],
    [(i * 2654435761) % (2 ** 256) for i in range(7)],
    [(i * 0x9e3779b97f4a7c15) % (2 ** 256) for i in range(17)],
    [(i * 0x9e3779b97f4a7c15) % (2 ** 256) for i in range(40)],
    [2 ** 256 - 1, 0, 2 ** 255, 1],
]
for n in range(len(MERS)):
    for idx in range(len(MERS[n])):
        V['merkle']['%d_%d' % (n, idx)] = cap(
            lambda h=MERS[n], i=idx: _merkle_link(h, i))
V['merkle']['err_empty'] = cap(lambda: _merkle_link([], 0))
V['merkle']['err_oob'] = cap(lambda: _merkle_link([1, 2], 5))


# ------------------------------------------------------------- addresses ----
V['address'] = {}
BCH = bnet.nets['bitcoincash']
TBCH = bnet.nets['bitcoincash_testnet']

SEEDS = [0, 1, 0x7fff, 0x80, 0xff, 0x10000, 2 ** 32 - 1, 2 ** 64 - 1,
         0x0000000000000000000000000000000000000000000000000000000000000001,
         0xbd3e56e7ef4bcff2f3b8ef8d3f6e3f3f8d3f6e3f8d3f6e3f8d3f6e3f8d3f6e3f]

for name, net in (('bch', BCH), ('tbch', TBCH)):
    for i, s in enumerate(SEEDS):
        h = s % (2 ** 160)
        V['address']['%s_p2pkh_%d' % (name, i)] = cap(
            lambda x=h, n=net: T(bdata.pubkey_hash_to_address(
                x, n.ADDRESS_VERSION, -1, n)))
        V['address']['%s_script_%d' % (name, i)] = cap(
            lambda x=h, n=net: H(bdata.pubkey_hash_to_script2(
                x, n.ADDRESS_VERSION, -1, n)))
        # Production sets net.PARENT.padding_bugfix = (share['type'] >= 35) at
        # p2pool/data.py:54, which pads a leading-zero pubkey_hash to 20 bytes
        # via '{:040x}'. Without it '{:x}' strips the zero and yields a
        # 19-byte payload, which cash_addr.encode REJECTS -> None.
        # p2pool/test/bitcoin/test_data.py::test_bch_addresses fails on py2 for
        # exactly this reason. Capture BOTH paths so the gate pins them.
        _pb = getattr(net, 'padding_bugfix', False)
        setattr(net, 'padding_bugfix', True)
        V['address']['%s_p2pkh_pad_%d' % (name, i)] = cap(
            lambda x=h, n=net: T(bdata.pubkey_hash_to_address(
                x, n.ADDRESS_VERSION, -1, n)))
        setattr(net, 'padding_bugfix', _pb)
    V['address']['%s_addrver' % name] = cap(lambda n=net: int(n.ADDRESS_VERSION))
    V['address']['%s_symbol' % name] = cap(lambda n=net: T(n.SYMBOL))
    V['address']['%s_block_period' % name] = cap(lambda n=net: int(n.BLOCK_PERIOD))
    V['address']['%s_p2p_prefix' % name] = cap(lambda n=net: H(n.P2P_PREFIX))
    V['address']['%s_pow_is_hash256' % name] = cap(
        lambda n=net: bool(n.POW_FUNC is bdata.hash256))
    for hh in (0, 1, 100, 500000, 629999, 630000, 700000):
        V['address']['%s_subsidy_%d' % (name, hh)] = cap(
            lambda n=net, h=hh: int(n.SUBSIDY_FUNC(h)))

KNOWN = [
    ('bitcoincash', '1KUCp7YP5FP8ViRxhfszSUJCTAajK6viGy'),
    ('bitcoincash', '1BgGZ9tcN4rm9KBzDn7KprQz87SZ26SAMH'),
]
# Addresses that must FAIL base58 checksum validation -- a checksum regression
# here would silently produce wrong addresses, so the failure is part of the
# contract.
KNOWN_BAD = [
    ('bitcoincash', '1CUNqcjF6YFgsSLmgnAqr6xhhUM9BhG28a'),
    ('bitcoincash_testnet', 'mpXwg4jMtRhuSpVq4xS3HFHmCmWp2NNBhw'),
    ('bitcoincash_testnet', 'mkV13cKu7wS7wJVxZhSPVdxf7hLhq1gHpT'),
]

# Real addresses from the local regtest node. The CashAddr prefix there is
# 'bchreg:', which is why this list is separate from the testnet vectors.
KNOWN_REGTEST = [
    'bchreg:qzxpvrzvvfqwn9sd86ja5e9lewj865w0xylyzzrafm',
    'bchreg:qrlkjrk62k0avtka6335d54gc09nu8trdusafcwmkm',
    'bchreg:qzw0czqew4w2srkzyqfmx45safnppekvc57vxcpve9',
    'bchreg:qzzyzdatz8ak65quuqdh5ct28gzv2ynpzv67szfvp4',
    'bchreg:qz3pmyfseuz0tzn828qny44lzmpvk04npuuewk3ek4',
]
for netname, addr in KNOWN:
    n = bnet.nets[netname]
    V['address']['known_%s_%s' % (netname, addr[:8])] = cap(
        lambda a=addr, nn=n: int(bdata.address_to_pubkey_hash(a, nn)[0]))
    V['address']['known_%s_%s_script' % (netname, addr[:8])] = cap(
        lambda a=addr, nn=n: H(bdata.address_to_pubkey_hash(a, nn)[1]))

for addr in KNOWN_REGTEST:
    n = bnet.nets['bitcoincash_regtest']
    V['address']['known_regtest_%s' % addr[-8:]] = cap(
        lambda a=addr, nn=n: int(bdata.address_to_pubkey_hash(a, nn)[0]))
    V['address']['known_regtest_%s_script' % addr[-8:]] = cap(
        lambda a=addr, nn=n: H(bdata.address_to_pubkey_hash(a, nn)[1]))
    V['address']['known_regtest_%s_enc' % addr[-8:]] = cap(
        lambda a=addr, nn=n: (lambda d: [int(d[0]), IL(d[1])])(
            cash_addr.decode(nn.HUMAN_READABLE_PART, a)))

for netname, addr in KNOWN_BAD:
    n = bnet.nets[netname]
    V['address']['bad_%s_%s' % (netname, addr[:8])] = cap(
        lambda a=addr, nn=n: int(bdata.address_to_pubkey_hash(a, nn)[0]))

# ---------------------------- regtest cashaddr encode (padding_bugfix) ----
# pubkey_hash_to_script2 only reads net.padding_bugfix on the cashaddr branch,
# which requires addr_ver == -1 and bech32_version >= 0 (data.py:514). Every
# regtest vector above calls it with net.ADDRESS_VERSION (0), so the flag was
# never exercised for this network and the stage-5 "Odd-length string" crash
# had no golden coverage.
#
# Five of these hashes are even-length under '{:x}' and encode identically
# with or without padding -- they pin the P2PKH script layout. The sixth is
# the stage-5 mining address, whose hash formats to 39 hex chars: with the
# flag unset that is a negative vector (TypeError on py2, binascii.Error on
# py3 -- cap() records the type name only, so the two agree), and with the
# flag set it is a positive one. Pinning BOTH states is what makes the gate
# catch a writer that silently stops setting the flag.
#
# The flag is set and restored explicitly around each capture. A bare
# setattr/restore pair cannot express the unpadded state, because upstream
# never sets the attribute at all and the encoder's guard is
# hasattr(net, 'padding_bugfix') and net.padding_bugfix -- so 'unpadded'
# means the attribute is ABSENT, not False. Restoring via
# getattr(net, 'padding_bugfix', None) would leave padding_bugfix = None
# behind, which hasattr treats as present-but-falsy (still unpadded) but
# would diverge from the import-time state every later vector sees.
#
# This block is mirrored into the py2ref harness at
# ~/p2pool-port-work/py2ref/p2pool/test/capture_vectors.py -- same hashes,
# same delattr/setattr pairing. Only the comments differ there. A vector set
# that exists on one interpreter only would show up as <MISSING> in the gate
# and quietly stop being a parity check.
RTEST = bnet.nets['bitcoincash_regtest']
REGTEST_CASHADDR_HASHES = [
    0x8c160c4c6240e9960d3ea5da64bfcba47d51cf31,
    0xff690eda559fd62eddd46346d2a8c3cb3e1d636f,
    0x9cfc0819755ca80ec22013b35690ea6610e6ccc5,
    0x844137ab11fb6d501ce01b7a616a3a04c5126113,
    0xa21d9130cf04f58a6751c13256bf16c2cb3eb30f,
    0x0d9295309fb64af6c763b905a777f774a7c0b7da,  # stage-5 trigger: 39 hex chars
]
for i, h in enumerate(REGTEST_CASHADDR_HASHES):
    # Unpadded (flag absent, as shipped).
    if hasattr(RTEST, 'padding_bugfix'):
        delattr(RTEST, 'padding_bugfix')
    V['address']['rtest_nopad_script2_%d' % i] = cap(
        lambda x=h, n=RTEST: H(bdata.pubkey_hash_to_script2(x, -1, 0, n)))
    V['address']['rtest_nopad_addr_%d' % i] = cap(
        lambda x=h, n=RTEST: T(bdata.pubkey_hash_to_address(x, -1, 0, n)))
    # Padded (the state the fix pins).
    RTEST.padding_bugfix = True
    V['address']['rtest_pad_script2_%d' % i] = cap(
        lambda x=h, n=RTEST: H(bdata.pubkey_hash_to_script2(x, -1, 0, n)))
    V['address']['rtest_pad_addr_%d' % i] = cap(
        lambda x=h, n=RTEST: T(bdata.pubkey_hash_to_address(x, -1, 0, n)))
    delattr(RTEST, 'padding_bugfix')

for name, net in (('bch', BCH), ('tbch', TBCH)):
    for i, s in enumerate(SEEDS[:6]):
        h = s % (2 ** 160)
        V['address']['%s_rt_%d' % (name, i)] = cap(
            lambda x=h, n=net: T(bdata.script2_to_pubkey_hash_address(
                bdata.pubkey_hash_to_script2(x, n.ADDRESS_VERSION, -1, n),
                n.ADDRESS_VERSION, -1, n)))

# script2 -> address for common script shapes (script2_to_address is the
# public entry point; there is no script2_to_pubkey_hash).
S2 = [b'', b'\x76\xa9\x14' + b'\x11' * 20 + b'\x88\xac',
      b'\xa9\x14' + b'\x22' * 20 + b'\x87',
      b'\x00\x14' + b'\x33' * 20,
      b'\x76\xa9\x14' + b'\x44' * 20 + b'\x88\xac',
      b'\xa9\x14' + b'\x55' * 20 + b'\x87']
for i, s in enumerate(S2):
    V['address']['s2addr_%d' % i] = cap(
        lambda x=s: T(bdata.script2_to_address(x, BCH.ADDRESS_VERSION, -1, BCH)))
    V['address']['s2addr_tbch_%d' % i] = cap(
        lambda x=s: T(bdata.script2_to_address(
            x, TBCH.ADDRESS_VERSION, -1, TBCH)))
    V['address']['s2pkh_addr_%d' % i] = cap(
        lambda x=s: H(bdata.script2_to_pubkey_hash_address(
            x, BCH.ADDRESS_VERSION, -1, BCH).encode('ascii')))


# -------------------------------------------------------------- cashaddr ----
# NOTE: `data` is list[int]. Payloads of 40/48/56/64 bytes exercise the
# `tmp /= 2` + `dlen += (tmp-160)/32` branch at cash_addr.py:171-172 --
# the exact integer-division code that silently breaks on py3.
V['cashaddr'] = {}
CA_SIZES = [20, 24, 28, 32, 40, 48, 56, 64]
for sz in CA_SIZES:
    for vi in (0, 1):
        payload = [(i * 7 + 1) % 256 for i in range(sz)]
        V['cashaddr']['sz%d_v%d' % (sz, vi)] = cap(
            lambda p=payload, v=vi: T(
                cash_addr.encode('bitcoincash', v, p)))
        V['cashaddr']['sz%d_v%d_rt' % (sz, vi)] = cap(
            lambda p=payload, v=vi: (lambda d: [int(d[0]), IL(d[1])])(
                cash_addr.decode('bitcoincash',
                                 cash_addr.encode('bitcoincash', v, p))))

CA_ENCODED = [
    ('bchreg', 'qzxpvrzvvfqwn9sd86ja5e9lewj865w0xylyzzrafm'),
    ('bitcoincash', 'qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a'),
    ('bitcoincash', 'qr95sy3j9xwd2ap32xkykttr4czcuwsrpp3mhsnf3p'),
    ('bitcoincash', 'qqq3728yw0y47sqn6l2na30mcw6zm78dzqre909m2r'),
    ('bchtest', 'qpm2qsznhks23z7629mms6s4cwef74vcwvyl3vzm3c'),
]
for i, (pfx, a) in enumerate(CA_ENCODED):
    V['cashaddr']['dec_%d' % i] = cap(
        lambda p=pfx, x=a: (lambda d: [int(d[0]), IL(d[1])])(
            cash_addr.decode(p, x)))
V['cashaddr']['dec_bad'] = cap(lambda: cash_addr.decode('bitcoincash', 'nonsense'))


# --------------------------------------------------------------- bech32 ----
# NOTE: witprog is list[int].
V['bech32'] = {}
for sz in (20, 32, 40, 62):
    for wv in (0, 1, 5):
        prog = [(i * 11 + 3) % 256 for i in range(sz)]
        V['bech32']['sz%d_v%d' % (sz, wv)] = cap(
            lambda p=prog, v=wv: T(segwit_addr.encode('bc', v, p)))
        V['bech32']['sz%d_v%d_rt' % (sz, wv)] = cap(
            lambda p=prog, v=wv: (lambda d: [int(d[0]), IL(d[1])])(
                segwit_addr.decode('bc', segwit_addr.encode('bc', v, p))))
B32_ENC = ['bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kygt080']
for i, a in enumerate(B32_ENC):
    V['bech32']['dec_%d' % i] = cap(
        lambda x=a: (lambda d: [int(d[0]), IL(d[1])])(
            segwit_addr.decode('bc', x)))
V['bech32']['dec_bad'] = cap(lambda: segwit_addr.decode('bc', 'nonsense'))


# ----------------------------------------------------------------- math ----
V['math'] = {}
B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
for n in (0, 1, 2, 127, 128, 255, 256, 65535, 2 ** 32, 2 ** 64, 2 ** 128,
          2 ** 255):
    V['math']['natural_%d' % n] = cap(lambda x=n: H(p2math.natural_to_string(x)))
    V['math']['natural_%d_rt' % n] = cap(
        lambda x=n: int(p2math.string_to_natural(p2math.natural_to_string(x))))
for i, c in enumerate('abcxyz0189AZ'):
    V['math']['b58_%d' % i] = cap(
        lambda x=c: H(p2math.natural_to_string(ord(x), B58).encode('ascii')))
V['math']['clip_hi'] = cap(lambda: p2math.clip(15, (0, 10)))
V['math']['clip_lo'] = cap(lambda: p2math.clip(-5, (0, 10)))
V['math']['clip_mid'] = cap(lambda: p2math.clip(5, (0, 10)))
V['math']['add_to_range'] = cap(lambda: IL(p2math.add_to_range(3, (0, 10))))
V['math']['binomial_ci'] = cap(
    lambda: [float(x) for x in p2math.binomial_conf_interval(10, 100, 0.05)])
V['math']['convertbits_8_5'] = cap(
    lambda: IL(p2math.convertbits([1, 2, 3, 4, 5], 8, 5)))
V['math']['convertbits_5_8'] = cap(
    lambda: IL(p2math.convertbits([0, 1, 2, 3, 4], 5, 8)))


# ---------------------------------------------------------------- script ----
V['script'] = {}
# NOTE: parse() is a generator; must be list()'d. create_push_script takes a LIST.
SCRIPTS = [
    b'', b'\x51', b'\x52', b'\x53', b'\x6a',
    b'\x76\xa9\x14' + b'\x11' * 20 + b'\x88\xac',
    b'\xa9\x14' + b'\x22' * 20 + b'\x87',
    b'\x00\x14' + b'\x33' * 20,
    b'\x88\xac', b'\x00' * 32, b'\x51\x52\x53',
    b'\x6a\x24\xaa\x21\xa9\xed' + b'\x00' * 32,
    b'\x04' + b'\x66' * 32 + b'\x65' * 32 + b'\x01',
    b'\x4c' + b'\xaa' * 80,
]
for i, s in enumerate(SCRIPTS):
    # parse() yields (opcode_name, opcode_arg) TUPLES; opcode_arg is often
    # bytes (a pushed datum) and sometimes None.
    def _enc(a):
        if a is None:
            return None
        if isinstance(a, tuple):        # (value, file_state) shape
            return {'TUPLE': [_enc(a[0])]}
        if isinstance(a, bytes):
            return H(a)
        return int(a)

    def _dis(x):
        return [[T(nm), _enc(ag)] for nm, ag in bscript.parse(x)]
    V['script']['disasm_%d' % i] = cap(lambda x=s: _dis(x))
    V['script']['sigops_%d' % i] = cap(lambda x=s: int(bscript.get_sigop_count(x)))

PUSHS = [[0], [1], [255], [256], [65535], [65536], [-1], [-128], [2 ** 31],
         [b'\x01\x02'], [b'\xff' * 100], [1, 2, 3], []]
for i, d in enumerate(PUSHS):
    V['script']['push_%d' % i] = cap(lambda x=d: H(bscript.create_push_script(x)))


# ------------------------------------------------------------ net consts ----
V['net'] = {}
for nname in sorted(p2net.nets.keys()):
    n = p2net.nets[nname]
    V['net'][nname] = cap(lambda x=n: {
        'CHAIN_LENGTH': int(x.CHAIN_LENGTH),
        'REAL_CHAIN_LENGTH': int(getattr(x, 'REAL_CHAIN_LENGTH', 0)),
        'SHARE_PERIOD': int(x.SHARE_PERIOD),
        'TARGET_LOOKBEHIND': int(x.TARGET_LOOKBEHIND),
        'SPREAD': int(x.SPREAD),
        'MIN_TARGET': int(x.MIN_TARGET),
        'MAX_TARGET': int(x.MAX_TARGET),
        'IDENTIFIER': H(x.IDENTIFIER),
        'PREFIX': H(x.PREFIX),
        'P2P_PORT': int(x.P2P_PORT),
        'WORKER_PORT': int(getattr(x, 'WORKER_PORT', 0)),
        'SOFTFORKS_REQUIRED': sorted([T(s) for s in x.SOFTFORKS_REQUIRED]),
        'MINIMUM_PROTOCOL_VERSION': int(getattr(x, 'MINIMUM_PROTOCOL_VERSION', 0)),
        'SEGWIT': int(getattr(x, 'SEGWIT_ACTIVATION_VERSION', 0) or 0),
        'BLOCK_MAX_SIZE': int(getattr(x, 'BLOCK_MAX_SIZE', 0)),
        'BLOCK_MAX_WEIGHT': int(getattr(x, 'BLOCK_MAX_WEIGHT', 0)),
    })

for nname in sorted(bnet.nets.keys()):
    n = bnet.nets[nname]
    V['net']['parent_' + nname] = cap(lambda x=n: {
        'SYMBOL': T(x.SYMBOL),
        'ADDRESS_VERSION': int(x.ADDRESS_VERSION),
        'P2P_PORT': int(x.P2P_PORT),
        'RPC_PORT': int(getattr(x, 'RPC_PORT', 0)),
        'BLOCK_PERIOD': int(x.BLOCK_PERIOD),
        'P2P_PREFIX': H(x.P2P_PREFIX),
        'DUST_THRESHOLD': float(x.DUST_THRESHOLD),
        'DUMB_SCRYPT_DIFF': int(x.DUMB_SCRYPT_DIFF),
        'SANE_TARGET_RANGE': [int(x.SANE_TARGET_RANGE[0]),
                              int(x.SANE_TARGET_RANGE[1])],
    })
    for hh in (0, 1, 100, 150, 200, 500000, 629999, 630000):
        V['net']['parent_%s_subsidy_%d' % (nname, hh)] = cap(
            lambda x=n, h=hh: int(x.SUBSIDY_FUNC(h)))


# ---------------------------------------------------------------- payout ----
# WeightsSkipList's delta methods are PURE (no self use), so we can drive them
# on a bare instance without a Tracker. This directly pins the consensus-
# critical payout arithmetic at p2pool/data.py:684-699 -- specifically the
# nested floor division
#     (desired_weight - total_weight1)//65535*w//(total_weight2//65535)
# which must NOT change in the port.
V['payout'] = {}
WSL = object.__new__(p2pdata.WeightsSkipList)

_DELTA_A = (1, {'addrA': 65535 * 10}, 65535 * 10, 0)
_DELTA_B = (1, {'addrB': 65535 * 5}, 65535 * 5, 65535 * 1)

V['payout']['combine_simple'] = cap(
    lambda: (lambda r: [int(r[0]), sorted(
        [[T(k), int(v)] for k, v in r[1].items()]),
        int(r[2]), int(r[3])])(WSL.combine_deltas(_DELTA_A, _DELTA_B)))
V['payout']['combine_same_addr'] = cap(
    lambda: (lambda r: [int(r[0]), sorted(
        [[T(k), int(v)] for k, v in r[1].items()]),
        int(r[2]), int(r[3])])(
        WSL.combine_deltas(_DELTA_A, _DELTA_A)))

# initial_solution asserts desired_weight % 65535 == 0
for dw in (65535, 65535 * 2, 65535 * 10, 65535 * 1000):
    V['payout']['init_%d' % dw] = cap(
        lambda w=dw: (lambda r: [int(r[0]),
                                 None if r[1] is None else int(r[1]),
                                 int(r[2]), int(r[3])])(
            WSL.initial_solution(0, (10, w))))
V['payout']['init_bad_mod'] = cap(lambda: WSL.initial_solution(0, (10, 65536)))

# apply_delta: the interesting branch is total_weight1+total_weight2 > desired
SOL = (0, ({}, ), 0, 0)


def _ap(delta1, delta2, max_shares, desired_weight):
    r = WSL.apply_delta(delta1, delta2, (max_shares, desired_weight))
    wl, new = r[1]

    def f(d):
        return sorted([[T(k), int(v)] for k, v in d.items()])
    return [int(r[0]), f(wl) if isinstance(wl, dict) else 'LINKED',
            f(new) if isinstance(new, dict) else 'LINKED',
            int(r[2]), int(r[3])]


D1 = (1, {'a': 65535 * 10}, 65535 * 10, 0)
D2 = (1, {'b': 65535 * 6}, 65535 * 6, 65535 * 2)
V['payout']['apply_under'] = cap(lambda: _ap(D1, D2, 10, 65535 * 100))
# exceeds desired_weight and share_count2 == 1 -> the nested-// branch
V['payout']['apply_over'] = cap(lambda: _ap(D1, D2, 10, 65535 * 12))
V['payout']['apply_exact'] = cap(lambda: _ap(D1, D2, 10, 65535 * 16))
D3 = (3, {'c': 65535 * 9}, 65535 * 9, 0)
V['payout']['apply_over_multicount'] = cap(lambda: _ap(D1, D3, 10, 65535 * 12))
V['payout']['apply_over_mod'] = cap(lambda: _ap(D1, D2, 10, 65535 * 13))

# judge
for sc, tw, mx, dw in ((5, 100, 10, 1000), (11, 100, 10, 1000),
                       (10, 100, 10, 1000), (9, 1000, 10, 1000)):
    V['payout']['judge_%d_%d' % (sc, tw)] = cap(
        lambda a=sc, b=tw, c=mx, d=dw: int(
            WSL.judge((a, ({},), b, 0), (c, d))))

# Pure integer-division invariants the port must preserve exactly
V['payout']['floor_div'] = cap(lambda: [
    int(7 // 2), int(-7 // 2), int(7 // -2), int(2 ** 256 // 3),
    int(199 * 65535 // 200), int(65535 * 12 // 65535),
    int((65535 * 16 - 65535 * 10) // 65535 * 12345 // (65535 * 6 // 65535)),
])
V['payout']['subsidy_split'] = cap(lambda: [
    int(5000000000 * (199 * 65535) // (200 * 65535)),
    int(5000000000 // 200),
])
V['payout']['math_helpers'] = cap(lambda: [
    sorted([[T(k), int(v)] for k, v in p2math.add_dicts(
        {'x': 1, 'y': 2}, {'y': 5, 'z': 9}).items()]),
    # flatten_linked_list is a GENERATOR (single-use) over (next, cur) pairs.
    sorted([[T(k), int(v)] for k, v in list(
        p2math.flatten_linked_list((None, {'a': 1})))[0].items()]),
    sorted([[T(k), int(v)] for k, v in list(
        p2math.flatten_linked_list(((None, {'a': 1}), {'b': 2})))[0].items()]),
])


# ---------------------------------------------------------- block template --
# Real Bitcoin Cash Node (v29.2.0) getblocktemplate responses, captured from a
# local regtest chain and frozen under p2pool/test/fixtures/. These pin the
# CONSENSUS-ADJACENT half of p2pool/bitcoin/helper.py::getwork: txid
# derivation, tx unpack/repack round trips, compact-target decoding, and
# coinbaseflags/coinbaseaux hex decoding. A divergence here means the py3 node
# builds a different coinbase commitment than the py2 node did.
V['template'] = {}
_FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    'fixtures')

if os.path.isdir(_FIX):
    for _fn in sorted(os.listdir(_FIX)):
        if not _fn.endswith('.json'):
            continue
        _key = _fn[:-5]

        def _parse_template(_path=os.path.join(_FIX, _fn)):
            with open(_path) as _f:
                work = json.load(_f)
            txids, unpacked, repacked, fees = [], [], [], []
            for x in work['transactions']:
                # getwork accepts either a bare hex string or {data, fee}
                if isinstance(x, dict):
                    raw, fee = x['data'], x.get('fee', None)
                else:
                    raw, fee = x, None
                packed = binascii.unhexlify(raw)
                txids.append(int(bdata.hash256(packed)))
                u = bdata.tx_type.unpack(packed)
                unpacked.append(H(bdata.tx_type.pack(u)))
                repacked.append(H(packed))
                fees.append(None if fee is None else int(fee))
            bits = work['bits']
            return {
                'height': int(work['height']),
                'version': int(work['version']),
                'previous_block': H(int(work['previousblockhash'], 16)),
                'bits': int(FloatingIntegerType().unpack(
                    binascii.unhexlify(bits)[::-1]).bits),
                'bits_int': int(FloatingIntegerType().unpack(
                    binascii.unhexlify(bits)[::-1]).bits),
                'target': int(bdata.FloatingIntegerType().unpack(
                    binascii.unhexlify(bits)[::-1]).target),
                'subsidy': int(work['coinbasevalue']),
                'sigoplimit': int(work.get('sigoplimit', 0)),
                'sizelimit': int(work.get('sizelimit', 0)),
                'capabilities': sorted([T(c) for c in work.get('capabilities', [])]),
                'rules': sorted([T(c) for c in work.get('rules', [])]),
                'mutable': sorted([T(c) for c in work.get('mutable', [])]),
                'n_tx': len(work['transactions']),
                'txids': txids,
                'tx_repack_identical': unpacked == repacked,
                'fees': fees,
                'fee_sum': int(sum(f for f in fees if f is not None)),
                # getwork: coinbaseflags = hex-decoded coinbaseaux values joined
                'coinbaseaux': sorted([[T(k), H(binascii.unhexlify(v))]
                                       for k, v in
                                       work.get('coinbaseaux', {}).items()
                                       if v]),
            }

        V['template'][_key] = cap(_parse_template)

        # Also exercise the RPC_CHECK genesis predicate used by every network.
        def _rpc_check(_path=os.path.join(_FIX, _fn)):
            with open(_path) as _f:
                work = json.load(_f)
            return T(work.get('previousblockhash'))
        V['template'][_key + '_prevhash'] = cap(_rpc_check)


# ----------------------------------------------------------------- emit ----
import p2pool


def _repo_version():
    """p2pool.__version__ uses dirname(argv[0]) as its git cwd, which is the
    script's dir -- not a repo. Resolve it explicitly so the value is stable."""
    import subprocess
    for d in ('.', os.path.dirname(os.path.abspath('.'))):
        try:
            return subprocess.check_output(
                ['git', 'describe', '--always', '--dirty'], cwd=d).strip()
        except Exception:
            continue
    return p2pool.__version__


out = {
    'meta': {
        'p2pool_version': T(_repo_version()),
        'python': sys.version.split()[0],
        'sections': sorted(V.keys()),
    },
    'vectors': V,
}

# Usage: capture_vectors.py [OUT.json]     (default: golden.json)
# Writes deterministic JSON to stdout-friendly file; stderr gets a summary.
_OUT = sys.argv[1] if len(sys.argv) > 1 else 'golden.json'
with open(_OUT, 'w') as f:
    f.write(json.dumps(out, indent=1, sort_keys=True))
    f.write('\n')

n_ok = sum(1 for s in V.values() for v in s.values() if v.get('ok'))
n_err = sum(1 for s in V.values() for v in s.values() if not v.get('ok'))
sys.stderr.write('WROTE %s  ok=%d err=%d sections=%d\n'
                 % (_OUT, n_ok, n_err, len(V)))
