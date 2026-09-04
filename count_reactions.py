#!/usr/bin/env python3
"""Slackメッセージに付いた絵文字リアクションを集計する。"""

from __future__ import annotations

import argparse
import csv
import json
import os
import time as time_module
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from slack_sdk import WebClient
from slack_sdk.http_retry.builtin_handlers import RateLimitErrorRetryHandler
from slack_sdk.web import SlackResponse


ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "results"


@dataclass(frozen=True)
class Period:
    start: datetime
    end: datetime

    def contains_ts(self, value: str) -> bool:
        timestamp = datetime.fromtimestamp(float(value), tz=timezone.utc)
        return self.start <= timestamp < self.end


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "指定期間に投稿されたSlackメッセージへ、現在付いているリアクションを集計します"
        )
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument(
        "--channel",
        action="append",
        dest="channels",
        help="集計するチャンネルID。複数回指定できます",
    )
    target.add_argument(
        "--all-channels",
        action="store_true",
        help="自分が参加している公開・非公開チャンネルを集計します",
    )
    parser.add_argument(
        "--exclude-channel",
        action="append",
        dest="excluded_channels",
        default=[],
        help="集計から除外するチャンネル名またはID。複数回指定できます",
    )
    parser.add_argument("--since", required=True, help="開始日（YYYY-MM-DD、当日を含む）")
    parser.add_argument("--until", required=True, help="終了日（YYYY-MM-DD、当日を含む）")
    parser.add_argument("--timezone", default="Asia/Tokyo", help="日付を解釈するタイムゾーン")
    parser.add_argument(
        "--thread-lookback-days",
        type=int,
        default=30,
        help="期間前から続くスレッドの親投稿を探す日数",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def build_period(since: str, until: str, timezone_name: str) -> Period:
    zone = ZoneInfo(timezone_name)
    start_date = date.fromisoformat(since)
    until_date = date.fromisoformat(until)
    if until_date < start_date:
        raise ValueError("--untilは--since以降の日付にしてください")

    start = datetime.combine(start_date, time.min, tzinfo=zone).astimezone(timezone.utc)
    end = datetime.combine(
        until_date + timedelta(days=1), time.min, tzinfo=zone
    ).astimezone(timezone.utc)
    return Period(start=start, end=end)


def validate_user_token(token: str) -> None:
    """誤ってBot Tokenを設定した場合は、APIを呼ぶ前に分かりやすく止める。"""
    if token.startswith("xoxb-"):
        raise SystemExit(
            "SLACK_USER_TOKENにBot Token（xoxb-）が設定されています。"
            "SlackアプリのOAuth & PermissionsにあるUser OAuth Tokenを設定してください。"
        )


def apply_channel_exclusions(
    channels: dict[str, str], excluded_channels: Iterable[str]
) -> dict[str, str]:
    """チャンネル名またはIDに一致するものを集計対象から除外する。"""
    excluded = {value.removeprefix("#") for value in excluded_channels}
    return {
        channel_id: channel_name
        for channel_id, channel_name in channels.items()
        if channel_id not in excluded and channel_name not in excluded
    }


def next_cursor(response: SlackResponse) -> str:
    metadata = response.get("response_metadata") or {}
    return str(metadata.get("next_cursor") or "")


def fetch_history(
    client: WebClient, channel: str, oldest: datetime, latest: datetime
) -> Iterable[dict[str, Any]]:
    cursor = ""
    while True:
        response = client.conversations_history(
            channel=channel,
            oldest=str(oldest.timestamp()),
            latest=str(latest.timestamp()),
            inclusive=True,
            limit=200,
            cursor=cursor or None,
        )
        yield from response.get("messages", [])
        cursor = next_cursor(response)
        if not cursor:
            break


def list_accessible_channels(client: WebClient) -> dict[str, str]:
    """User Tokenを発行した本人が参加しているチャンネルを取得する。"""
    joined: dict[str, str] = {}
    cursor = ""
    while True:
        response = client.conversations_list(
            types="public_channel,private_channel",
            exclude_archived=True,
            limit=200,
            cursor=cursor or None,
        )
        for channel in response.get("channels", []):
            channel_id = channel.get("id")
            if channel.get("is_member") and isinstance(channel_id, str):
                joined[channel_id] = str(channel.get("name") or channel_id)
        cursor = next_cursor(response)
        if not cursor:
            return joined


def fetch_replies(
    client: WebClient,
    channel: str,
    parent_ts: str,
    period: Period,
) -> Iterable[dict[str, Any]]:
    cursor = ""
    while True:
        response = client.conversations_replies(
            channel=channel,
            ts=parent_ts,
            oldest=str(period.start.timestamp()),
            latest=str(period.end.timestamp()),
            inclusive=True,
            limit=200,
            cursor=cursor or None,
        )
        for message in response.get("messages", []):
            # conversations.repliesは親投稿も返すため、二重集計しない。
            if message.get("ts") != parent_ts:
                yield message
        cursor = next_cursor(response)
        if not cursor:
            break


def add_message(
    message: dict[str, Any],
    channel: str,
    period: Period,
    counts: Counter[str],
    message_counts: Counter[str],
    channel_counts: dict[str, Counter[str]],
) -> bool:
    message_ts = message.get("ts")
    if not isinstance(message_ts, str) or not period.contains_ts(message_ts):
        return False

    found = False
    for reaction in message.get("reactions") or []:
        name = reaction.get("name")
        count = reaction.get("count")
        if not isinstance(name, str) or not isinstance(count, int):
            continue
        counts[name] += count
        message_counts[name] += 1
        channel_counts[channel][name] += count
        found = True
    return found


def load_custom_emoji(client: WebClient) -> dict[str, str]:
    response = client.emoji_list()
    emoji = response.get("emoji") or {}
    return {str(name): str(value) for name, value in emoji.items()}


def resolve_custom_emoji(name: str, custom_emoji: dict[str, str]) -> str:
    value = custom_emoji.get(name, "")
    visited = {name}
    while value.startswith("alias:"):
        alias = value.removeprefix("alias:")
        if alias in visited:
            return ""
        visited.add(alias)
        value = custom_emoji.get(alias, "")
    return value


def write_results(
    output: Path,
    args: argparse.Namespace,
    period: Period,
    counts: Counter[str],
    message_counts: Counter[str],
    channel_counts: dict[str, Counter[str]],
    custom_emoji: dict[str, str],
    scanned_messages: int,
    reacted_messages: int,
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    ranking = []
    for rank, (name, count) in enumerate(counts.most_common(), start=1):
        image_url = resolve_custom_emoji(name, custom_emoji)
        ranking.append(
            {
                "rank": rank,
                "emoji": name,
                "reaction_count": count,
                "message_count": message_counts[name],
                "is_custom": name in custom_emoji,
                "image_url": image_url,
            }
        )

    with (output / "ranking.csv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=ranking[0].keys() if ranking else [
            "rank", "emoji", "reaction_count", "message_count", "is_custom", "image_url"
        ])
        writer.writeheader()
        writer.writerows(ranking)

    markdown = [
        "# Slack絵文字リアクションランキング",
        "",
        "> 指定期間に投稿されたメッセージへ、集計時点で付いていたリアクションです。",
        "",
        "| 順位 | 絵文字 | リアクション数 | 付いたメッセージ数 | カスタム絵文字 |",
        "|---:|---|---:|---:|:---:|",
    ]
    for item in ranking:
        markdown.append(
            f"| {item['rank']} | `:{item['emoji']}:` | {item['reaction_count']} "
            f"| {item['message_count']} | {'○' if item['is_custom'] else '-'} |"
        )
    (output / "ranking.md").write_text("\n".join(markdown) + "\n", encoding="utf-8")

    summary = {
        "definition": (
            "指定期間に投稿されたメッセージへ、集計時点で付いているリアクション数"
        ),
        "since": args.since,
        "until": args.until,
        "timezone": args.timezone,
        "channels": args.channels,
        "excluded_channels": args.excluded_channels,
        "thread_lookback_days": args.thread_lookback_days,
        "scanned_messages": scanned_messages,
        "reacted_messages": reacted_messages,
        "total_reactions": sum(counts.values()),
        "ranking": ranking,
        "by_channel": {
            channel: dict(values.most_common()) for channel, values in channel_counts.items()
        },
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> int:
    args = parse_args()
    # ローカルの.envがあれば読み込む。既存の環境変数は上書きしない。
    load_dotenv(ROOT / ".env")
    token = os.environ.get("SLACK_USER_TOKEN")
    if not token:
        raise SystemExit("SLACK_USER_TOKENが設定されていません")
    validate_user_token(token)
    if args.thread_lookback_days < 0:
        raise SystemExit("--thread-lookback-daysは0以上にしてください")

    try:
        period = build_period(args.since, args.until, args.timezone)
    except (ValueError, KeyError) as error:
        raise SystemExit(f"期間の指定を確認してください: {error}") from error

    client = WebClient(token=token)
    # SlackからHTTP 429が返ったら、Retry-Afterに従って最大5回まで再試行する。
    client.retry_handlers.append(RateLimitErrorRetryHandler(max_retry_count=5))
    channel_names: dict[str, str] = {}
    if args.all_channels:
        accessible_channels = list_accessible_channels(client)
        accessible_channels = apply_channel_exclusions(
            accessible_channels, args.excluded_channels
        )
        channel_names = accessible_channels
        args.channels = list(accessible_channels)
    else:
        excluded_ids = {
            value.removeprefix("#") for value in args.excluded_channels
        }
        args.channels = [
            channel for channel in args.channels if channel not in excluded_ids
        ]
    if not args.channels:
        raise SystemExit("除外後に集計対象となるチャンネルがありません")

    print(f"対象チャンネル: {len(args.channels)}")
    if args.all_channels:
        for channel_id in args.channels:
            channel_name = accessible_channels[channel_id]
            print(f"  #{channel_name} ({channel_id})")
    if args.excluded_channels:
        print(f"除外チャンネル: {', '.join(args.excluded_channels)}")

    custom_emoji = load_custom_emoji(client)
    counts: Counter[str] = Counter()
    message_counts: Counter[str] = Counter()
    channel_counts: dict[str, Counter[str]] = defaultdict(Counter)
    scanned_messages = 0
    reacted_messages = 0
    history_start = period.start - timedelta(days=args.thread_lookback_days)

    total_channels = len(args.channels)
    print("集計を開始します。", flush=True)
    for channel_number, channel in enumerate(args.channels, start=1):
        channel_name = channel_names.get(channel, channel)
        channel_started_at = time_module.monotonic()
        messages_before = scanned_messages
        reactions_before = sum(counts.values())
        print(
            f"[{channel_number}/{total_channels}] #{channel_name} を集計中...",
            flush=True,
        )
        seen_replies: set[str] = set()
        for message in fetch_history(client, channel, history_start, period.end):
            message_ts = message.get("ts")
            if isinstance(message_ts, str) and period.contains_ts(message_ts):
                scanned_messages += 1
                if add_message(
                    message, channel, period, counts, message_counts, channel_counts
                ):
                    reacted_messages += 1

            if message.get("reply_count", 0) and isinstance(message_ts, str):
                for reply in fetch_replies(client, channel, message_ts, period):
                    reply_ts = reply.get("ts")
                    if not isinstance(reply_ts, str) or reply_ts in seen_replies:
                        continue
                    seen_replies.add(reply_ts)
                    scanned_messages += 1
                    if add_message(
                        reply, channel, period, counts, message_counts, channel_counts
                    ):
                        reacted_messages += 1

        channel_elapsed = time_module.monotonic() - channel_started_at
        print(
            f"[{channel_number}/{total_channels}] #{channel_name} 完了 "
            f"メッセージ={scanned_messages - messages_before} "
            f"リアクション={sum(counts.values()) - reactions_before} "
            f"経過={channel_elapsed:.1f}秒",
            flush=True,
        )

    write_results(
        args.output,
        args,
        period,
        counts,
        message_counts,
        channel_counts,
        custom_emoji,
        scanned_messages,
        reacted_messages,
    )
    print(f"確認したメッセージ: {scanned_messages}")
    print(f"リアクション付き: {reacted_messages}")
    print(f"リアクション合計: {sum(counts.values())}")
    print(f"結果: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
