# =============================================================================
# feeds.py ― RSSフィードの取得と記事の「正規化」を担当
# =============================================================================
#
# 【このファイルの責務】
#   1. RSSフィードをHTTPで取得する（失敗しても例外を投げず警告を返す）
#   2. 外部データ（RSS）のバラバラな形を、models.Article の箱に変換する（正規化）
#
# 【今回の変化: dict → dataclass】
#   これまで記事は {"title": ..., "link": ...} のdictで返していました。
#   これからは models.Article として返します。呼び出し側は
#   article.title / article.link と「属性名」で取り出します。
#   キーの打ち間違いが構造的に起きなくなり、
#   「Articleには何が入っているか」が models.py 一読で分かります。
#
# 【失敗に強い設計】
#   外部のWebは必ず壊れます（サイト障害・URL変更・アクセス拒否403…）。
#   1つのURLの失敗でアプリ全体が落ちると「お気に入りサイトが1つ落ちた日」に
#   他の新着も全部取れなくなってしまいます。
#   そこで fetch_feed は失敗しても例外を外に投げず、
#   (記事のリスト, 警告メッセージ) のタプルで結果を返します。
#
# =============================================================================

# calendar: 日付の構造体を「UNIX秒」に変換する標準モジュール
import calendar

# urlparse: URLを部品（プロトコル・ドメイン・パス）に分解する標準モジュール
from urllib.parse import urlparse

# feedparser: RSSフィード（XML形式のデータ）を解析する外部ライブラリ
import feedparser

# requests: HTTPリクエストを送る外部ライブラリ
import requests

# models: このアプリ共通のデータの箱（Topic / Article）の定義
import models


def _to_epoch(entry) -> float | None:
    """
    RSS entry の中の「公開日時」をUNIX秒（float）に変換する。

    戻り値:
        UNIX秒   ... 日付が取得できた場合
        None     ... RSSに日付が無い場合（後段で「新しめ」として扱う）

    【日付が struct_time という「分解された状態」で来る】
        feedparser は published_parsed / updated_parsed というキーに
        (年, 月, 日, 時, 分, 秒, ...) のタプルをくれます。
        calendar.timegm() がそれを「1970年からの秒数」に換算してくれます。
        数字にすると「◯時以降か？」という比較が簡単になるのが利点です。

    【published と updated のどちらを使う？】
        「掲載された時刻(published)」を優先し、無ければ「更新時刻(updated)」を
        使う、という順で拾います（すべてのRSSが両方持つとは限らないため）。
    """
    dt = entry.get("published_parsed") or entry.get("updated_parsed")
    return calendar.timegm(dt) if dt else None


def fetch_feed(topic: models.Topic, url: str) -> tuple[list, str | None]:
    """
    1つのRSSフィードを取得し、Article のリストに正規化して返す。

    引数:
        topic: このフィードが所属する Topic（情報源ではなく「まとめ先」の情報）
        url:   RSSフィードのURL
    戻り値:
        (articles, warning) のタプル
            articles: Article のリスト（失敗時は空リスト []）
            warning : 失敗・異常時の警告文字列。正常時は None

    【なぜ feedparser.parse(url) ではなく requests で取得するのか】
        feedparser はURLを直接渡されると、HTTPが失敗しても例外を投げず
        「記事0件」として黙って返してしまうことがあります。
        これだと「本当に0件のサイト」なのか「取得に失敗した」のか区別できません。
        そこで HTTP取得だけは requests に任せて raise_for_status で明示的に
        失敗を検出し、XML解析だけ feedparser に任せる、という分担にしています。
        （requestsに渡すのは resp.content = 生のバイト列です。
          feedparserはバイトを渡すとXML内の文字コード宣言を尊重してくれるため
          日本語が壊れにくい、というおまけもあります）
    """
    # ------------------------------------------------------------------
    # 1. HTTP取得（失敗は例外 → ここで受け止めて警告に変換）
    # ------------------------------------------------------------------
    try:
        resp = requests.get(url, timeout=20)
        # ステータスコードが4xx/5xxなら HTTPError 例外を起こす
        resp.raise_for_status()
    except requests.RequestException as e:
        # タイムアウト・DNS失敗・403拒否など、HTTP由来の失敗は全部この親例外。
        # type(e).__name__ で「どの種類の失敗か」を警告文に含めて診断しやすくします。
        return [], f"取得失敗({type(e).__name__}): {url}"

    # ------------------------------------------------------------------
    # 2. XML解析と正規化（RSSのentry → Article の箱）
    # ------------------------------------------------------------------
    parsed = feedparser.parse(resp.content)

    # URLからドメイン（例: https://a.b/c → "a.b"）を取り出しておきます。
    # 同じトピックに複数サイトが混ざったとき「どこのサイトの記事か」の
    # 目印として Article.source に入れます。
    host = urlparse(url).netloc

    articles = []
    for entry in parsed.entries:
        # entry.get("キー", 既定値) で取り出す。
        # RSSによっては title が無かったり link が無かったりするので、
        # ここで「無い場合」を吸収しておくのが正規化の仕事です。
        title = entry.get("title", "タイトルなし")
        link = (entry.get("link") or "").strip()

        # URLが空の記事はスキップ。
        # 理由: linkは「既読管理のキー」かつ「Discordに出すリンク」なので、
        # これが無い記事はこのアプリで何も扱えません。
        if not link:
            continue

        # Article 箱に詰める。ここで一度「型」に通すので、
        # 必須項目の欠落はここで気づけます（dataclassの利点）。
        articles.append(models.Article(
            topic_key=topic.key,
            topic_label=topic.label,
            title=title,
            link=link,
            source=host,
            published=_to_epoch(entry),
        ))

    # 取得はできたのに記事が1件も無い場合も、設定ミスの手がかりとして警告にします。
    if not articles:
        return [], f"記事なし: {url}"

    return articles, None
