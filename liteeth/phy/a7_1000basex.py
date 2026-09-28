#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Enjoy-Digital <enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

# Compatibility alias for liteeth.phy.serial.basex.a7_gtp.
import sys as _sys

from liteeth.phy.serial.basex import a7_gtp as _impl

_sys.modules[__name__] = _impl
