import unittest

from p2pool.bitcoin import script

class Test(unittest.TestCase):
    def test_all(self):
        # 76=A9=14<20 bytes>88AC -> OP_DUP OP_HASH160 PUSH20 .. OP_EQUALVERIFY OP_CHECKSIG
        data = bytes.fromhex('76  A9  14 89 AB CD EF AB BA AB BA AB BA AB BA AB BA AB BA AB BA AB BA  88 AC'.replace(' ', ''))
        parsed = list(script.parse(data))
        # Opcode names are str; push payloads are bytes.  The second element of
        # each pair is (None, stream) for data-less opcodes, so compare names
        # and payloads rather than the stream objects.  (The original test
        # compared whole tuples including the stream, which is why it was
        # recorded as a pre-existing failure -- see PY2_BASELINE.md.)
        self.assertEqual([name for name, _ in parsed],
                         ['UNK_118', 'UNK_169', 'PUSH', 'UNK_136', 'CHECKSIG'])
        pushed = parsed[2][1]
        self.assertEqual(pushed, bytes.fromhex('89abcdef') + bytes.fromhex('abba') * 8)
        self.assertEqual(len(pushed), 20)
        self.assertEqual(script.get_sigop_count(data), 1)

    def test_push_script_tolerates_empty_bytes_datum(self):
        # work.py used to set mm_data = '' (str) when STRATUM_FAKE_AUXMM is
        # off.  Under py2 '' was bytes, so create_push_script worked.  Under
        # py3 '' is str, and b''.join() in create_push_script raises TypeError
        # -- swallowed by the bare except in got_response, so every block
        # submit silently died.  Pin the bytes path work.py now takes.
        # Ints 1..16 encode as OP_1..OP_16 (datum+80), so 10 -> OP_10 == 0x5a;
        # empty bytes is a 0x00 length prefix (an empty push).
        self.assertEqual(script.create_push_script([10, b'']), b'\x5a\x00')
        # An int-only push must be unaffected by the fix.
        self.assertEqual(script.create_push_script([10]), b'\x5a')