"""LINE shared-expense calculator V4.1. Python 3.10+."""
import logging
import os
import re
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
AMOUNT_RE = re.compile(r'(?:應繳(?:總)?金額|帳單(?:總)?金額|總費用|費用總額|應繳|總金額|金額)\s*[：:=]?\s*(?:NT\$|NTD|\$)?\s*([\d,]+(?:\.\d+)?)\s*元?', re.I)
PERSON_RE = re.compile(r'([^\s，,、：:；;。\d]{1,12})\s*[：:]\s*(\d+(?:\.\d+)?)\s*天')
DAYS_RE = re.compile(r'(?:共|計費|期間)\s*(\d+)\s*天')


def calculate(total, people):
    """Largest remainder allocation, deterministic tie-break by input order."""
    if total < 0 or total != total.to_integral_value():
        raise ValueError('目前僅支援非負整數的新臺幣帳單金額。')
    if not people:
        raise ValueError('找不到姓名與在家天數。')
    total_days = sum((days for _, days in people), Decimal('0'))
    if total_days <= 0:
        raise ValueError('所有人的在家天數合計必須大於 0。')
    shares = [total * days / total_days for _, days in people]
    floors = [int(x.to_integral_value(rounding=ROUND_FLOOR)) for x in shares]
    remainder = int(total) - sum(floors)
    order = sorted(range(len(people)), key=lambda i: (-(shares[i] - floors[i]), i))
    for i in order[:remainder]:
        floors[i] += 1
    return floors


def parse_message(raw):
    # Only messages beginning with the exact trigger phrase receive a reply.
    if not raw.strip().startswith('計算費用'):
        return None
    raw = raw.strip()[len('計算費用'):].strip()
    if not raw:
        return '請在「計算費用」後貼上費用類型、應繳金額及每人的在家天數。'
    text = raw.replace('：', ':').replace('，', ',').replace('－', '-').replace('—', '-').replace('–', '-')
    category = next((c for c in CATEGORIES if c in text), None)
    if not category:
        return '請提供費用類型，例如：電費、水費或瓦斯費。'
    m = AMOUNT_RE.search(text)
    if not m:
        return '請提供應繳金額，例如：應繳金額：4866元。'
    try:
        total = Decimal(m.group(1).replace(',', ''))
    except InvalidOperation:
        return '應繳金額格式不正確，請使用數字。'
    people = []
    seen = set()
    for name, days_str in PERSON_RE.findall(text):
        name = name.strip()
        # Remove a possible list ordinal like "1." before a name.
        if name in seen:
            return f'「{name}」重複出現，請確認每人只輸入一次。'
        seen.add(name)
        days = Decimal(days_str)
        people.append((name, days))
    if not people:
        return '請提供姓名與在家天數，例如：御心：43天、立華：46天。'
    period = DAYS_RE.search(text)
    if period:
        max_days = Decimal(period.group(1))
        for name, days in people:
            if days > max_days:
                return f'「{name}」的在家天數超過計費期間 {int(max_days)} 天，請確認。'
    try:
        amounts = calculate(total, people)
    except ValueError as exc:
        return str(exc)
    items = '、'.join(f'{name} ${amount:,}' for (name, _), amount in zip(people, amounts))
    return f'{category}分攤如下：{items}，合計 ${int(total):,}'


@app.get('/')
def home():
    return 'LINE expense bot V4.1 running', 200


@app.post('/callback')
def callback():
    if handler is None or configuration is None:
        logger.error('Missing LINE_CHANNEL_SECRET or LINE_CHANNEL_ACCESS_TOKEN')
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
                MessagingApi(api_client).reply_message(
                    ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=response)])
                )
        except Exception:
            logger.exception('LINE reply failed: verify LINE_CHANNEL_ACCESS_TOKEN and channel permissions')
            raise


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', '10000')))
