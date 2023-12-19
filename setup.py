#!/usr/bin/env python3

from setuptools import setup
from setuptools import find_packages


with open("README.md", "r", encoding="utf-8") as fp:
    long_description = fp.read()


setup(
    name                          = "liteeth",
    version                       = "2023.08",
    description                   = "Small footprint and configurable Ethernet core",
    long_description              = long_description,
    long_description_content_type = "text/markdown",
    author                        = "Florent Kermarrec",
    author_email                  = "florent@enjoy-digital.fr",
    url                           = "http://enjoy-digital.fr",
    download_url                  = "https://github.com/enjoy-digital/liteeth",
    test_suite                    = "test",
    license                       = "BSD",
    python_requires               = "~=3.7",
    install_requires              = ["pyyaml", "litex", "liteiclink"],
    extras_require                = {"develop": ["setuptools"]},
    packages                      = find_packages(exclude=("test*", "sim*", "doc*", "examples*")),
    include_package_data          = True,
    keywords                      = "HDL ASIC FPGA hardware design",
    classifiers                   = [
        "Topic                :: Scientific/Engineering :: Electronic Design Automation (EDA)",
        "Environment          :: Console",
        "Development Status   :: 3 - Alpha",
        "Intended Audience    :: Developers",
        "License              :: OSI Approved :: BSD License",
        "Operating System     :: OS Independent",
        "Programming Language :: Python",
    ],
    entry_points = {
        "console_scripts": [
            "liteeth_gen=liteeth.gen:main",
        ],
    },
)
