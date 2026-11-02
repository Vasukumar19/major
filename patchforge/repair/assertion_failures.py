"""Extract small, model-actionable assertion failures from test output."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class AssertionFailure:
    test_id: str = ""
    assertion: str = ""
    expected: str = ""
    actual: str = ""
    exception: str = ""
    context: str = ""

    def format_for_refinement(self) -> str:
        fields = []
        if self.test_id:
            fields.append(f"test={self.test_id}")
        if self.assertion:
            fields.append(f"assertion={self.assertion}")
        if self.expected:
            fields.append(f"expected={self.expected}")
        if self.actual:
            fields.append(f"actual={self.actual}")
        if self.exception:
            fields.append(f"exception={self.exception}")
        return "; ".join(fields) or self.context


class AssertionFailureExtractor:
    """Best-effort pytest/unittest parser with bounded, non-speculative output."""

    _FAILED = re.compile(r"^(?:FAILED|ERROR)\s+([^\s]+)", re.MULTILINE)
    _ASSERT_SOURCE = re.compile(r"^\s*(?:[>E]\s+)?(assert\s+.+)$", re.MULTILINE)
    _ASSERT_DIFF = re.compile(r"(?:AssertionError:\s*)?(?P<actual>.+?)\s*==\s*(?P<expected>.+)")
    _EXCEPTION = re.compile(r"^E\s+([A-Za-z_][\w.]*?(?:Error|Exception|Failure))(?::\s*(.*))?$", re.MULTILINE)

    @classmethod
    def extract(cls, output: str, *, fallback_test_ids: list[str] | None = None, limit: int = 3) -> list[AssertionFailure]:
        if not output:
            return []
        # Pytest often puts the ``FAILED test-id`` summary before the detailed
        # assertion block. Keep that relationship intact; splitting on the
        # decorative underscore separator loses the test identity.
        blocks = [output]
        failures: list[AssertionFailure] = []
        seen: set[tuple[str, str, str, str]] = set()
        fallback = iter(fallback_test_ids or [])
        for block in blocks:
            test_match = cls._FAILED.search(block)
            test_id = test_match.group(1) if test_match else next(fallback, "")
            assertion_match = cls._ASSERT_SOURCE.search(block)
            assertion = assertion_match.group(1).strip() if assertion_match else ""
            exception_match = cls._EXCEPTION.search(block)
            exception = ""
            actual = ""
            expected = ""
            if exception_match:
                exception = exception_match.group(1)
                detail = exception_match.group(2) or ""
                diff = cls._ASSERT_DIFF.search(detail)
                if diff:
                    actual, expected = diff.group("actual").strip(), diff.group("expected").strip()
                    actual = actual.removeprefix("assert ").strip()
            if not actual and assertion:
                diff = cls._ASSERT_DIFF.search(assertion.removeprefix("assert "))
                if diff:
                    actual, expected = diff.group("actual").strip(), diff.group("expected").strip()
                    actual = actual.removeprefix("assert ").strip()
            if not (test_id or assertion or exception):
                continue
            item = AssertionFailure(
                test_id=test_id,
                assertion=assertion,
                expected=expected,
                actual=actual,
                exception=exception,
                context="\n".join(block.splitlines()[-8:])[:1200],
            )
            key = (item.test_id, item.assertion, item.expected, item.actual)
            if key not in seen:
                seen.add(key)
                failures.append(item)
            if len(failures) >= limit:
                break
        return failures
