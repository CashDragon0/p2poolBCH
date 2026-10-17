# Persistent found-block history for the web UI.
#
# The share chain only keeps CHAIN_LENGTH (~3 days) of shares in memory, so
# /recent_blocks and /luck_stats can only see that far back. This module
# appends every block found by the p2pool network (locally or by a peer) to a
# JSONL file in the datadir, so luck and graphs stay meaningful over a longer
# window (default 90 days).

import json
import os
import time

from . util import deferral
from .bitcoin import data as bitcoin_data
from . import data as p2pool_data


class BlockHistory(object):
    def __init__(self, path, retention_days=90):
        self.path = path
        self.retention_days = retention_days
        self.blocks = []  # list of dicts, oldest first
        self._known_shares = set()
        self._watchers = []
        self._load()

    def watch(self, callback):
        # callback(record) invoked on each newly added block
        self._watchers.append(callback)

    def _notify(self, record):
        for cb in self._watchers:
            try:
                cb(record)
            except Exception:
                pass

    def _load(self):
        self.blocks = []
        self._known_shares = set()
        try:
            with open(self.path, 'rb') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    sh = rec.get('share')
                    if sh and sh not in self._known_shares:
                        self._known_shares.add(sh)
                        self.blocks.append(rec)
        except IOError as e:
            if e.errno != 2:  # errno.ENOENT
                import traceback
                traceback.print_exc()
        self.blocks.sort(key=lambda b: b.get('ts', 0))
        self._compute_luck()
        self._prune()

    def add_block(self, record):
        sh = record.get('share')
        if not sh or sh in self._known_shares:
            return False
        self._known_shares.add(sh)
        self.blocks.append(record)
        self.blocks.sort(key=lambda b: b.get('ts', 0))
        self._compute_luck()
        self._prune()
        self._append_line(record)
        self._notify(record)
        return True

    def _append_line(self, record):
        try:
            d = os.path.dirname(self.path)
            if d and not os.path.isdir(d):
                os.makedirs(d)
            with open(self.path, 'ab') as f:
                f.write(json.dumps(record, sort_keys=True).encode('utf-8') + b'\n')
        except (IOError, OSError):
            pass

    def _compute_luck(self):
        # luck for a block needs its successor (the next block found).
        # 100 * expected_shares / actual_shares, where actual_shares is the
        # share-height gap between the two blocks. Retroactive: computed for
        # every adjacent pair in the store, not just newly found blocks.
        for i in range(len(self.blocks) - 1):
            cur, nxt = self.blocks[i], self.blocks[i + 1]
            exp = cur.get('expected_shares')
            if not exp or exp <= 0:
                cur['luck'] = None
                cur['luck_method'] = 'first_block'
                cur['time_to_find'] = None
                continue
            actual = (nxt.get('height') or 0) - (cur.get('height') or 0)
            if actual > 0:
                cur['luck'] = 100.0 * exp / actual
                cur['luck_method'] = 'shares'
                if cur.get('ts') is not None and nxt.get('ts') is not None:
                    cur['time_to_find'] = float(nxt['ts'] - cur['ts'])
                else:
                    cur['time_to_find'] = None
            else:
                cur['luck'] = None
                cur['luck_method'] = 'first_block'
                cur['time_to_find'] = None
        # newest block has no successor yet
        if self.blocks:
            last = self.blocks[-1]
            if last.get('luck_method') != 'shares':
                last['luck'] = None
                last['luck_method'] = 'first_block'
                last['time_to_find'] = None

    def _prune(self):
        if self.retention_days <= 0:
            return
        cutoff = time.time() - self.retention_days * 86400
        keep = [b for b in self.blocks if (b.get('ts') or 0) >= cutoff]
        if len(keep) != len(self.blocks):
            self.blocks = keep
            self._known_shares = set(b.get('share') for b in self.blocks)
            self._rewrite()

    def _rewrite(self):
        try:
            d = os.path.dirname(self.path)
            if d and not os.path.isdir(d):
                os.makedirs(d)
            tmp = self.path + '.new'
            with open(tmp, 'wb') as f:
                for b in self.blocks:
                    f.write(json.dumps(b, sort_keys=True).encode('utf-8') + b'\n')
                f.flush()
                try:
                    os.fsync(f.fileno())
                except (OSError, IOError):
                    pass
            try:
                os.rename(tmp, self.path)
            except OSError:
                if os.path.exists(self.path):
                    os.remove(self.path)
                os.rename(tmp, self.path)
        except (IOError, OSError):
            pass

    def get_recent_blocks(self):
        # newest first, matching the old /recent_blocks ordering
        return list(reversed(self.blocks))

    def get_luck_stats(self):
        if not self.blocks:
            return dict(luck_available=False, current_luck_trend=None, blocks=[])
        blocks_out = []
        for b in reversed(self.blocks):
            blocks_out.append(dict(ts=b.get('ts'), hash=b.get('hash'), luck=b.get('luck')))
        return dict(luck_available=True, current_luck_trend=None, blocks=blocks_out)


def make_block_record(share, tracker, net, pool_hash_rate=None):
    """Build a history record from a share that met the network block target.

    Called at detection time so difficulty/pool-rate are snapshotted and later
    tracker pruning can't destroy them.
    """
    try:
        height = tracker.get_height(share.hash)
    except Exception:
        height = None
    try:
        share_diff = bitcoin_data.target_to_difficulty(share.target)
    except Exception:
        share_diff = None
    try:
        network_diff = bitcoin_data.target_to_difficulty(share.header['bits'].target)
    except Exception:
        network_diff = None
    try:
        actual_hash_diff = bitcoin_data.target_to_difficulty(share.pow_hash) if share.pow_hash > 0 else None
    except Exception:
        actual_hash_diff = None
    try:
        miner = (bitcoin_data.script2_to_address(share.new_script, net.PARENT.ADDRESS_VERSION, -1, net.PARENT) or
                 bitcoin_data.script2_to_address(share.new_script, net.PARENT.ADDRESS_P2SH_VERSION, -1, net.PARENT) or '')
    except Exception:
        miner = ''
    try:
        number = p2pool_data.parse_bip0034(share.share_data['coinbase'])[0]
    except Exception:
        number = None

    # expected shares between blocks, computed from THIS block's difficulties.
    expected_shares = None
    try:
        attempts_per_share = bitcoin_data.target_to_average_attempts(share.target)
        attempts_per_block = bitcoin_data.target_to_average_attempts(share.header['bits'].target)
        if attempts_per_share > 0:
            expected_shares = float(attempts_per_block) / attempts_per_share
    except Exception:
        pass

    # expected_time using pool hashrate at this block (matches Miners Block Value formula)
    expected_time = None
    if pool_hash_rate and pool_hash_rate > 0:
        try:
            attempts_per_block_at_time = bitcoin_data.target_to_average_attempts(share.header['bits'].target)
            expected_time = float(attempts_per_block_at_time) / pool_hash_rate
        except Exception:
            pass

    return dict(
        ts=share.timestamp,
        height=height,
        hash='%064x' % share.header_hash,
        share='%064x' % share.hash,
        number=number,
        miner=miner,
        share_difficulty=share_diff,
        network_difficulty=network_diff,
        actual_hash_difficulty=actual_hash_diff,
        expected_shares=expected_shares,
        pool_hash_rate=pool_hash_rate,
        expected_time=expected_time,
        luck=None,
        luck_method='first_block',
        time_to_find=None,
        verified=True,
        status='confirmed',
    )
