#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Enjoy-Digital <enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

# Compatibility import; implementation lives in liteeth.phy.serial.baser.a7_gtp.
from liteeth.phy.serial.baser import a7_gtp as _impl
from liteeth.phy.serial.baser.a7_gtp import *

def __getattr__(name):
    return getattr(_impl, name)

def __dir__():
    return sorted(set(globals()) | set(dir(_impl)))
