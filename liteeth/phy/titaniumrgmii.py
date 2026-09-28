#
# This file is part of LiteEth.
#
# Copyright (c) 2026 Enjoy-Digital <enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

# Compatibility alias for liteeth.phy.parallel.rgmii.titanium.
import sys as _sys

from liteeth.phy.parallel.rgmii import titanium as _impl

_sys.modules[__name__] = _impl
