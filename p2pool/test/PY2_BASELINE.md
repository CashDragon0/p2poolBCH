# Phase 0 baseline — frozen from the Python 2 reference

Captured from **PyPy 2.7.18 (7.3.20) + Twisted 18.9.0 + mock 3.0.5** against
p2pool `16.0-334-g6603b79`, before any porting edits.

The py3 port is **not** expected to make every test pass. These five already
failed on py2; matching them is fidelity, not regression.

## Test suite after Phase 1: 68 run / 64 pass / 3 failures / 1 error

(Before Phase 1 it was 72 / 67 pass / 3 failures / 2 errors. Phase 1 removed
`test_header_hash_litecoin`, `test_btc_addresses`, `test_tbtc_addresses`,
`test_ltc_addresses`, `test_tltc_addresses` and added
`test_regtest_addresses`.)

| Test | Pre-existing failure |
|---|---|
| `bitcoin.test_data.IntegrationTests.test_bch_addresses` | `None != 'bitcoincash:qqqxkp...'` — see below |
| `bitcoin.test_data.UnitTests.test_pubkey_hash_to_address` | mock assertion mismatch: expected `encode('ltc',1,[...20 bytes])`, got `encode('ltc',1,[1,2,3,4,5])` — bad mock setup in the test |
| `bitcoin.test_data.Test.test_header_hash_litecoin` | needs `ltc_scrypt`, the py2 C extension Phase 1 deletes |
| `bitcoin.test_script.Test.test_all` | pre-existing script test failure |
| `test_data.Test.test_skiplist` | `'FakeShare' object has no attribute 'address'` — `WeightsSkipList.get_delta` (`data.py:673`) reads `.address`, the fake doesn't have it |

`test_node.py`, root `test_p2p.py`, `bitcoin/test_p2p.py` and
`util/test_skiplist.py` are **not** in this baseline: they need a live
bitcoind or are dead stubs. All are deleted by decision in Phase 1/8.

## `test_bch_addresses` — a real finding, not a port regression

`pubkey_hash_to_address` (`bitcoin/data.py:413`) formats the hash as:

```python
thash = '{:x}'.format(pubkey_hash)      # strips leading zeros
if len(thash) % 2 == 1:
    thash = '0%s' % thash
data = [int(x) for x in bytearray.fromhex(thash)]
```

For a `pubkey_hash` with a leading `0x00` byte (e.g.
`0x006b05f63239f7c5dd5c1f6cd2a00c4c358351b7`) this produces a **19-byte**
payload. `cash_addr.encode` accepts only `[20,24,28,32,40,48,56,64]` bytes
and returns `None` for anything else.

The zero-padding is supposed to be handled by:

```python
if hasattr(net, 'padding_bugfix') and net.padding_bugfix:
    thash = '{:040x}'.format(pubkey_hash)
```

`padding_bugfix` **used** to be set only when a share was loaded
(`net.PARENT.padding_bugfix = (share['type'] >= 35)` at `p2pool/data.py:54`),
which was dead code: every registered BCH share version is ≤ 34, so the
condition never held and the flag was never set.

The port now pins it **unconditionally True** at all three sites:

| Site | Why it must be unconditional |
|---|---|
| `p2pool/networks/bitcoincash_regtest.py:49` | Runs at import. A fresh regtest chain has no shares, so neither writer in `data.py` has run when the first `getwork` arrives — and the stage-5 mining address is exactly an odd-nibble hash, so every getwork died with `Odd-length string` and no share could be found past genesis. |
| `p2pool/data.py:63` (`load_share`) | Runs once per loaded share. |
| `p2pool/data.py:899` (`OkayTracker.think`) | Runs on **every** think(), so it would overwrite any network-level pin; it must be True itself. |

Verified live: with the pins in place the regtest node found 440 shares over
63.5 minutes with zero `Odd-length string` errors.

