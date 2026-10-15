"""File-persistence regression tests.

Five call sites wrote a py2 str straight to a binary-mode file.  Under py3 that
raises TypeError, so the write never happened -- and every one of these is a
background loop or a shutdown path, which is why nothing surfaced at startup:

  - data.py ShareStore._add_line   -- the persisted share chain
  - main.py save_addrs             -- peer addresses, every 60s
  - main.py payout-address cache   -- startup path
  - stratum.py reject dump         -- share-reject forensics
  - broadcaster.py peer db         -- peer database

These are write/read round-trips, so the assertion is that the bytes on disk
are what py2 would have written, and that they read back.
"""
import json
import os
import shutil
import tempfile
import unittest

from p2pool import data as p2pool_data


class ShareStoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.prefix = os.path.join(self.dir, 'shares')

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _store(self):
        return p2pool_data.ShareStore(self.prefix, None, lambda s: None,
                                      lambda h: None)

    def test_add_line_writes_and_reads_back(self):
        store = self._store()
        line = '%i %s' % (2, '%064x' % 0xdeadbeefcafebabe)
        # _add_line returns the file it chose (prefix0 on a fresh store).
        written_path = store._add_line(line)
        self.assertEqual(os.path.dirname(written_path), store.dirname)
        with open(written_path, 'rb') as f:
            written = f.read()
        self.assertEqual(written, (line + '\n').encode('utf-8'))
        self.assertEqual(written.decode('utf-8').strip(), line)

    def test_appends_are_readable_by_a_fresh_store(self):
        store = self._store()
        hashes = [0x1111, 0x2222, 0x3333]
        path = None
        for h in hashes:
            path = store._add_line('%i %x' % (2, h))
        # Reconstructing a ShareStore over the same prefix must recover the
        # verified hashes from disk -- the whole point of the file existing.
        reloaded = p2pool_data.ShareStore(self.prefix, None, lambda s: None,
                                          lambda h: None)
        recovered = set()
        for _fname, (_shares, verified) in reloaded.known_desired.items():
            recovered |= verified
        self.assertEqual(recovered, set(hashes))
        with open(path, 'rb') as f:
            lines = f.read().decode('utf-8').splitlines()
        self.assertEqual(len(lines), 3)


class JSONToBinaryFileTest(unittest.TestCase):
    """The json.dumps -> binary file shape, in the exact form main.py uses."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_addr_store_items_round_trip(self):
        # main.py:308 did json.dumps(node.p2p_node.addr_store.items()).
        # Under py3 dict.items() is a view, which json cannot serialise --
        # so peer addresses were never persisted at all.
        addr_store = {('1.2.3.4', 8333): 1700000000.0}
        path = os.path.join(self.dir, 'addrs')
        with open(path, 'wb') as f:
            f.write(json.dumps(list(addr_store.items())).encode('utf-8'))
        with open(path, 'rb') as f:
            # json turns the (key, value) tuples into nested lists.
            self.assertEqual(json.loads(f.read()),
                             [[['1.2.3.4', 8333], 1700000000.0]])

    def test_stats_round_trip(self):
        # web.py:941
        stat_log = [{'ts': 1700000000.0, 'hashrate': 1234.5}]
        path = os.path.join(self.dir, 'stats')
        with open(path, 'wb') as f:
            f.write(json.dumps(stat_log).encode('utf-8'))
        with open(path, 'rb') as f:
            self.assertEqual(json.loads(f.read()), stat_log)

    def test_reject_dump_lines_are_appendable(self):
        # stratum.py:313
        path = os.path.join(self.dir, 'rejects')
        row = {'user': 'u1', 'reason': 'low diff'}
        for _ in range(3):
            with open(path, 'ab') as f:
                f.write((json.dumps(row, sort_keys=True) + '\n').encode('utf-8'))
        with open(path, 'rb') as f:
            lines = f.read().decode('utf-8').splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual(json.loads(lines[0]), row)

    def test_payout_address_cache_round_trip(self):
        # main.py:148
        address = 'bitcoincash:qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a'
        path = os.path.join(self.dir, 'cached_payout_address')
        with open(path, 'wb') as f:
            f.write(address.encode('utf-8'))
        with open(path, 'rb') as f:
            self.assertEqual(f.read().decode('utf-8'), address)


if __name__ == '__main__':
    unittest.main()