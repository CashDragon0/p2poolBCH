"""Unit tests for the persistent block-history store (p2pool/block_history.py).

Covers the parts the golden gate can't reach: JSONL persistence, dedupe,
retroactive luck across adjacent blocks, and retention pruning.
"""
import os
import shutil
import tempfile
import time
import unittest

from p2pool import block_history

# Timestamps are relative to "now" because BlockHistory._prune drops any record
# older than the retention window; the luck assertions only care about deltas.
NOW = time.time()


def _rec(ts, height, share, expected_shares=None):
    return dict(
        ts=NOW + ts, height=height, hash='h' * 64, share=share, number=height,
        miner='addr', share_difficulty=1.0, network_difficulty=2.0,
        actual_hash_difficulty=3.0, expected_shares=expected_shares,
        pool_hash_rate=None, expected_time=None,
        luck=None, luck_method='first_block', time_to_find=None,
        verified=True, status='confirmed',
    )


class BlockHistoryTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, 'block_history.jsonl')

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_add_and_get_newest_first(self):
        bh = block_history.BlockHistory(self.path, retention_days=90)
        bh.add_block(_rec(100, 1, 'a', expected_shares=3.0))
        bh.add_block(_rec(200, 2, 'b', expected_shares=3.0))
        got = bh.get_recent_blocks()
        self.assertEqual([b['share'] for b in got], ['b', 'a'])  # newest first

    def test_dedupe_by_share(self):
        bh = block_history.BlockHistory(self.path, retention_days=90)
        self.assertTrue(bh.add_block(_rec(100, 1, 'a')))
        self.assertFalse(bh.add_block(_rec(100, 1, 'a')))  # duplicate share
        self.assertEqual(len(bh.blocks), 1)

    def test_retro_luck_computed_for_adjacent(self):
        bh = block_history.BlockHistory(self.path, retention_days=90)
        # block a expects 3 shares; b is 6 shares later -> luck 50%
        bh.add_block(_rec(100, 10, 'a', expected_shares=3.0))
        bh.add_block(_rec(200, 16, 'b', expected_shares=3.0))
        a = [b for b in bh.blocks if b['share'] == 'a'][0]
        self.assertIsNotNone(a['luck'])
        self.assertAlmostEqual(a['luck'], 50.0)
        self.assertEqual(a['luck_method'], 'shares')
        self.assertAlmostEqual(a['time_to_find'], 100.0)
        # newest block has no successor -> no luck
        b = [b for b in bh.blocks if b['share'] == 'b'][0]
        self.assertIsNone(b['luck'])
        self.assertEqual(b['luck_method'], 'first_block')

    def test_retro_luck_on_load(self):
        bh = block_history.BlockHistory(self.path, retention_days=90)
        bh.add_block(_rec(100, 10, 'a', expected_shares=3.0))
        bh.add_block(_rec(200, 16, 'b', expected_shares=3.0))
        # reload from disk; luck should be recomputed, not lost
        bh2 = block_history.BlockHistory(self.path, retention_days=90)
        a = [b for b in bh2.blocks if b['share'] == 'a'][0]
        self.assertAlmostEqual(a['luck'], 50.0)

    def test_retention_prunes_old(self):
        bh = block_history.BlockHistory(self.path, retention_days=1)
        bh.add_block(_rec(-10 * 86400, 1, 'old'))   # 10 days ago -> pruned
        bh.add_block(_rec(-60, 2, 'new'))           # 1 min ago -> kept
        self.assertEqual([b['share'] for b in bh.blocks], ['new'])

    def test_get_luck_stats_shape(self):
        bh = block_history.BlockHistory(self.path, retention_days=90)
        empty = bh.get_luck_stats()
        self.assertFalse(empty['luck_available'])
        bh.add_block(_rec(100, 10, 'a', expected_shares=3.0))
        bh.add_block(_rec(200, 16, 'b', expected_shares=3.0))
        stats = bh.get_luck_stats()
        self.assertTrue(stats['luck_available'])
        self.assertEqual(len(stats['blocks']), 2)
        self.assertEqual(stats['blocks'][0]['hash'], 'h' * 64)

    def test_watch_notified(self):
        bh = block_history.BlockHistory(self.path, retention_days=90)
        seen = []
        bh.watch(seen.append)
        bh.add_block(_rec(100, 1, 'a'))
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]['share'], 'a')


if __name__ == '__main__':
    unittest.main()
