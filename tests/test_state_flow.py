"""Unit tests for Deterministic State-Flow and Control-Flow Analyzer."""
from __future__ import annotations

import pytest

from patchforge.analysis.state_flow import (
    StateFlowAnalyzer,
    extract_state_flow,
    format_state_flow_summary,
)


SAMPLE_REDIRECT_FUNC = """
def resolve_redirects(self, resp, req, stream=False, timeout=None, verify=True, cert=None, proxies=None):
    i = 0
    hist = []
    while resp.is_redirect:
        prepared_request = req.copy()
        resp.content
        if i >= self.max_redirects:
            raise TooManyRedirects('Exceeded %s redirects.' % self.max_redirects)
        resp.close()
        url = resp.headers['location']
        if url.startswith('//'):
            url = '%s:%s' % (url, url)
        prepared_request.url = url
        try:
            resp = self.send(prepared_request, stream=stream, timeout=timeout, verify=verify, cert=cert, proxies=proxies)
        except HTTPError:
            raise
        except ConnectionError:
            pass
        i += 1
        yield resp
    return resp
"""


def test_state_flow_extraction():
    reports = extract_state_flow(SAMPLE_REDIRECT_FUNC, target_symbol="resolve_redirects")
    assert len(reports) == 1
    rep = reports[0]

    assert rep.symbol_name == "resolve_redirects"
    assert "self" in rep.parameters
    assert "resp" in rep.parameters
    assert "req" in rep.parameters

    # Explicit copies detected
    assert any("req.copy()" in c for c in rep.copied_vars)

    # Attribute mutations detected
    assert any("prepared_request.url" in a for a in rep.attribute_mutations)

    # Loops detected
    assert len(rep.loops) == 1
    loop = rep.loops[0]
    assert loop.loop_type == "while"
    assert "resp.is_redirect" in loop.loop_var
    assert "i" in loop.carried_vars
    assert any("req.copy()" in c for c in loop.copies_inside)

    # Exception blocks detected
    assert len(rep.exception_blocks) == 1
    exc = rep.exception_blocks[0]
    assert "HTTPError" in exc.caught_exceptions
    assert "ConnectionError" in exc.caught_exceptions
    assert exc.has_reraise is True
    assert exc.is_suppressed is True


def test_format_state_flow_summary():
    summary = format_state_flow_summary(SAMPLE_REDIRECT_FUNC, target_symbol="resolve_redirects")
    assert "=== STATE & CONTROL FLOW DIAGNOSTICS ===" in summary
    assert "Target: resolve_redirects" in summary
    assert "Explicit Copies:" in summary
    assert "req.copy()" in summary
    assert "Loop (while" in summary
    assert "HTTPError" in summary
