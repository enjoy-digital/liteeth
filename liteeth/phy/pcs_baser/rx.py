#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Enjoy-Digital <enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

# Compatibility import; implementation lives in liteeth.phy.serial.baser.pcs.rx.
from liteeth.phy.serial.baser.pcs import rx as _impl
from liteeth.phy.serial.baser.pcs.rx import *

def __getattr__(name):
    return getattr(_impl, name)

def __dir__():
    return sorted(set(globals()) | set(dir(_impl)))
