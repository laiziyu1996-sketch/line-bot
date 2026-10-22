

import os

import logging

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

from linebot.v3.webhooks import (

    MessageEvent,

    TextMessageContent

)

app = Flask(__name__)

logging.basicConfig(level=logging.INFO)

CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")

CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")

if not CHANNEL_SECRET or not CHANNEL_ACCESS_TOKEN:

    raise RuntimeError("LINE credentials are missing")

handler = WebhookHandler(CHANNEL_SECRET)

configuration = Configuration(

    access_token=CHANNEL_ACCESS_TOKEN

)

@app.route("/", methods=["GET"])

def home():

    return "LINE Bot is running!", 200

@app.route("/callback", methods=["POST"])

def callback():

    signature = request.headers.get("X-Line-Signature", "")

    body = request.get_data(as_text=True)

    try:

        handler.handle(body, signature)

    except InvalidSignatureError:

        abort(400)

    except Exception:

        app.logger.exception("Webhook error")

        abort(500)

    return "OK", 200

@handler.add(MessageEvent, message=TextMessageContent)

def handle_message(event):

    user_text = event.message.text.strip()

    # Keyword replies

    if user_text in ["你好", "嗨", "Hi", "hi"]:

        reply = "你好！我是你的 LINE 機器人。"

    elif user_text == "幫助":

        reply = (

            "目前支援以下指令：\n"

            "你好：打招呼\n"

            "功能：查看功能\n"

            "測試：測試機器人\n"

            "幫助：顯示指令"

        )

    elif user_text == "功能":

        reply = (

            "目前功能：\n"

            "1. 自動回覆\n"

            "2. 關鍵字指令\n"

            "3. 群組訊息處理\n"

            "4. Webhook 連線"

        )

    elif user_text == "測試":

        reply = "測試成功！機器人正常運作。"

    else:

        # Avoid replying to every group message

        if event.source.type == "group":

            return

        reply = (

            "我收到你的訊息了！\n"

            "輸入「幫助」查看可用指令。"

        )

    with ApiClient(configuration) as api_client:

        line_bot_api = MessagingApi(api_client)

        line_bot_api.reply_message(

            ReplyMessageRequest(

                reply_token=event.reply_token,

                messages=[TextMessage(text=reply)]

            )

        )

if __name__ == "__main__":

    port = int(os.environ.get("PORT", 10000))

    app.run(host="0.0.0.0", port=port)
