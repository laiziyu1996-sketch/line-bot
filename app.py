import base64
import hashlib
import hmac
import json
import logging
import os
import re
import urllib.error
import urllib.request
from decimal import Decimal, ROUND_FLOOR
from flask import Flask, request, jsonify

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)


def calculate(text):
    amount_match = re.search(r'(?:應繳金額|總金額|金額|總費用)\s*[：:]?\s*[＄$]?\s*([\d,]+)', text)
    if not amount_match:
        return '請提供應繳金額，例如：應繳金額：4866元。'
    amount = int(amount_match.group(1).replace(',', ''))
    people = []
    for line in text.splitlines():
        match = re.search(r'^\s*(?:\d+[.、．]\s*)?([^：:\s，,。]+)\s*[：:]\s*(\d+)\s*天', line)
        if match:
            people.append((match.group(1), int(match.group(2))))
    if not people:
        return '請逐行輸入姓名與在家天數，例如：\n1.御心：43天\n2.立華：46天'
    total_days = sum(days for _, days in people)
    if total_days == 0:
        return '在家天數合計不可為零。'
    # Largest remainder method: exact sum and deterministic tie-breaking.
    shares = [Decimal(amount) * Decimal(days) / Decimal(total_days) for _, days in people]
    allocations = [int(s.to_integral_value(rounding=ROUND_FLOOR)) for s in shares]
    remaining = amount - sum(allocations)
    order = sorted(range(len(people)), key=lambda i: (-(shares[i] - allocations[i]), i))
    for i in order[:remaining]:
        allocations[i] += 1
    title = re.search(r'(電費|水費|瓦斯費|管理費)', text)
    heading = title.group(1) if title else '帳單'
    lines = [f'{heading}分攤結果', f'應繳總額：{amount:,}元', f'在家天數合計：{total_days}人天', '']
    for (name, days), share in zip(people, allocations):
        lines.append(f'{name}：{days}天，{share:,}元')
    lines.append(f'合計：{sum(allocations):,}元')
    return '\n'.join(lines)


def answer(text):
    text = text.strip()
    if text.startswith('計算帳單'):
        return calculate(text)
    if text in ('測試', 'ping', '你好', '嗨'):
        return '機器人已收到訊息，可以輸入「計算帳單」開始計算。'
    return '請輸入「計算帳單」，附上應繳金額及每人的在家天數。'


def reply(token, reply_token, message):
    data = json.dumps({'replyToken': reply_token, 'messages': [{'type': 'text', 'text': message[:5000]}]}, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request('https://api.line.me/v2/bot/message/reply', data=data,
                                 headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {token}'}, method='POST')
    with urllib.request.urlopen(req, timeout=10) as response:
        return response.status


@app.get('/')
def home():
    return 'LINE bill bot is running', 200


@app.get('/health')
def health():
    return jsonify(status='ok'), 200


@app.post('/callback')
def callback():
    secret = os.getenv('LINE_CHANNEL_SECRET', '').strip()
    token = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', '').strip()
    if not secret or not token:
        app.logger.error('Missing LINE_CHANNEL_SECRET or LINE_CHANNEL_ACCESS_TOKEN')
        return 'Server configuration missing', 503
    raw = request.get_data()
    signature = request.headers.get('X-Line-Signature', '')
    expected = base64.b64encode(hmac.new(secret.encode(), raw, hashlib.sha256).digest()).decode()
    if not hmac.compare_digest(signature, expected):
        return 'Invalid signature', 400
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return 'Invalid JSON', 400
    for event in payload.get('events', []):
        if event.get('type') == 'message' and event.get('message', {}).get('type') == 'text':
            reply_token = event.get('replyToken')
            if reply_token:
                try:
                    reply(token, reply_token, answer(event['message'].get('text', '')))
                except (urllib.error.URLError, TimeoutError, OSError) as exc:
                    app.logger.exception('LINE reply API failed: %s', exc)
                    return 'LINE API error', 502
    return 'OK', 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', '10000')))
