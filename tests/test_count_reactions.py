import sys
import unittest
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from count_reactions import (
    Period,
    add_message,
    apply_channel_exclusions,
    build_period,
    list_accessible_channels,
    resolve_custom_emoji,
    validate_user_token,
)


class FakeWebClient:
    def conversations_list(self, **_kwargs):
        return {
            "channels": [
                {"id": "C_PUBLIC", "name": "public", "is_private": False, "is_member": False},
                {"id": "G_JOINED", "name": "private", "is_private": True, "is_member": True},
                {"id": "G_HIDDEN", "name": "hidden", "is_private": True, "is_member": False},
            ],
            "response_metadata": {"next_cursor": ""},
        }


class CountReactionsTest(unittest.TestCase):
    def test_channels_can_be_excluded_by_name_or_id(self) -> None:
        channels = apply_channel_exclusions(
            {
                "C_GENERAL": "general",
                "C_RANDOM": "random",
                "C_SECRET": "secret",
            },
            ["#random", "C_SECRET"],
        )

        self.assertEqual(channels, {"C_GENERAL": "general"})

    def test_bot_token_is_rejected_before_calling_slack_api(self) -> None:
        with self.assertRaisesRegex(SystemExit, "Bot Token"):
            validate_user_token("xoxb-example")

    def test_user_token_is_accepted(self) -> None:
        validate_user_token("xoxp-example")

    def test_user_token_targets_only_joined_channels(self) -> None:
        channels = list_accessible_channels(FakeWebClient())

        self.assertEqual(
            channels,
            {"G_JOINED": "private"},
        )

    def test_until_date_includes_the_whole_day_in_tokyo(self) -> None:
        period = build_period("2026-08-01", "2026-08-31", "Asia/Tokyo")

        self.assertEqual(
            period.start, datetime(2026, 7, 31, 15, 0, tzinfo=timezone.utc)
        )
        self.assertEqual(period.end, datetime(2026, 8, 31, 15, 0, tzinfo=timezone.utc))

    def test_reaction_count_is_aggregated(self) -> None:
        period = Period(
            start=datetime(2026, 8, 1, tzinfo=timezone.utc),
            end=datetime(2026, 9, 1, tzinfo=timezone.utc),
        )
        counts = Counter()
        message_counts = Counter()
        channel_counts = defaultdict(Counter)
        message = {
            "ts": str(datetime(2026, 8, 10, tzinfo=timezone.utc).timestamp()),
            "reactions": [
                {"name": "tada", "count": 4, "users": ["U1"]},
                {"name": "eyes", "count": 2, "users": ["U1", "U2"]},
            ],
        }

        found = add_message(
            message, "C123", period, counts, message_counts, channel_counts
        )

        self.assertTrue(found)
        self.assertEqual(counts, Counter({"tada": 4, "eyes": 2}))
        self.assertEqual(message_counts, Counter({"tada": 1, "eyes": 1}))
        self.assertEqual(channel_counts["C123"]["tada"], 4)

    def test_custom_emoji_alias_is_resolved(self) -> None:
        emoji = {
            "party": "alias:party_parrot",
            "party_parrot": "https://example.com/party.gif",
        }

        self.assertEqual(
            resolve_custom_emoji("party", emoji), "https://example.com/party.gif"
        )


if __name__ == "__main__":
    unittest.main()
