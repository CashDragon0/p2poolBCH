# p2poolBCH

Decentralised pooled mining for Bitcoin Cash. Miners connect over Stratum;
p2pool builds a share chain and pays out per share, with no central server.

This tree runs on **Python 3.13 + Twisted 24**. It was ported from the
original PyPy2/Twisted 18 codebase.

---

## Requirements

- **Python 3.9+** (developed and tested on 3.13)
- **Twisted 21+** (tested on 24.3.0). Earlier Twisted is not supported:
  `get_page` uses `twisted.web.client.Agent`/`readBody`, which replaced
  `twisted.web.client.getPage` in Twisted 21.
- A running **Bitcoin Cash Node (BCHN)** with JSON-RPC enabled

Install the dependencies (pinned in `requirements.txt` — `Twisted>=21` is the
floor; the port is tested against 24.3.0):

```bash
python3 -m pip install -r requirements.txt
```

---

## Running

Start BCHN first (`server=1` and RPC credentials in `bitcoin.conf`), then:

```bash
python3 run_p2pool.py \
  --net bitcoincash \
  -a <your_BCH_payout_address> \
  --bitcoind-address 127.0.0.1 \
  --bitcoind-rpc-port 8332 \
  BITCOIND_RPCUSER BITCOIND_RPCPASSWORD \
  --p2pool-port 9349 \
  -w 9348
```

The RPC username and password are positional (space-separated, no flag name),
in that order. If you omit them, `run_p2pool.py` reads `rpcuser=` /
`rpcpassword=` from `~/.bitcoin/bitcoin.conf`. Use `--help` for the full flag
list.

Networks: `--net {bitcoincash,bitcoincash_regtest}`. For testnet, add
`--testnet` to `--net bitcoincash` (ports 19339/19338).

`-W ignore::SyntaxWarning` suppresses one pre-existing upstream `is 0.0`
warning in `work.py`; it is harmless and not a bug in this port.

---

## Ports

| Network | P2P port | Worker / stratum + web |
|---------|----------|-----------------------|
| Bitcoin Cash mainnet | 9349 | 9348 |
| BCHN testnet | 19339 | 19338 |
| BCHN regtest (local) | 19448 | 19447 |

Miners connect to the worker/stratum port; the web UI is served on the same
port. Forward the P2P port from your router for better peer connectivity.

**p2pool no longer does UPnP port-mapping.** The old `--disable-upnp` flag is
still accepted so existing systemd units keep working, but it does nothing and
prints a deprecation warning. Forward the ports manually.

If automatic public-address detection via p2p peers picks the wrong address,
set it explicitly with `--external-ip`.

---

## Miner setup

p2pool communicates with miners via the Stratum protocol:

- **URL:** `stratum+tcp://<node-ip-or-hostname>:9348`
- **Worker:** your BCH address
- **Password:** `x`

Mining to Legacy (P2PKH), SegWit/MultiSig (P2SH) and Bech32 addresses are
supported for the following coins with the specified address prefixes:

|Network		|P2PKH			|P2SH		|CashAddr prefix	|
|---------------|-----------------|-------|-------------------|
|Bitcoin Cash	|`1...`			|`3...`	|`bitcoincash:`	|
|BCH testnet	|`m...` / `n...`		|`2...`	|`bchtest:`		|
|BCH regtest	| (local only)		| —	|`bchreg:`		|

Bitcoin Cash uses CashAddr, not Bech32. The prefix is part of the address and
must match the network. Regtest addresses are produced by a local node only and
have no value on any public network.

To modify the mining difficulty, append a suffix to your mining address:
`address+4096` sets the pseudoshare difficulty to 4096, and
`address/65536` sets the actual share difficulty to 65536 (or the p2pool
minimum, whichever is higher). Pseudoshare difficulty only affects hashrate
statistics; actual share difficulty affects revenue variance and efficiency.

---

## Web UI

The web UI is served on the worker/stratum port. Open
`http://<node-ip>:9348/` in a browser. The **Miners** tab (`miners.html`)

*Ghost worker filtering* — Workers are hidden from the table when:
- They have never submitted a share on this connection (`last_diff == 0`), or
- Their last accepted share is older than 10 minutes and they are no longer TCP-connected.

This prevents stale entries from miners that are pointed at a higher-priority
backup pool.

*Idle-miner reconnect nudge* — If a worker is TCP-connected but has not
submitted a share for **15 minutes**, the pool automatically sends a
`client.reconnect` Stratum notification. The miner disconnects, reconnects,
and re-evaluates its pool-priority list — priority 1 (this node) wins and
shares resume. If the firmware does not support `client.reconnect`, the TCP
connection is dropped instead; the same reconnect cycle occurs. A 15-minute
cooldown prevents repeated nudges. The nudge count is visible in the Last
Submit column tooltip.

---

## Running the tests

```bash
python3 -m twisted.trial p2pool.test
```

The suite has 141 tests (138 pass, 3 documented skips that fail identically
under py2). The golden-vector gate compares against a captured py2 reference:

```bash
python3 -m p2pool.test.golden_check
```

It reports 3 known baseline mismatches (`pack.ipv6_4`, `pack.ipv6_4_rt`,
`script.push_10`) that are pre-existing.

Or run both through the wrapper, which builds a venv and exits non-zero if
either check fails:

```bash
./contrib/run-tests.sh
```
