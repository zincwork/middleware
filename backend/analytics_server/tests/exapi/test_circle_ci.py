import unittest
from datetime import datetime, timezone

from mhq.exapi.circle_ci import parse_circle_ci_datetime


class TestParseCircleCiDatetime(unittest.TestCase):
    """
    CircleCI's backend is Go, whose RFC3339Nano formatter trims trailing
    zeros from fractional seconds, so ".380000" of a second comes back as
    ".38". Python's datetime.fromisoformat (this repo runs 3.9) only accepts
    exactly 3 or 6 fractional digits, so 2 digits used to raise ValueError,
    which was swallowed into a None timestamp — silently dropping the whole
    deployment run downstream, since a missing started_at reads as "never
    ran" rather than "unknown".
    """

    def test_two_fractional_digits_parses(self):
        # The exact value from the reported log line.
        result = parse_circle_ci_datetime("2026-09-10T13:42:10.38Z")
        self.assertEqual(
            result, datetime(2026, 9, 10, 13, 42, 10, 380000, tzinfo=timezone.utc)
        )

    def test_one_fractional_digit_parses(self):
        result = parse_circle_ci_datetime("2026-09-10T13:42:10.5Z")
        self.assertEqual(
            result, datetime(2026, 9, 10, 13, 42, 10, 500000, tzinfo=timezone.utc)
        )

    def test_three_fractional_digits_parses(self):
        """The one length that already worked before this fix."""
        result = parse_circle_ci_datetime("2026-09-10T13:42:10.123Z")
        self.assertEqual(
            result, datetime(2026, 9, 10, 13, 42, 10, 123000, tzinfo=timezone.utc)
        )

    def test_six_fractional_digits_parses(self):
        """The other length that already worked before this fix."""
        result = parse_circle_ci_datetime("2026-09-10T13:42:10.123456Z")
        self.assertEqual(
            result, datetime(2026, 9, 10, 13, 42, 10, 123456, tzinfo=timezone.utc)
        )

    def test_no_fractional_seconds_parses(self):
        result = parse_circle_ci_datetime("2026-09-10T13:42:10Z")
        self.assertEqual(
            result, datetime(2026, 9, 10, 13, 42, 10, tzinfo=timezone.utc)
        )

    def test_nanosecond_precision_is_truncated_not_rejected(self):
        """Beyond microseconds Python cannot represent it; keep the value we
        can rather than dropping the whole timestamp."""
        result = parse_circle_ci_datetime("2026-09-10T13:42:10.123456789Z")
        self.assertEqual(
            result, datetime(2026, 9, 10, 13, 42, 10, 123456, tzinfo=timezone.utc)
        )

    def test_padding_is_exact_not_approximate(self):
        """.38 of a second is 380ms, not 38ms — a naive zfill on the wrong
        side would silently corrupt the value by two orders of magnitude."""
        result = parse_circle_ci_datetime("2026-09-10T13:42:10.38Z")
        self.assertEqual(result.microsecond, 380000)
        self.assertNotEqual(result.microsecond, 38)

    def test_none_returns_none(self):
        self.assertIsNone(parse_circle_ci_datetime(None))

    def test_empty_string_returns_none(self):
        self.assertIsNone(parse_circle_ci_datetime(""))

    def test_genuinely_malformed_value_returns_none_not_raise(self):
        self.assertIsNone(parse_circle_ci_datetime("not-a-timestamp"))


if __name__ == "__main__":
    unittest.main()
