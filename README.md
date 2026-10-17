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

On Debian/Ubuntu, install the packaged versions directly (no pip needed —
Debian 13 ships Twisted 24.11.0 and pyOpenSSL 25.0.0, both satisfying the pins):

```bash
sudo apt install python3-twisted python3-openssl python3-service-identity
```

Or with pip:

```bash
python3 -m pip install -r requirements.txt
```

---

## Running

Start BCHN first (`server=1` and RPC credentials in `bitcoin.conf`), then:

```bash
python3 run_p2pool.py --net bitcoincash -a <your_BCH_payout_address>
```

  *OPTIONAL*
```bash
  --bitcoind-address 127.0.0.1 \
  --bitcoind-rpc-port 8332 BITCOIND_RPCUSER BITCOIND_RPCPASSWORD \
  --p2pool-port 9349 -w 9348
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
- **Password:** `x` (optional)

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

### Pages

All pages are served under `/static/` on the same port as stratum:

- `/` or `/static/dashboard.html` — dashboard (landing page; `GET /` redirects here)
- `/static/miners.html` — per-miner table (hashrate, latency, DOA, payouts)
- `/static/stratum.html` — stratum stats and connection overview
- `/static/graphs.html` — historical graphs
- `/static/index.html` — legacy status page

### API

All API endpoints are unauthenticated JSON on the same port and send
`Access-Control-Allow-Origin: *`. Parameterized endpoints take the value as a
path segment, not a query string (e.g. `/miner_stats/<address>`).

| Endpoint | Returns |
|---|---|
| `/local_stats` | local hashrates, share counts, warnings |
| `/global_stats` | pool hashrate, share difficulty, efficiency |
| `/difficulty` | current share difficulty |
| `/rate` | current share submission rate |
| `/uptime` | node uptime (seconds) |
| `/fee` | pool fee proportion |
| `/users` | connected worker usernames |
| `/user_stales` | stale-share rates per user |
| `/stale_rates` | aggregate stale rates |
| `/node_info` | node address, ports, best share |
| `/v36_status` | share-chain protocol status |
| `/network_difficulty` | network difficulty |
| `/version_signaling` | client version signalling |
| `/recent_blocks` | recent blocks found |
| `/recent_merged_blocks` | recent merged-mined blocks |
| `/current_payouts` | current block payout outputs |
| `/current_merged_payouts` | current merged payout outputs |
| `/best_share` | best share data |
| `/luck_stats` | per-block luck calculation |
| `/stratum_stats` | per-worker hashrate, shares, latency |
| `/stratum_security` | share-rate and burst metrics |
| `/ban_stats` | ban counters |
| `/connected_miners` | payout addresses with active workers |
| `/pings` | per-peer ping times |
| `/peer_list` | connected p2pool peers |
| `/peer_addresses` | peer addresses, space-separated |
| `/peer_txpool_sizes` | remembered tx-pool size per peer |
| `/peer_versions` | peer sub-versions |
| `/broadcaster_status` | block broadcaster health and BCH network peers |
| `/miner_stats/<address>` | per-address stats and worker table |
| `/miner_payouts/<address>` | per-address payout history |
| `/merged_miner_payouts/<address>` | per-address merged payouts |
| `/web/version` | node version string |
| `/web/currency_info` | ticker symbols and prefixes |
| `/web/heads` | share-chain head hashes |
| `/web/tails` | share-chain tail hashes |
| `/web/verified_heads` | verified head hashes |
| `/web/verified_tails` | verified tail hashes |
| `/web/log` | recent log lines |
| `/web/share/<share_hash>` | single share by hash |
| `/web/payout_address/<share_hash>` | payout address for a share |

Peer endpoints (`/peer_list`, `/peer_addresses`, `/peer_txpool_sizes`,
`/pings`, `/peer_versions`, `/broadcaster_status`) expose connected peer IP
addresses in cleartext. Miner (stratum) IPs are never exposed; `/stratum_stats`
masks them (`x.x.x.NN`). These endpoints have no authentication — bind the
worker port to localhost or firewall it if you do not want them reachable.

---

## Running the tests

```bash
python3 -m twisted.trial p2pool.test
```

```bash
python3 -m p2pool.test.golden_check
```

Or run both through the wrapper, which builds a venv and exits non-zero if
either check fails:

```bash
./contrib/run-tests.sh
```
