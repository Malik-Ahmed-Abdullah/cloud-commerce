from flask import Flask, request, jsonify
import requests
import os

app = Flask(__name__)


def required_env(name):
  value = os.environ.get(name)
  if not value:
    raise RuntimeError(f"{name} is required")
  return value


RESEND_API_KEY = required_env("RESEND_API_KEY")

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "notification-service is running"}), 200

@app.route('/notify/health', methods=['GET'])
def notify_health():
  return jsonify({"status": "notification-service is running"}), 200

@app.route('/notify/order-confirmation', methods=['POST'])
def send_order_confirmation():
    data = request.json
    to_email = data.get('email')
    order_id = data.get('order_id')
    items = data.get('items', [])
    total = data.get('total', 0)

    if not to_email or not order_id:
        return jsonify({"error": "email and order_id are required"}), 400

    items_html = "".join([
        f"<tr><td style='padding:8px'>{i.get('name')}</td><td style='padding:8px'>x{i.get('quantity')}</td><td style='padding:8px'>${i.get('price',0):.2f}</td></tr>"
        for i in items
    ])

    html_body = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:auto">
      <h1 style="background:#0f172a;color:white;padding:20px;margin:0">☁️ NimbusMart</h1>
      <div style="padding:30px">
        <h2>Order Confirmed! 🎉</h2>
        <p>Thank you for your order. Your order <strong>#{order_id}</strong> has been placed successfully.</p>
        <table style="width:100%;border-collapse:collapse;margin:20px 0">
          <thead><tr style="background:#f1f5f9">
            <th style="padding:8px;text-align:left">Item</th>
            <th style="padding:8px;text-align:left">Qty</th>
            <th style="padding:8px;text-align:left">Price</th>
          </tr></thead>
          <tbody>{items_html}</tbody>
        </table>
        <p style="font-size:1.2em"><strong>Total: ${total:.2f}</strong></p>
        <p style="color:#64748b">Your order is being processed and will ship soon.</p>
      </div>
    </div>
    """

    resp = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
        json={
            "from": "NimbusMart <onboarding@resend.dev>",
            "to": [to_email],
            "subject": f"Order #{order_id} Confirmed — NimbusMart",
            "html": html_body
        }
    )

    if resp.status_code in (200,201):
        return jsonify({"message": "Email sent"}), 200
    else:
        return jsonify({"error": "Failed to send email", "detail": resp.text}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5008, debug=True)
