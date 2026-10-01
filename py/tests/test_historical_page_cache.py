"""Restart and shard reuse of complete, historical HyperSync log pages."""
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from drhs import hypersync as H


def response(start, end, head=2000):
    return {
        'archive_height': head, 'next_block': end,
        'data': [{'blocks': [{'number': start, 'timestamp': 100}],
                  'logs': [{'block_number': start, 'log_index': 0,
                            'address': '0xabc', 'data': '0x'}]}],
    }


def setup(monkeypatch, tmp_path, fetch):
    monkeypatch.setenv('ENVIO_API_TOKEN', 'unit-test')
    monkeypatch.setenv('DRHS_CACHE_DIR', str(tmp_path))
    monkeypatch.setattr(H, '_RETRIES', 0)
    def post(url, *, json, **kwargs):
        data = fetch(json)
        class Response:
            status_code = 200
            ok = True
            def json(self):
                return data
        return Response()
    monkeypatch.setattr(requests, 'post', post)
    return post


def test_historical_pages_reused_and_field_selection_is_part_of_key(monkeypatch, tmp_path):
    calls = []
    post = setup(monkeypatch, tmp_path,
                 lambda b: calls.append(b) or response(b['from_block'], b['to_block']))
    args = ('ethereum', [{'address': ['0xabc']}], 10, 20)
    first = H._query_logs_live(*args, post=post)
    assert H._query_logs_live(*args, post=post).rows == first.rows
    assert len(calls) == 1
    H._query_logs_live(*args, post=post,
                       log_fields=['block_number', 'address'])
    assert len(calls) == 2
    monkeypatch.setenv('HYPERSYNC_URL_ETHEREUM', 'https://alternate.example/query')
    H._query_logs_live(*args, post=post)
    assert len(calls) == 3


def test_restart_reuses_pages_before_failed_cursor(monkeypatch, tmp_path):
    calls = []
    def fetch(body):
        start = body['from_block']
        calls.append(start)
        if len(calls) == 2:
            raise requests.ConnectionError('transient test failure')
        return response(start, start + 1)
    post = setup(monkeypatch, tmp_path, fetch)
    args = ('ethereum', [{'address': ['0xabc']}], 10, 11)
    with pytest.raises(H.HyperSyncError):
        H._query_logs_live(*args, post=post)
    result = H._query_logs_live(*args, post=post)
    assert calls == [10, 11, 11]
    assert [r.block_number for r in result.rows] == [10, 11]


def test_near_head_pages_are_not_cached(monkeypatch, tmp_path):
    calls = []
    post = setup(monkeypatch, tmp_path,
                 lambda b: calls.append(b) or response(10, 21, head=100))
    for _ in range(2):
        H._query_logs_live('ethereum', [{'address': ['0xabc']}], 10, 20,
                           post=post)
    assert len(calls) == 2


def test_malformed_page_is_not_cached(monkeypatch, tmp_path):
    def fetch(body):
        data = response(10, 21)
        data['data'][0]['blocks'] = []
        return data
    post = setup(monkeypatch, tmp_path, fetch)
    with pytest.raises(H.HyperSyncError, match='no matching block timestamp'):
        H._query_logs_live('ethereum', [{'address': ['0xabc']}], 10, 20,
                           post=post)
    assert not list(tmp_path.rglob('*.json.gz'))


def test_corrupt_cache_refetches(monkeypatch, tmp_path):
    calls = []
    post = setup(monkeypatch, tmp_path,
                 lambda b: calls.append(b) or response(10, 21))
    args = ('ethereum', [{'address': ['0xabc']}], 10, 20)
    H._query_logs_live(*args, post=post)
    next(tmp_path.rglob('*.json.gz')).write_bytes(b'interrupted cache')
    H._query_logs_live(*args, post=post)
    assert len(calls) == 2


def test_optional_cache_write_failure_preserves_complete_result(monkeypatch, tmp_path):
    post = setup(monkeypatch, tmp_path, lambda b: response(10, 21))
    def fail(*args):
        raise OSError('disk full')
    monkeypatch.setattr(H.os, 'replace', fail)
    result = H._query_logs_live('ethereum', [{'address': ['0xabc']}], 10, 20,
                                post=post)
    assert len(result.rows) == 1
    assert not list(tmp_path.rglob('*.tmp'))
    assert not list(tmp_path.rglob('*.json.gz'))


def test_outer_cache_live_fetch_forwards_injected_transport(monkeypatch, tmp_path):
    monkeypatch.setenv('ENVIO_API_TOKEN', 'unit-test')
    monkeypatch.setenv('DRHS_CACHE_DIR', str(tmp_path))
    calls = []

    def post(url, *, json, **kwargs):
        calls.append(json)
        data = response(json['from_block'], json['to_block'])

        class Response:
            status_code = 200
            ok = True

            def json(self):
                return data

        return Response()

    result = H.query_logs('ethereum', [{'address': ['0xabc']}], 10, 20,
                          post=post, use_cache=True)
    assert len(result.rows) == 1
    assert len(calls) == 1


def test_no_cache_bypasses_historical_pages(monkeypatch, tmp_path):
    calls = []
    post = setup(monkeypatch, tmp_path,
                 lambda b: calls.append(b) or response(10, 21))
    args = ('ethereum', [{'address': ['0xabc']}], 10, 20)
    H.query_logs(*args, post=post, use_cache=False)
    H.query_logs(*args, post=post, use_cache=False)
    assert len(calls) == 2
    assert not list(tmp_path.rglob('*.json.gz'))
