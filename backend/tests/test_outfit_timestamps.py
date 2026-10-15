"""A committed outfit keeps identical wire clocks across database timezones."""
from datetime import datetime, timedelta, timezone
import unittest

from app.api.serializers import outfit_to_api
from app.models import Outfit


class OutfitTimestampTests(unittest.TestCase):
    def test_same_instant_has_one_stable_wire_value_before_and_after_reload(self):
        expected = '2026-10-16T00:05:12.123456+00:00'
        for clock in (
                datetime(2026, 10, 16, 0, 5, 12, 123456),
                datetime(2026, 10, 16, 0, 5, 12, 123456, tzinfo=timezone.utc),
                datetime(2026, 10, 16, 3, 5, 12, 123456, tzinfo=timezone(timedelta(hours=3)))):
            with self.subTest(clock=clock):
                outfit = Outfit(id='clock-fixture', items_json={}, total_price=0, created_at=clock)
                self.assertEqual(outfit_to_api(outfit)['created_at'], expected)
