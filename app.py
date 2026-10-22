

import os

import re

import logging

from decimal import Decimal, ROUND_HALF_UP

from flask import Flask, request, abort

from linebot.v3 import WebhookHandler

from linebot.v3.exceptions import InvalidSignatureError

from linebot.v3.messaging import (

    Configuration,

    ApiClient,

    MessagingApi,

    ReplyMessageRequest,

    TextMessage

)

from linebot.v3.webhooks import MessageEvent, TextMessageContent

logging.basicConfig(level=logging.INFO)

logger = logging.getLogger(__name__)

app = Flask(__name__)

CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")

CHANNEL_ACCESS_TOKEN = os.environ.get(

    "LINE_CHANNEL_ACCESS_TOKEN", ""

)

handler = WebhookHandler(CHANNEL_SECRET)

configuration = Configuration(

    access_token=CHANNEL_ACCESS_TOKEN

)

def normalize(text):

    text = text.translate(str.maketrans(

        "０１２３４５６７８９",

        "0123456789"

    ))

    text = text.replace("，", ",")

    text = text.replace("：", ":")

    text = text.replace("＄", "$")

    text = text.replace("（", "(").replace("）", ")")

    text = text.replace("　", " ")

    return text

def extract_amount(text):

    patterns = [

        r"應繳\s*金額\s*[:：]?\s*[$]?\s*([\d,]+)",

        r"總金額\s*[:：]?\s*[$]?\s*([\d,]+)",

        r"應繳\s*[$]?\s*([\d,]+)"

    ]

    for pattern in patterns:

        match = re.search(pattern, text)

        if match:

            return int(match.group(1).replace(",", ""))

    return None

def extract_people(text):

    people = []

    seen = set()

    # 每筆資料以編號、姓名、天數開頭

    pattern = (

        r"(?:^|[\n。])\s*"

        r"(?:\d+\s*[.、．]\s*)?"

        r"([\u4e00-\u9fffA-Za-z]{2,15})"

        r"\s*[:：]?\s*"

        r"(\d+)\s*天"

    )

    for match in re.finditer(pattern, text):

        name = match.group(1).strip()

        days = int(match.group(2))

        if name in seen:

            continue

        if days < 0 or days > 366:

            continue

        seen.add(name)

        people.append((name, days))

    return people

def calculate_bill(amount, people):

    total_days = sum(days for _, days in people)

    if total_days == 0:

        return None

    exact = [

        Decimal(amount) * Decimal(days)

        / Decimal(total_days)

        for _, days in people

    ]

    # 一般四捨五入

    rounded = [

        int(value.quantize(

            Decimal("1"),

            rounding=ROUND_HALF_UP

        ))

        for value in exact

    ]

    difference = amount - sum(rounded)

    # 尾差調整：優先調整小數部分最大者

    if difference > 0:

        order = sorted(

            range(len(people)),

            key=lambda i: (

                exact[i] - int(exact[i]),

                exact[i],

                -i

            ),

            reverse=True

        )

        for n in range(difference):

            rounded[order[n % len(order)]] += 1

    elif difference < 0:

        order = sorted(

            range(len(people)),

            key=lambda i: (

                exact[i] - int(exact[i]),

                exact[i],

                -i

            )

        )

        for n in range(-difference):

            rounded[order[n % len(order)]] -= 1

    return rounded

def process_bill(text):

    text = normalize(text)

    if not text.strip().startswith("計算帳單"):

        return (

            "請輸入「計算帳單」加上帳單資料。\n"

            "例如：\n"

            "計算帳單\n"

            "電費計費期間：115/08/11—115/10/12\n"

            "應繳金額：$4866\n"

            "1.琇淇：45天\n"

            "2.御心：43天\n"

            "3.婉瑜：48天"

        )

    text = text.strip()[4:].strip()

    # 支援同一則訊息包含多種帳單

    sections = re.split(

        r"(?=(?:電費|水費|瓦斯費|管理費)計費期間)",

        text

    )

    sections = [s.strip() for s in sections if s.strip()]

    results = []

    for section in sections:

        amount = extract_amount(section)

        people = extract_people(section)

        if amount is None:

            continue

        if not people:

            continue

        shares = calculate_bill(amount, people)

        if shares is None:

            continue

        bill_type = "帳單"

        for name in ["電費", "水費", "瓦斯費", "管理費"]:

            if name in section:

                bill_type = name

                break

        period_match = re.search(

            r"(\d{2,3}/\d{1,2}/\d{1,2})\s*"

            r"[-—–~～至]+\s*"

            r"(\d{2,3}/\d{1,2}/\d{1,2})",

            section

        )

        lines = [f"【{bill_type}分攤結果】"]

        if period_match:

            lines.append(

                f"計費期間：{period_match.group(1)}"

                f"～{period_match.group(2)}"

            )

        lines.append(f"應繳總額：${amount:,}")

        lines.append(

            f"在家天數合計："

            f"{sum(days for _, days in people)}天"

        )

        lines.append("")

        for (name, days), share in zip(people, shares):

            lines.append(

                f"{name}：{days}天，應繳${share:,}"

            )

        lines.append("")

        lines.append(f"分攤合計：${sum(shares):,}")

        lines.append("計算方式：依在家天數比例分攤")

        results.append("\n".join(lines))

    if not results:

        return (

            "無法辨識帳單資料。\n"

            "請確認有輸入：\n"

            "1. 應繳金額\n"

            "2. 每個人的姓名\n"

            "3. 每個人的在家天數\n\n"

            "例如：\n"

            "計算帳單\n"

            "電費計費期間：115/08/11—115/10/12\n"

            "應繳金額：$4866\n"

            "1.琇淇：45天\n"

            "2.御心：43天"

        )

    return "\n\n".join(results)

@app.route("/", methods=["GET"])

def home():

    return "LINE Bill Calculator is running!", 200

@app.route("/callback", methods=["POST"])

def callback():

    signature = request.headers.get(

        "X-Line-Signature", ""

    )

    body = request.get_data(as_text=True)

    try:

        handler.handle(body, signature)

    except InvalidSignatureError:

        abort(400)

    except Exception:

        logger.exception("Webhook error")

        abort(500)

    return "OK", 200
 @handler.add(MessageEvent, message=TextMessageContent)

def handle_message(event):

    user_text = event.message.text

    try:

        reply_text = process_bill(user_text)

    except Exception:

        logger.exception("Calculation error")

        reply_text = "計算發生錯誤，請檢查資料格式。"

    with ApiClient(configuration) as api_client:

        line_bot_api = MessagingApi(api_client)

        line_bot_api.reply_message(

            ReplyMessageRequest(

                reply_token=event.reply_token,

                messages=[

                    TextMessage(text=reply_text[:4900])

                ]

            )

        )

if __name__ == "__main__":

    port = int(os.environ.get("PORT", 10000))

    app.run(host="0.0.0.0", port=port)
