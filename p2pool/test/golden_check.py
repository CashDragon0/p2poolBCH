#!/usr/bin/env python
"""Golden-vector gate for the py2 -> py3 p2pool port.

Re-runs p2pool/test/capture_vectors.py under the CURRENT interpreter and
diffs the result against the frozen py2 reference in
p2pool/test/golden_vectors.json.

    python p2pool/test/golden_check.py          # summary + exit code
    python p2pool/test/golden_check.py -v       # print every mismatch
    python p2pool/test/golden_check.py --section payout --section pack

Exit code 0 => byte-identical to the py2 reference.
Exit code 1 => at least one mismatch (the port is NOT faithful).

Why this exists
---------------
p2pool's share serialisation, merkle roots, coinbase scriptsig, payout
weights and CashAddr output are CONSENSUS-CRITICAL. A py3 port that
reorders a payout weight or pads an address differently silently forks the
node off the P2P network. Reading the diff cannot reliably catch that;
this can.

Negative cases are part of the contract too: a vector that raises on py2
MUST raise on py3. The exception TYPE name is part of the contract -- except
for the single documented alias folded by _normalise_pair below, which exists
because binascii names the same failure differently in the two interpreters.
"""
from __future__ import print_function

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CAPTURE = os.path.join(HERE, 'capture_vectors.py')
GOLDEN = os.path.join(HERE, 'golden_vectors.json')

# Fields that legitimately differ between interpreters.
_IGNORE_META = ('python',)

# binascii.unhexlify raises TypeError on py2 but binascii.Error (a ValueError
# subclass) on py3 for the identical input. That hits the stage-5 "Odd-length
# string" crash at bitcoin/data.py:519, pinned as address.rtest_nopad_script2_5
# in golden_vectors.json. Scope the alias to that one key: pack.ipv6_4 and
# pack.ipv6_4_rt are also TypeErrors on py2, but py3 raises ValueError there,
# so a blanket err->err mapping would silently rewrite the three known
# mismatches and turn a real diff into a silent pass.
_RTEST_ODD_LENGTH = 'address.rtest_nopad_script2_5'


def _normalise_types(node):
    """The capture's type-check marker records type(x).__name__.

    Python 2 calls a large int 'long'; Python 3 calls it 'int'. The VALUES
    are identical, so normalise the marker on both sides -- otherwise every
    hash160 vector differs for no substantive reason.
    """
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k == 'NOT_BYTES' and v == 'long':
                v = 'int'
            elif k == 'NOT_TEXT' and v == 'long':
                v = 'str'
            out[k] = _normalise_types(v)
        return out
    if isinstance(node, list):
        return [_normalise_types(x) for x in node]
    return node


def _normalise_pair(key, want, got):
    """Fold the one documented py2/py3 exception-name difference."""
    if key == _RTEST_ODD_LENGTH and not want.get('ok') and not got.get('ok'):
        if want.get('err') == 'TypeError' and got.get('err') == 'Error':
            return dict(want, err='OddLength'), dict(got, err='OddLength')
    return want, got


def _load(path):
    with open(path) as f:
        return json.load(f)


def _capture():
    """Run the capture script under the current interpreter."""
    out_fd, out_path = tempfile.mkstemp(suffix='.json', prefix='p2cap')
    os.close(out_fd)
    env = dict(os.environ)
    repo = os.path.dirname(os.path.dirname(HERE))
    env['PYTHONPATH'] = repo + os.pathsep + env.get('PYTHONPATH', '')
    env['PYTHONWARNINGS'] = 'ignore'
    try:
        p = subprocess.Popen([sys.executable, CAPTURE, out_path],
                             cwd=repo, env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        so, se = p.communicate()
        if p.returncode != 0:
            sys.stderr.write('capture FAILED (rc=%d)\n' % p.returncode)
            sys.stderr.write(se.decode('utf-8', 'replace')[-4000:] + '\n')
            return None, None
        return _load(out_path), se.decode('utf-8', 'replace')
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass


def _flatten(vectors, prefix=''):
    """{section: {key: record}} -> {'section.key': record}."""
    out = {}
    for sec, entries in vectors.items():
        for k, v in entries.items():
            out['%s.%s' % (sec, k)] = v
    return out


def _diff(want, got):
    keys = sorted(set(want) | set(got))
    out = []
    for k in keys:
        w, g = want.get(k, '<MISSING>'), got.get(k, '<MISSING>')
        if isinstance(w, dict) and isinstance(g, dict):
            w, g = _normalise_pair(k, w, g)
        if w != g:
            out.append((k, w, g))
    return out


def main(argv):
    verbose = '-v' in argv or '--verbose' in argv
    only = []
    for i, a in enumerate(argv):
        if a == '--section' and i + 1 < len(argv):
            only.append(argv[i + 1])

    if not os.path.exists(GOLDEN):
        sys.stderr.write('missing golden reference: %s\n' % GOLDEN)
        return 2

    golden = _load(GOLDEN)
    got, stderr = _capture()
    if got is None:
        return 2

    if stderr:
        for line in stderr.splitlines():
            if 'WROTE' in line or 'STRATUM' in line:
                sys.stderr.write(line + '\n')

    for k in _IGNORE_META:
        got.get('meta', {}).pop(k, None)
        golden.get('meta', {}).pop(k, None)

    w = _flatten(_normalise_types(golden['vectors']))
    g = _flatten(_normalise_types(got['vectors']))

    if only:
        w = dict((k, v) for k, v in w.items() if k.split('.')[0] in only)
        g = dict((k, v) for k, v in g.items() if k.split('.')[0] in only)

    diffs = _diff(w, g)

    # Section-level rollup, so a systemic break is obvious.
    by_sec = {}
    for k, _, _ in diffs:
        sec = k.split('.')[0]
        by_sec[sec] = by_sec.get(sec, 0) + 1

    print('golden vectors : %s' % os.path.basename(GOLDEN))
    print('interpreter    : %s' % sys.version.split()[0])
    print('vectors        : %d compared' % len(w))
    print('sections       : %d' % len(golden['meta'].get('sections', [])))
    if by_sec:
        print('MISMATCHES     : %d' % len(diffs))
        for sec in sorted(by_sec, key=lambda s: -by_sec[s]):
            print('   %-12s %d' % (sec, by_sec[sec]))
    else:
        print('MISMATCHES     : 0  -- byte-identical to the py2 reference')

    if verbose and diffs:
        print('\n--- details ---')
        for k, a, b in diffs:
            print('\n%s' % k)
            print('   py2: %s' % json.dumps(a)[:300])
            print('   py3: %s' % json.dumps(b)[:300])

    if not diffs:
        print('\nGATE PASS -- port is faithful to the py2 reference.')
        return 0
    print('\nGATE FAIL -- %d vector(s) diverge from py2. The port is NOT '
          'consensus-faithful yet.' % len(diffs))
    return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))