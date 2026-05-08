"""
File summary: This file defines a function to retrieve a logging object with a specific name for debugging purposes.
"""

# src/debugflow/__init__.py

# This looks for 'log' inside logger_system.py
from .logger_system import log

def get_logger(name):
    import logging
    return logging.getLogger(f"debugflow.{name}")