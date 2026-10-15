import importlib
import pkgutil

# Auto-discover every sibling module in this package. Python 2's
# __import__(name, globals(), ...) resolved bare names against __path__ as an
# implicit relative import, which Python 3 removed; importlib with an explicit
# '.' package keeps the same behaviour.
nets = {}
for _loader, _name, _ispkg in pkgutil.iter_modules(__path__):
    nets[_name] = importlib.import_module('.' + _name, __name__)

for _net_name, _net in nets.items():
    _net.NAME = _net_name
