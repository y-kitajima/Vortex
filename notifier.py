# =============================================================================
# notifier.py ― Discordへの配信を担当
# =============================================================================
#
# 【このファイルの責務】
#   「記事リスト → Discordのembedという形に組み立てて、Webhookに届ける」ことだけ。
#   将来 Slack やメールに配信先を増やすなら、似た作りの notifier_slack.py を
#   追加すれば良く、他ファイルは変更不要です（責務分割の利点）。
#
#   ※注意: ファイル名を discord.py にしてはいけません。
#   PyPIの人気ライブラリ「discord.py」と名前衝突し、
#   将来そのライブラリを入れたときに自分自身を import して壊れます。
#
# 【Webhookの仕組み】
#   DiscordのWebhook URLに対して、HTTPという通信のPOSTという手段で
#   「JSON（構造化されたテキスト）」を送ると、そのチャンネルに投稿が現れます。
#   送信データは以下のような形です:
#
#     {
#       "embeds": [                       ... embed（リッチ表示の枠）のリスト
#         {
#           "title":  "【Gigazine】",      ... 見出し
#           "color":  28470,               ... 枠の色（16進数の数字）
#           "fields": [                     ... 中身の項目リスト
#             { "name": "記事タイトル", "value": "https://..." },
#             ...
#           ]
#         }
#       ]
#     }
#
#   Pythonの辞書（dict）はこのJSON形式と相性が良く、
#   requests.post(..., json=...) と渡すと自動でJSON文字列に変換されます。
#
# =============================================================================

# requests: WebにHTTPリクエストを送る外部ライブラリ
import requests


# --- Discordの仕様による上限（これを超えると投稿がエラー=HTTP 400になる） ---
# 【重要】Discordの制限は「個数」だけでなく「文字数」にもあります。
#   embed 1枚の合計文字数（title + 全fieldの name + value の文字数合計）は
#   6000文字以下。メッセージ1通に入れるembedの合計も6000文字以下です。
#   個数だけで分割すると長いタイトルで容易に突破するため、
#   「文字数を見積もりながら積み、上限に達したら次の枠へ」とします。
MAX_FIELDS_PER_EMBED = 25      # embed 1枚に入れられる field の個数上限
MAX_EMBEDS_PER_MESSAGE = 10    # メッセージ1通に入れられる embed の枚数上限
MAX_FIELD_NAME_LENGTH = 256    # field の name（記事タイトル）の文字数上限
MAX_FIELD_VALUE_LENGTH = 1024  # field の value（URL）の文字数上限
MAX_EMBED_CHARS = 6000         # embed 1枚・メッセージ1通の合計文字数上限
CHAR_MARGIN = 100              # 上限で事故らないための安全マージン


def _make_embed(label: str, fields: list, part: int) -> dict:
    """
    field のリストから embed（1枚のリッチ表示）を作る。

    ※名前の前に _（アンダースコア）が付いている関数は「このファイル専用の小道具」
    という合図です。他ファイルからは呼ばない前提の命名というPython慣習です。
    """
    title = f"【{label}】" if part == 0 else f"【{label}】（続き{part}）"
    return {
        "title": title,
        "color": 0x00A67D,   # 16進数の色指定（緑系）。Discordの枠の色になります
        "fields": fields,
    }


def _embed_chars(embed: dict) -> int:
    """
    embed 1枚がDiscord上で「何文字扱いされるか」の見積もりを返す。

    Discordは文字数ベースで上限判定します。
    title + 全fieldの name + value の文字数の合計が目安になります。
    """
    total = len(embed["title"])
    for field in embed["fields"]:
        total += len(field["name"]) + len(field["value"])
    return total


