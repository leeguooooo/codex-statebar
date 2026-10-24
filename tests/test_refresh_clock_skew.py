import json
import time

import pytest
from codex_statebar import git_cache, balance_cache


@pytest.mark.parametrize('module', [git_cache, balance_cache])
def test_future_snapshot_cannot_remain_fresh(module):
    assert not module.is_fresh({'ts': 2000, 'supported': True}, now=1000)
    assert module.is_fresh({'ts': 1000.25, 'supported': True}, now=1000)


@pytest.mark.parametrize('module', [git_cache, balance_cache])
def test_future_inflight_marker_does_not_disable_refresh_forever(module, tmp_path, monkeypatch):
    monkeypatch.setattr(module, '_cache_root', lambda: tmp_path)
    marker = module._inflight_path('/repo' if module is git_cache else 'account')
    marker.write_text(json.dumps({'ts': time.time() + 10000, 'pid': 1}))
    assert not module.is_inflight('/repo' if module is git_cache else 'account')
