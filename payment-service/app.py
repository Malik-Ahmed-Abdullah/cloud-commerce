from flask import Flask, request, jsonify
import firebase_admin
from firebase_admin import credentials, db as firebase_db
import stripe
import requests
import os
import psycopg2
import time

app = Flask(__name__)


def required_env(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value

stripe.api_key = required_env("STRIPE_SECRET_KEY")
WEBHOOK_SECRET = required_env("STRIPE_WEBHOOK_SECRET")
ORDER_SERVICE_URL = os.environ.get("ORDER_SERVICE_URL", "http://order-service:5003")

# Initialize Firebase (only once)
try:
    cred = credentials.Certificate("/app/firebase-key.json")
    firebase_admin.initialize_app(cred, {
        'databaseURL': os.environ.get("FIREBASE_DB_URL")
    })
    print("Firebase initialized successfully")
except Exception as e:
    print(f"Firebase init failed (non-critical): {e}")

def get_db():
    for attempt in range(10):
        try:
            return psycopg2.connect(
                host=os.environ.get("DB_HOST", "postgres"),
                database=os.environ.get("DB_NAME", "cloudcommerce"),
                user=os.environ.get("DB_USER", "admin"),
                password=required_env("DB_PASSWORD")
            )
        except psycopg2.OperationalError:
            time.sleep(3)
    raise Exception("Could not connect to database")

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id SERIAL PRIMARY KEY,
            stripe_payment_intent VARCHAR(200) UNIQUE,
            amount NUMERIC(10,2),
            currency VARCHAR(10),
            status VARCHAR(50),
            user_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    cur.close()
    conn.close()

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "payment-service is running"}), 200

@app.route('/webhook', methods=['POST'])
def webhook():
    payload = request.get_data()
    sig_header = request.headers.get('Stripe-Signature')

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError:
        return jsonify({"error": "Invalid signature"}), 400

    if event['type'] == 'payment_intent.succeeded':
        intent = event['data']['object']
        payment_intent_id = intent['id']
        amount = intent['amount'] / 100
        currency = intent['currency']
        user_id = intent.get('metadata', {}).get('user_id')

        # Save payment record
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO payments (stripe_payment_intent, amount, currency, status, user_id)
            VALUES (%s, %s, %s, 'succeeded', %s)
            ON CONFLICT (stripe_payment_intent) DO UPDATE SET status = 'succeeded'
        """, (payment_intent_id, amount, currency, user_id))
        conn.commit()
        cur.close()
        conn.close()

        # Update order status
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            UPDATE orders SET status = 'payment_confirmed', updated_at = NOW()
            WHERE stripe_payment_intent = %s
        """, (payment_intent_id,))
        conn.commit()
        cur.close()
        conn.close()

        # Mirror status change to Firebase
        try:
            conn2 = get_db()
            cur2 = conn2.cursor()
            cur2.execute("SELECT id FROM orders WHERE stripe_payment_intent = %s", (payment_intent_id,))
            row2 = cur2.fetchone()
            cur2.close()
            conn2.close()
            if row2:
                firebase_db.reference(f'/orders/{row2[0]}/status').set('payment_confirmed')
                firebase_db.reference(f'/orders/{row2[0]}/updated_at').set({'.sv': 'timestamp'})
        except Exception as e:
            print(f"Firebase update in payment service failed: {e}")

        print(f"Payment succeeded for intent {payment_intent_id}")

    elif event['type'] == 'payment_intent.payment_failed':
        intent = event['data']['object']
        payment_intent_id = intent['id']
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO payments (stripe_payment_intent, status)
            VALUES (%s, 'failed')
            ON CONFLICT (stripe_payment_intent) DO UPDATE SET status = 'failed'
        """, (payment_intent_id,))
        conn.commit()
        cur.close()
        conn.close()

    return jsonify({"status": "ok"}), 200

@app.route('/payments', methods=['GET'])
def list_payments():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, stripe_payment_intent, amount, currency, status, user_id, created_at
        FROM payments ORDER BY created_at DESC
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify([{
        "id": r[0], "stripe_payment_intent": r[1], "amount": float(r[2]) if r[2] else 0,
        "currency": r[3], "status": r[4], "user_id": r[5], "created_at": str(r[6])
    } for r in rows]), 200

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5004, debug=True)