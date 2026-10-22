"""LINE shared expense bot V4.2: trigger-only, flexible bill parsing."""
import logging
import os
import re
import unicodedata
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from flask import Flask, abort, request
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import ApiClient, Configuration, MessagingApi, ReplyMessageRequest, TextMessage
from linebot.v3.webhooks import MessageEvent, TextMessageContent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
app = Flask(__name__)
SECRET = os.getenv('LINE_CHANNEL_SECRET', '')
TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', '')
handler = WebhookHandler(SECRET) if SECRET else None
configuration = Configuration(access_token=TOKEN) if TOKEN else None
CATEGORIES = ('電費', '水費', '瓦斯費', '管理費', '網路費', '清潔費', '房租', '其他費用')
# Match the amount label and then accept punctuation, whitespace, currency symbols, and comma-separated digits.
AMOUNT_RE = re.compile(r'(?:應\s*繳\s*(?:總\s*)?金\s*額|帳\s*單\s*(?:總\s*)?金\s*額|應\s*繳\s*費\s*用|總\s*費\s*用|費\s*用\s*總\s*額|總\s*金\s*額|應\s*繳|金\s*額)\s*[:=：]?\s*(?:NTD|NT\$|TWD|\$)?\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(?:元|塊)?', re.I)
# Require explicit '天' to avoid treating bill amount as a person's days.
PERSON_RE = re.compile(r'(?<![\w])([^\s,:;。\d\-]{1,12})\s*[:：]\s*(\d+(?:\.\d+)?)\s*天')
PERIOD_RE = re.compile(r'共\s*(\d+)\s*天')


def allocate(total, people):
    if total < 0 or total != total.to_integral_value():
        raise ValueError('目前僅支援非負整數的新臺幣帳單金額。')
    if not people:
        raise ValueError('請提供姓名與在家天數，例如：御心：43天。')
    if any(days < 0 for _, days in people):
        raise ValueError('在家天數不可為負數。')
    days_sum = sum((days for _, days in people), Decimal(0))
    if days_sum <= 0:
        raise ValueError('所有人的在家天數合計必須大於 0。')
    exact = [total * days / days_sum for _, days in people]
    result = [int(x.to_integral_value(rounding=ROUND_FLOOR)) for x in exact]
    remainder = int(total) - sum(result)
    for i in sorted(range(len(people)), key=lambda i: (-(exact[i] - result[i]), i))[:remainder]:
        result[i] += 1
    return result


def parse_message(raw):
    normalized = unicodedata.normalize('NFKC', raw).strip()
    if not normalized.startswith('計算費用'):
        return None
    text = normalized[len('計算費用'):].strip()
    if not text:
        return '請在「計算費用」後貼上費用類型、應繳金額及每人的在家天數。'
    category = next((c for c in CATEGORIES if c in text), None)
    if not category:
        return '請提供費用類型，例如：電費、水費或瓦斯費。'
    amount_match = AMOUNT_RE.search(text)
    if not amount_match:
        return '無法辨識應繳金額，請確認包含「應繳金額：4866元」或「應繳金額：4,866元」。'
    try:
        total = Decimal(amount_match.group(1).replace(',', ''))
    except InvalidOperation:
        return '應繳金額格式不正確，請使用數字。'
    # Split at line breaks and Chinese list separators; parse names in each segment.
    # Ignore date lines and labels, and never use the total bill amount as a person's day count.
    people = []
    seen = set()
    for segment in re.split(r'[\n\r、，,;；]+', text):
        segment = segment.strip()
        for m in PERSON_RE.finditer(segment):
            name = m.group(1).strip()
            if any(word in name for word in ('期間', '計費', '應繳', '金額', '總計', '合計', '費用')):
                continue
            if name in seen:
                return f'「{name}」重複出現，請確認每人只輸入一次。'
            seen.add(name)
            people.append((name, Decimal(m.group(2))))
    if not people:
        return '請提供姓名與在家天數，例如：御心：43天、立華：46天。'
    period = PERIOD_RE.search(text)
    if period:
        max_days = Decimal(period.group(1))
        for name, days in people:
            if days > max_days:
                return f'「{name}」的在家天數超過計費期間 {int(max_days)} 天，請確認。'
    try:
        amounts = allocate(total, people)
    except ValueError as exc:
        return str(exc)
    detail = '、'.join(f'{name} ${amount:,}' for (name, _), amount in zip(people, amounts))
    return f'{category}分攤如下：{detail}，合計 ${int(total):,}'


@app.get('/')
def home():
    return 'LINE expense bot V4.2 running', 200


@app.post('/callback')
def callback():
    if handler is None or configuration is None:
        logger.error('Missing LINE credentials')
        return 'Server configuration incomplete', 503
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    except Exception:
        logger.exception('Webhook processing error')
        abort(500)
    return 'OK', 200


if handler:
    @handler.add(MessageEvent, message=TextMessageContent)
    def on_text(event):
        response = parse_message(event.message.text)
        if response is None:
            return
        try:
            with ApiClient(configuration) as api_client:
                MessagingApi(api_client).reply_message(ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=response)]))
        except Exception:
            logger.exception('LINE reply failed')
            raise


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', '10000')))
