# Slack Reaction Counter

指定期間に投稿されたSlackメッセージへ、集計時点で付いている絵文字リアクションを数えます。

## この集計で分かること

- 絵文字ごとのリアクション数
- その絵文字が付いたメッセージ数
- 通常の絵文字か、ワークスペースのカスタム絵文字か
- チャンネル別の件数

例えば8月1日から8月31日を指定した場合、8月中に投稿されたメッセージのリアクションを数えます。

## Slackアプリを作る

1. [Slack APIのYour Apps](https://api.slack.com/apps)を開く
2. `Create New App`を選ぶ
3. `From an app manifest`を選ぶ
4. 対象のワークスペースを選ぶ
5. `slack-app-manifest.yml`の内容を貼り付ける
6. アプリを作成し、`Install to Workspace`を実行する
7. `User OAuth Token`を控える

`Bot User OAuth Token`ではありません。Bot Tokenは通常`xoxb-`、今回使うUser OAuth Tokenは通常`xoxp-`から始まります。

マニフェストで要求する権限は次の5つです。

- `channels:history`: 公開チャンネルの投稿を読む
- `channels:read`: 公開チャンネルの一覧を読む
- `groups:history`: 非公開チャンネルの投稿を読む
- `groups:read`: 非公開チャンネルの一覧を読む
- `emoji:read`: カスタム絵文字の一覧を読む

すべてUser Tokenの読み取り権限です。投稿やリアクションを変更する権限は要求しません。

すでに以前のマニフェストでSlackアプリを作成済みの場合、ローカルの`slack-app-manifest.yml`を変更しただけではSlack側へ反映されません。

Slackアプリの`App Manifest`を開いて、このリポジトリのマニフェストへ更新します。保存後、`OAuth & Permissions`の`User Token Scopes`に上記5つが表示されていることを確認し、ワークスペースへ再インストールします。

## 準備する

```bash
cd /path/to/slack-reaction-counter
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

`.env.example`をコピーして、ローカル用の`.env`を作ります。

```bash
cp .env.example .env
```

`.env`へUser OAuth Tokenを設定します。

```dotenv
SLACK_USER_TOKEN=YOUR_USER_TOKEN
```

`.env`は`.gitignore`へ登録済みです。GitHubへ公開するのは、値が入っていない`.env.example`だけです。

### `not_in_channel`になった場合

`SLACK_USER_TOKEN`へ`xoxb-`から始まるBot Tokenを設定していないか確認してください。

Bot Tokenでは、Botが参加していないチャンネルの履歴を取得すると`not_in_channel`になります。

以前のマニフェストで作成したアプリの場合は、`App Manifest`を現在の`slack-app-manifest.yml`へ更新して再インストールします。その後に発行されたUser OAuth Tokenを`.env`へ設定します。

## 集計する

トークンを発行した本人が参加している公開・非公開チャンネルを集計します。

```bash
python3 count_reactions.py \
  --all-channels \
  --since 2026-08-01 \
  --until 2026-08-31
```

各チャンネルへBotを招待する必要はありません。本人が参加していないチャンネルは集計しません。

実行中は、チャンネルごとに進捗を表示します。

```text
[1/95] #channel-a を集計中...
[1/95] #channel-a 完了 メッセージ=123 リアクション=456 経過=2.3秒
[2/95] #channel-b を集計中...
```

チャンネル数やメッセージ数が多い場合は、完了まで数分以上かかることがあります。

### 一部のチャンネルを除外する場合

集計したくないチャンネルは、チャンネル名を`--exclude-channel`で指定します。複数ある場合は繰り返し指定できます。先頭の`#`は付けても付けなくても構いません。

```bash
python3 count_reactions.py \
  --all-channels \
  --exclude-channel channel-a \
  --exclude-channel channel-b \
  --since 2026-08-01 \
  --until 2026-08-31
```

チャンネルIDが分かる場合は、IDでも除外できます。

### チャンネルを限定する場合

Slackで対象チャンネルのリンクをコピーすると、URLの中に`C`などで始まるチャンネルIDがあります。

```bash
python3 count_reactions.py \
  --channel C0123456789 \
  --since 2026-08-01 \
  --until 2026-08-31
```

複数チャンネルへ限定する場合は`--channel`を繰り返します。

```bash
python3 count_reactions.py \
  --channel C0123456789 \
  --channel C9876543210 \
  --since 2026-08-01 \
  --until 2026-08-31
```

スレッドの親投稿が期間より前にある場合に備え、初期設定では30日前まで遡って親投稿を探します。

```bash
python3 count_reactions.py \
  --channel C0123456789 \
  --since 2026-08-01 \
  --until 2026-08-31 \
  --thread-lookback-days 90
```

## 出力

`results/`へ3つのファイルを作ります。

- `ranking.csv`: 表計算ソフトで扱えるランキング
- `ranking.md`: そのまま確認できるMarkdown
- `summary.json`: 集計条件を含む全結果

トークンや集計結果を外部へ送信する処理はありません。

`results/`には、チャンネルID、カスタム絵文字の名前や画像URLなど、ワークスペース固有の情報が含まれます。そのため、`.env`と同じく`.gitignore`で除外しています。

`--output`で別の出力先を指定した場合は、そのディレクトリもGitの管理対象に入っていないことを確認してください。

## GitHubへ公開する前の確認

```bash
git status --short
git check-ignore -v .env results/summary.json
```

`.env`と`results/`のファイルが`git status`へ表示されないことを確認します。`.env.example`には本物のトークンを入れません。

## 参考にした記事

- [Slack APIで本気集計！最も使われた絵文字TOP20はコレ！](https://note.com/moonx/n/na19516d24bbe)
- [ロボペイSlackリアクションランキング 集計してみた](https://tech.robotpayment.co.jp/entry/2024/05/16/070000)

公開チャンネルを巡回してメッセージの`reactions`を数える流れを参考にしました。このプログラムでは、ページネーションとレート制限への再試行に加え、スレッド内の返信も集計します。
