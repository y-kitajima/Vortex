# =============================================================================
# settings.py ― 設定ファイル（config.yaml）を読み込む担当
# =============================================================================
#
# 【このファイルの責務（役割）】
#   「config.yaml を読んで、他の部品がそのまま使える形の辞書にする」ことだけ。
#
# ファイルを分割する原則は「変更する理由が同じものは同じファイル」です。
# フィードのURLを変えたり保持日数を変えたりするのは「設定の変化」なので、
# その変化に対応するのはこのファイルだけ、という作りにしています。
#
# 【今回の変化: トピック構造】
#   config.yaml の feeds 直列リストが、topics の入れ子構造に進化しました。
#   YAMLの入れ子はPythonの辞書の入れ子として読まれるので、
#       raw["topics"]["tech_news"]["feeds"]
#   のように辿って取り出します。
#   ここで「辞書の生データ」を models.py の Topic という dataclass の箱に
#   詰め替えて返すのがこのファイルの仕事です。
#   以降の他のファイルは Topic.label / Topic.feeds と「名前」でアクセスでき、
#   キー名の文字列を何度も書きません。
#
# =============================================================================

# yaml: YAML形式の設定ファイルを読み込む外部ライブラリ
import yaml

# models: 自分のフォルダ内の models.py（Article と Topic の箱の定義）
import models


def load(path: str = "config.yaml") -> dict:
    """
    config.yaml を読み、正規化済みの設定辞書を返す。

    引数:
        path: 設定ファイルの場所（既定では "config.yaml"）
    戻り値:
        {
            "topics": [Topic(...), ...],       ... トピック一覧（dataclass）
            "retention_days": 30,              ... 既読保持日数（整数保証）
            "window_hours": 24,                ... 対象記事の時間窓（整数保証）
            "max_items_per_topic": 10,         ... 1トピック最大件数（整数保証）
            "webhook_url": "https://...",      ... 前後空白なしの文字列保証
        }

    【この関数の価値は「正規化（せいしか）」】
        YAMLをそのまま読むと「キーが無い」「None が混じる」「文字列に空白が付く」
        といった揺らぎがあります。それをここで吸収して、
        「returnされた値は形が約束通りである」という状態にします。
        こうすれば後続の他のファイル（store, feeds, notifier）は
        「無い場合」を一切心配しなくて済みます。
    """
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    # raw は例えば次のような辞書です（YAMLの入れ子がそのままPythonになります）:
    # {
    #     "retention_days": 30,
    #     "window_hours": 24,
    #     "discord": {"webhook_url": "https://discord.com/api/webhooks/..."},
    #     "topics": {
    #         "tech_news": {"label": "テックニュース", "feeds": ["https://...", ...]},
    #         ...
    #     }
    # }

    # raw.get("キー", 既定値) は「キーが無ければ既定値を使う」取り出し方。
    # int(...) は「"30"のような文字列でも整数 30 に揃える」変換です。
    retention_days = int(raw.get("retention_days", 30))
    window_hours = int(raw.get("window_hours", 24))
    max_items = int(raw.get("max_items_per_topic", 10))

    # config.get("discord") が無い場合は None になる可能性があるので、
    # `or {}` で「無いなら空辞書」に正規化してから中身を取ります。
    # .strip() は文字列の前後の空白（URLコピペで紛れみやすい）を取ります。
    webhook_url = ((raw.get("discord") or {}).get("webhook_url") or "").strip()

    # ------------------------------------------------------------------
    # topics の読み込み: 辞書の入れ子 → Topic のリストへ
    # ------------------------------------------------------------------
    # raw["topics"] は {key名: {"label": ..., "feeds": [...]}, ...} という辞書。
    # .items() は (キー, 値) の組を1つずつ取り出すイテレータです。
    topics = []
    for key, node in (raw.get("topics") or {}).items():
        # feeds を [str, str, ...] に正規化。
        # YAMLのURLは数値や None に化けることがあるので str() で文字列保証します。
        feeds = [str(u).strip() for u in (node.get("feeds") or [])]

        # label が無ければ key をそのまま表示名にする（設定の負担を減らす）
        label = str(node.get("label") or key)

        # models.py の Topic 箱に詰める。以降は topic.feeds / topic.label で扱う。
        topics.append(models.Topic(key=key, label=label, feeds=feeds))

    # トピックが1つも無いとアプリに仕事が無いため、ここで明確に止めます。
    # （設定ミスは起動時に派手に見せる方が「直したい」に対応できます）
    if not topics:
        raise ValueError(
            "config.yaml の topics に1つ以上のトピックを書いてください。"
            "（古い feeds: 直列形式からの変更が必要な場合はリポジトリのテンプレを参照）"
        )

    # 正規化の結果をまとめて返します。以降のコードはこの辞書だけを見ます。
    return {
        "topics": topics,
        "retention_days": retention_days,
        "window_hours": window_hours,
        "max_items_per_topic": max_items,
        "webhook_url": webhook_url,
    }