with a bare `'{:x}'` has **two** callers, and they do not fail the same way:

| Caller | Odd-length input | Why |
|---|---|---|
| `pubkey_hash_to_address` (`data.py:418`) | re-adds the zero nibble, never raises | has its own `len(thash) % 2 == 1` guard, so it emits a 20-byte (19-value-byte) payload instead |
| `pubkey_hash_to_script2` (`data.py:519`) | **raises** `binascii.Error "Odd-length string"` | no guard; passes the 39-char string straight to `unhexlify` |

`test_pubkey_hash_to_address` therefore never crashes — it fails with a
mock/return-value mismatch, because a 19-value-byte payload is rejected by
`cash_addr.encode`. The **crash** is the script2 side only, which no test
exercised at all until the `rtest_*` vectors were added below. That is the
whole reason stage 5 could mine into a chain tip the node then refused to
encode a payout script for.

The golden file captures **both** paths — `bch_p2pkh_N` (unpadded) and
`bch_p2pkh_pad_N` (padded) — so the gate pins whatever py3 does. If py3
"fixes" the unpadded case by accident, the gate will flag it, and that flag
is a deliberate decision to make rather than an accident.

Second, unrelated observation: `script2_to_address` (`data.py:542`) returns
the first non-exception result from its candidate list, but
`pubkey_hash_to_address` can return `None`. A `None` short-circuits the loop
instead of falling through to the next candidate or raising `ValueError`.
Left as-is — do not "improve" during the port.

## Golden-vector artifact

- `p2pool/test/golden_vectors.json` — 137,341 bytes
  sha256 `6afa3592f3ee7edc9fcd945c184ea1121002dc520091a0e547367b7c5bece7fc`
- 945 vectors: 901 successes + 44 intentional negative cases
- 11 sections: address, bech32, cashaddr, hash, math, merkle, net, pack,
  payout, script, template
- Re-frozen after Phase 1 (only the 3 BCH networks remain, so the `net`
  section shrank; the 3 `net.fastcoin/terracoin/terracoin_testnet`
  AttributeError negatives went away with them).
- Capture is deterministic — re-running under py2 reproduces it exactly.

Coverage of the consensus-critical paths:

- **pack** (454) — `IntType` 8/16/32/64/128/256 × little/big, `VarInt`,
  `VarStr`, `ListType`, `StructType`, `FixedStrType`, `IPV6AddressType`,
  `EnumType`, `PossiblyNoneType`; every case is a pack→unpack round trip
- **merkle** (78) — `merkle_hash`, `calculate_merkle_link`,
  `check_merkle_link` across 9 tree shapes, every index
- **cashaddr** (35) — encode/decode for payload sizes 20/24/28/32/40/48/56/64
  × versions 0/1. **The 40/48/56/64 sizes specifically exercise
  `cash_addr.py:171-172`**, the `tmp /= 2` / `dlen += (tmp - 160) / 32`
  integer-division code that silently breaks under py3 true division.
- **address** (160) — base58 + CashAddr + script2 round trips for BCH and
  BCHN testnet, both `padding_bugfix` states, plus vectors that **must**
  fail checksum validation. Includes the `rtest_*` block: 6 regtest hashes ×
  {padded, unpadded} × {script2, address}. Five hashes are 40 hex chars and
  encode identically either way, pinning the P2PKH script layout; the sixth is
  the stage-5 trigger (`0x0d9295309fb64af6c763b905a777f774a7c0b7da`, 39 hex
  chars under a bare `'{:x}'`), which makes `rtest_nopad_script2_5` a negative
  vector and `rtest_pad_script2_5` its positive counterpart.
- **payout** (17) — `WeightsSkipList` delta methods driven directly on a
  bare instance (`object.__new__`), pinning the nested floor division at
  `p2pool/data.py:687-688`:
  `(desired_weight - total_weight1)//65535*w//(total_weight2//65535)`
  and the 199/200 subsidy split at `data.py:260`
