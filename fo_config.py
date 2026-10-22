"""
fo_config.py: Proxy loader for FnOQuantAI configuration.
Safely loads local config.py to prevent circular name shadowing with CommonModule.
"""
import os
import sys
import importlib.util

DEFAULT_TICK_SIZE: float = 0.05  # Standard NSE Equity tick fallback

_local_config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.py')
if os.path.exists(_local_config_path):
    _spec = importlib.util.spec_from_file_location('fnoquantai_user_config', _local_config_path)
    _user_cfg = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_user_cfg)
    for _key, _val in _user_cfg.__dict__.items():
        if not _key.startswith('__'):
            globals()[_key] = _val
else:
    raise FileNotFoundError(f"Required configuration file not found: {_local_config_path}")
