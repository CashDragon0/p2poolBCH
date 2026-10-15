import os
import platform

from twisted.internet import defer

from .. import data, helper
from p2pool.util import pack


# BCHN regtest. Unlike testnet, regtest NEVER re-organises on a --redefine
# flag, so the genesis hash check is sufficient.
# Wire magic is BCHN's, NOT Bitcoin's regtest fabfb5da: captured live from
# two BCHN 29.2.0 nodes (da b5 bf fa). The old shared-with-Bitcoin value
# made every inbound handshake die with "INVALID MESSAGESTART".
P2P_PREFIX = bytes.fromhex('dab5bffa')
P2P_PORT = 18444
ADDRESS_VERSION = 0
ADDRESS_P2SH_VERSION = 5
# All Bitcoin Cash-family networks encode addresses as CashAddr, not Bech32.
# Address coding checks this flag rather than matching on SYMBOL, so a network
# whose symbol differs (e.g. regtest's 'rBCH') is handled correctly.
USES_CASHADDR = True
HUMAN_READABLE_PART = 'bchreg'  # verified: validateaddress returns bchreg:q... on regtest
RPC_PORT = 28332
RPC_CHECK = defer.inlineCallbacks(lambda bitcoind: defer.returnValue(
            (yield helper.check_block_header(bitcoind, '0f9188f13cb7b2c71f2a335e3a4fc328bf5beb436012afca590b1a11466e2206')) and # regtest genesis
            (yield bitcoind.rpc_getblockchaininfo())['chain'] == 'regtest'
        ))
SUBSIDY_FUNC = lambda height: 50*100000000 >> (height + 1)//150  # verified: halving every 150 blocks
POW_FUNC = data.hash256
BLOCK_PERIOD = 600 # s
SYMBOL = 'rBCH'
CONF_FILE_FUNC = lambda: os.path.join(os.path.join(os.environ['APPDATA'], 'Bitcoin') if platform.system() == 'Windows' else os.path.expanduser('~/Library/Application Support/Bitcoin/') if platform.system() == 'Darwin' else os.path.expanduser('~/.bitcoin'), 'bitcoin.conf')
BLOCK_EXPLORER_URL_PREFIX = '#'
ADDRESS_EXPLORER_URL_PREFIX = '#'
TX_EXPLORER_URL_PREFIX = '#'
SANE_TARGET_RANGE = (2**256//2**32//1000000000 - 1, 2**256//2 - 1)
DUMB_SCRYPT_DIFF = 1
DUST_THRESHOLD = 0.001e8