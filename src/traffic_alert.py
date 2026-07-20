#!/usr/bin/env python3
"""Track VPS traffic by provider billing cycle and send Telegram alerts."""

from __future__ import annotations

import argparse
import calendar
import fcntl
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