- **net** (135) — every chain constant for all networks including
  `IDENTIFIER`, `PREFIX`, `P2P_PREFIX`, `SUBSIDY_FUNC` at 8 heights
- **hash/script/math/bech32** (29/40/43/21) — `hash160`/`hash256`/`hash256d`,
  script disassembly and push-script building, base58 natural numbers
- **template** (8) — REAL Bitcoin Cash Node v29.2.0 `getblocktemplate`
  responses from a local regtest chain, frozen in
  `p2pool/test/fixtures/`. Pins the consensus-adjacent half of
  `bitcoin/helper.py::getwork`: txid derivation, tx unpack→repack round trips,
  compact-target decoding, `coinbaseaux` hex decoding, and fee accounting.

  Captured templates (regtest height 417 and 420): `bits=0x207fffff`,
  `version=0x20000000` (BCHN Nov-2019 top bits), 12-tx and empty mempools,
  `subsidy=1250002628` with `fee_sum=2628`. All four report
  `tx_repack_identical: true`.

### Note on types

`bitcoin/data.py::hash256`, `FloatingInteger.bits` and `.target` return
**integers**, not byte strings (`hash256` is `pack.IntType(256).unpack(...)`).
The capture encodes them as JSON integers. Getting this wrong silently
mis-typed them as `{"NOT_BYTES": "long"}`, so the encoder is type-checked on
purpose — a py3 port that starts returning `bytes` here will be caught.

### Deliberate negative cases (44)

These raise on py2 and **must** raise on py3 with the same exception type
name. Messages are excluded because py2/py3 wording legitimately differs.

`ValueError` (20) · `TypeError` (11) · `AssertionError` (7) ·
`AddrError` (4) · `error` (2)

Examples: empty/out-of-range merkle indices, `desired_weight % 65535 != 0`,
unknown enum values, the `none_value` sentinel, short-form IPv6 (`::`,
`2001:db8::1`), invalid scripts, bad base58 checksums, and the three
networks being deleted in Phase 1 (`fastcoin`, `terracoin`,
`terracoin_testnet`).

## Running the gate

```
./contrib/run-tests.sh --gate      # gate only
./contrib/run-tests.sh             # gate + full suite
```

`p2pool/test/golden_check.py` re-runs `p2pool/test/capture_vectors.py` under
the current interpreter as a subprocess and diffs. Verified in both
directions during Phase 0:

- under **py2**: `GATE PASS`, 1,009 compared, 0 mismatches, exit 0
- under **py3 on unported code**: `GATE FAIL`, exit 2, first SyntaxError

Per-section diff:

```
python p2pool/test/golden_check.py --section payout -v
```

## Local regtest harness

Two Bitcoin Cash Node v29.2.0 regtest daemons were used to capture the
templates. `getblocktemplate` requires a non-zero connection count and a
completed IBD, so a second node peers with the first:

```sh
BTCA=~/p2pool-port-work/bch-regtest
BTCB=~/p2pool-port-work/bch-regtest-b
bitcoin-cli -datadir=$BTCB -regtest -rpcport=28334 \
            -rpcuser=p2pooltest -rpcpassword=localtest123 \
            addnode 127.0.0.1:18444 onetry
```

Notes for whoever drives this next:

- `-regtest` must be passed on the **command line**; the `[regtest]` config
  section alone does not select the network.
- ZMQ must stay disabled — `zmqpubhashblock` aborts the node on shutdown with
  `CZMQAbstractPublishNotifier::Shutdown(): Assertion 'psocket' failed`.
- `getwork` does not exist in BCHN 29.x (`Method not found`), so
  `helper.getwork()` must use the GBT path.
- Regtest CashAddr prefix is **`bchreg:`**, not `bchtest:`, and the legacy
  address version is `0`.
