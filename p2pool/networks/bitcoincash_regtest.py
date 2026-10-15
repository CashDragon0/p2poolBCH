from p2pool.bitcoin import networks

# BCHN regtest. A private, throwaway chain used for local testing only:
# IDENTIFIER and PREFIX are deliberately distinct from every other network so
# a regtest node can never be mistaken for a mainnet or testnet one. They are
# fixed constants, not generated at import, so a share signed on one regtest
# node always verifies on another.
#
# PERSIST = False is the important one -- regtest chains are wiped between runs,
# so persisting a share store across restarts would resurrect orphaned state.

PARENT = networks.nets['bitcoincash_regtest']
SHARE_PERIOD = 30 # seconds
CHAIN_LENGTH = 60*60//10 # shares
REAL_CHAIN_LENGTH = 60*60//10 # shares
TARGET_LOOKBEHIND = 200 # shares
SPREAD = 3 # blocks
IDENTIFIER = bytes.fromhex('b2f1c0d4e3a59768')
PREFIX = bytes.fromhex('c7e4a1b93d0f5268')
P2P_PORT = 19448
MIN_TARGET = 0
MAX_TARGET = 2**256//2 - 1
PERSIST = False
WORKER_PORT = 19447
BOOTSTRAP_ADDRS = []
ANNOUNCE_CHANNEL = '#p2pool-regtest'
VERSION_CHECK = lambda v: None
VERSION_WARNING = lambda v: None
SOFTFORKS_REQUIRED = set()
MINIMUM_PROTOCOL_VERSION = 3301
BLOCK_MAX_SIZE = 32000000
BLOCK_MAX_WEIGHT = 128000000

# Pin padding_bugfix to True at import time.
#
# A fresh regtest chain has no shares, so neither writer in p2pool/data.py
# has run yet when the first getwork arrives, and pubkey_hash_to_script2
# takes its unpadded branch. That branch formats the pubkey hash with a bare
# '{:x}', which drops the leading zero nibble and yields 39 hex chars for a
# hash like 0x0d92..., and binascii.unhexlify rejects odd-length input. The
# stage-5 mining address bchreg:qqxe99... hashes to exactly such a value, so
# every getwork failed with "Odd-length string" and no share could be found
# past genesis.
#
# The two writers in p2pool/data.py also pin the flag unconditionally (:63
# and :899), but neither has run when the first getwork arrives on a fresh
# chain -- there is no share yet to load or to be best -- so the flag has to
# already be True here.
PARENT.padding_bugfix = True