def build_embeds(new_articles: list) -> list:
    """
    新着記事リストを、Discord embed の形に変換する。

    引数:
        new_articles: models.Article のリスト
                      （feeds.py が正規化したdataclassの箱）
    戻り値:
        Discordが要求する形式の embed（辞書）のリスト

    【dataclass化で変わったこと】
        dictのとき: article["title"]（キー名を毎回書く／打ち間違い無警告）
        Articleとき: article.title  （エディタ補完が効く／打ち間違いは即エラー）
        見出しのグループ化は「トピック表示名」でまとめます。

    【分割の理屈（2つの上限を両方守る）】
        記事を1件ずつ今のembedに積みながら、どちらかに当たったら新しいembedへ:
          a) field が 25個に達した場合
          b) 次の記事を積むと合計文字数が6000（−安全マージン）を超える場合
    """
    # ------------------------------------------------------------------
    # ステップ1: トピック表示名ごとに記事をまとめる
    # ------------------------------------------------------------------
    # setdefault(キー, 初期値) は「キーが無ければ初期値をセットして、
    # 既にあればそのまま」。これで「トピック → そのトピックの記事リスト」を作ります。
    by_label = {}
    for article in new_articles:
        by_label.setdefault(article.topic_label, []).append(article)

    # ------------------------------------------------------------------
    # ステップ2: 文字数を見ながら embed に積み上げる
    # ------------------------------------------------------------------
    embeds = []
    for label, items in by_label.items():
        fields = []            # 今積んでいるembedのfield達
        size = len(label) + 4  # embedのタイトル【label】分の文字数を先に予約
        part = 0               # 続き番号

        for article in items:
            # Discordの個別上限（name 256 / value 1024）で先に切る。
            # nameは「出典ドメイン: タイトル」の形にすると、同じトピックに
            # 複数サイトが混ざったとき見分けやすいです。
            title = article.title or "(無題)"
            name = f"{article.source}: {title}"[:MAX_FIELD_NAME_LENGTH]
            value = article.link[:MAX_FIELD_VALUE_LENGTH]

            # 「今のembedが空でない」かつ「これ以上積むと上限オーバー」なら
            # 今のembedを確定して、新しいembedを始めます。
            if fields and (
                len(fields) >= MAX_FIELDS_PER_EMBED
                or size + len(name) + len(value) > MAX_EMBED_CHARS - CHAR_MARGIN
            ):
                embeds.append(_make_embed(label, fields, part))
                part += 1
                fields = []
                size = len(label) + 4  # 新embedのタイトル分をリセット

            fields.append({"name": name, "value": value})
            size += len(name) + len(value)

        # ループ終了時に残ったfield達もembedとして確定します。
        if fields:
            embeds.append(_make_embed(label, fields, part))

    return embeds


def send_to_discord(webhook_url: str, embeds: list) -> None:
    """
    embedのリストをDiscord Webhookへ送信する。

    【HTTP POSTとは】
        Webサーバーに「データを送り付けて処理させる」通信の方法です。
        サーバーは処理結果をステータスコードという数字で返します。
        2xx = 成功（Webhookは 204 No Content）、4xx/5xx = エラー。

    【メッセージ単位の分割】
        embed自体は6000文字以内に収まるよう build_embeds で作られています。
        ただし「メッセージ1通に入れるembedの合計」も6000文字以下が必要なため、
        枚数・文字数のどちらかを超えたら送信して箱をリセットします。
    """
    message = []  # 1通にまとめるembed達をためる箱

    for embed in embeds:
        # 今の message に次の embed を追加できるか判定します。
        current_chars = sum(_embed_chars(e) for e in message)
        if message and (
            len(message) >= MAX_EMBEDS_PER_MESSAGE
            or current_chars + _embed_chars(embed) > MAX_EMBED_CHARS
        ):
            # 追加できない → 今の message を1通として送ってリセット
            _post(webhook_url, message)
            message = []

        message.append(embed)

    # ループ終了後、送られていない残りがあります（0枚なら _post しません）。
    if message:
        _post(webhook_url, message)


def _post(webhook_url: str, embeds: list) -> None:
    """embedのリストを1通としてWebhookにPOSTする（送信の最下層の部品）。"""
    payload = {"embeds": embeds}

    # requests.post(url, json=...) がPOST送信本体です。
    # json= は辞書をJSON文字列に変換して送り、適切なヘッダーも自動で付きます。
    # timeout=20 は「20秒応答がなければエラー」という安全装置（無限待ち受け防止）。
    resp = requests.post(webhook_url, json=payload, timeout=20)

    # raise_for_status() は、ステータスコードが4xx/5xxなら例外（HTTPError）を起こします。
    # 「失敗したのに成功と勘違いする」事故を防ぐお約束の一行です。
    # 例外が起きれば呼び出し元（main）の try/except で受け止められます。
    resp.raise_for_status()