- Subsidy halves every **150** blocks (50 → 25 at h150 → 12.5 at h300),
  matching `btcregtest.py`'s `50*100000000 >> (height + 1)//150`.

### Added networks

`p2pool/bitcoin/networks/bitcoincash_regtest.py` and
`p2pool/networks/bitcoincash_regtest.py` were added for this — the repo had
**no BCH regtest network at all**, and `btcregtest.py` is genuinely Bitcoin
(`SYMBOL='rBTC'`, legacy `segwit2x` softforks). Constants were read back from
the running node rather than guessed. These are new files, not ported ones.


## Phase 1 changes that affect the vectors

**`bitcoin/data.py` address coding now keys off a per-network flag.** The three
hardcoded `net.SYMBOL.lower() in ['bch','tbch','bsv','tbsv']` checks became
`getattr(net, 'USES_CASHADDR', False)`. Reason: regtest's symbol is `rBCH`,
which a hardcoded list silently misses — every regtest address would have
fallen through to the bech32 branch. All three BCH networks set
`USES_CASHADDR = True`.

Consequence: the `bchnet` stub in `p2pool/test/bitcoin/test_data.py` had to
gain `USES_CASHADDR = True` too, or two unit tests silently switched to the
bech32 branch and began failing.

**The `btcnet`/`ltcnet` stubs were kept** even though those coins are gone,
because `pubkey_hash_to_script2`'s bech32 and P2SH branches are still exercised
through them. Removing them would have deleted real coverage of `data.py`.

**`p2pool/data.py` BTC-only rejection rule removed.** The
`net.NAME == 'bitcoin' and absheight > 3927800` guard could never fire once
Bitcoin is gone, and it only ever *refused* shares, so removing it cannot widen
the set of accepted shares.

### Pre-existing bug found and fixed in this phase

`run_p2pool.py --help` **crashed** before this port. `--aux-getwork`'s help text
contains the literal `payout%http://...`, and argparse `%`-formats help text,
raising `ValueError: unsupported format character 't'`. Fixed by doubling the
`%`.

This was masked in CI precisely because the docker smoke test that exercised
`--help` ended in `|| true`. Docker is now removed, so there is no CI at all --
`./contrib/run-tests.sh --help` is the local equivalent and it now works.


## Phase 2: known gate divergences (3 of 945)

All three are **negative or behaviour-correction cases**, and each was
verified rather than assumed.

### `script.push_10` — py2 was broken; the port fixes it

`create_push_script` handled pushes of 76-255 bytes with a bare
`res.append(76)` (an int) into a list joined as a string. Verified against
real py2 (PyPy 2.7.18, commit cf438b6):

```
push( 75 bytes) -> 4baaaaaaaaaa...     (OK, OP_PUSHDATA)
push( 80 bytes) -> TypeError: sequence item 0: expected string, int found
push(100 bytes) -> TypeError: sequence item 0: expected string, int found
```

So the OP_PUSHDATA1 path was **entirely broken on py2 — it raised rather than
emitting a malformed script**. The intent was clearly 76 == 0x4c ==
OP_PUSHDATA1. Python 3 now emits the correct encoding (`4c64ff...`).

Why this is safe: the path *crashed*, so it never produced a malformed
coinbase. No historical block was built through it, so there is nothing to
diverge from. In practice p2pool's datums (32-byte hashes, the 32-byte
`[P2Pool]` witness value) all stay under 76 bytes, which is why it went
unnoticed.

**Decision: accept the divergence.** Documented rather than silenced.

### `pack.ipv6_4` / `ipv6_4_rt` — exception class differs, behaviour identical

Input `0000:0000:0000:0000:0000:ffff:192.168.1.1` has an odd-length hex
payload once the colons are stripped. py2's `str.decode('hex')` raised
`TypeError`; py3's `bytes.fromhex()` raises `ValueError`. Both reject the
input; only the exception class differs. This is a pure interpreter
difference in a negative test, not a port defect.
