from flask import Flask, request, jsonify
import firebase_admin
from firebase_admin import credentials, db as firebase_db
import psycopg2
import requests
import stripe
import jwt
import os
import time

app = Flask(__name__)


def required_env(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value

stripe.api_key = required_env("STRIPE_SECRET_KEY")
SECRET_KEY = required_env("SECRET_KEY")
USER_SERVICE_URL = os.environ.get("USER_SERVICE_URL", "http://user-service:5001")
PRODUCT_SERVICE_URL = os.environ.get("PRODUCT_SERVICE_URL", "http://product-service:5002")
CART_SERVICE_URL = os.environ.get("CART_SERVICE_URL", "http://cart-service:5005")

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
        CREATE TABLE IF NOT EXISTS orders (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            items JSONB NOT NULL,
            total NUMERIC(10,2) NOT NULL,
            status VARCHAR(50) DEFAULT 'pending',
            stripe_payment_intent VARCHAR(200),
            shipping_address TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    cur.close()
    conn.close()

def push_order_to_firebase(order_id, user_id, status, total, items):
    try:
        firebase_db.reference(f'/orders/{order_id}').set({
            'order_id': order_id,
            'user_id': user_id,
            'status': status,
            'total': total,
            'items': items,
            'updated_at': {'.sv': 'timestamp'}
        })
    except Exception as e:
        print(f"Firebase write failed (non-critical): {e}")

def verify_token(request):
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        return None
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    except:
        return None

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "order-service is running"}), 200

@app.route('/orders/checkout', methods=['POST'])
def checkout():
    auth = verify_token(request)
    if not auth:
        return jsonify({"error": "Unauthorized"}), 401
    user_id = auth.get("user_id")

    data = request.json
    shipping_address = data.get('shipping_address', '')

    # Get cart
    cart_resp = requests.get(f"{CART_SERVICE_URL}/cart/{user_id}", timeout=5)
    cart = cart_resp.json()

    if not cart['items']:
        return jsonify({"error": "Cart is empty"}), 400

    total = cart['total']
    items = cart['items']

    # Verify stock for all items
    for item in items:
        prod_resp = requests.get(f"{PRODUCT_SERVICE_URL}/products/{item['product_id']}", timeout=5)
        if prod_resp.status_code != 200:
            return jsonify({"error": f"Product {item['product_id']} not found"}), 404
        prod = prod_resp.json()
        if prod['stock'] < item['quantity']:
            return jsonify({"error": f"Not enough stock for {prod['name']}"}), 400

    # Create Stripe Payment Intent
    intent = stripe.PaymentIntent.create(
        amount=int(total * 100),  # Stripe uses cents
        currency='usd',
        metadata={'user_id': str(user_id)}
    )

    # Create order in DB
    import json
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO orders (user_id, items, total, status, stripe_payment_intent, shipping_address)
        VALUES (%s, %s, %s, 'pending', %s, %s) RETURNING id
    """, (user_id, json.dumps(items), total, intent.id, shipping_address))
    order_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()

    # Write to Firebase for real-time tracking
    push_order_to_firebase(order_id, user_id, 'pending', total, items)

    # Send confirmation email (best-effort, non-blocking)
    try:
        user_resp = requests.get(f"{USER_SERVICE_URL}/users/{user_id}", timeout=3)
        if user_resp.status_code == 200:
            user_email = user_resp.json().get('email')
            if user_email:
                try:
                    requests.post(
                        "http://notification-service:5008/notify/order-confirmation",
                        json={"email": user_email, "order_id": order_id, "items": items, "total": total},
                        timeout=5
                    )
                except Exception:
                    pass
    except Exception:
        pass

    return jsonify({
        "order_id": order_id,
        "client_secret": intent.client_secret,
        "total": total,
        "items": items
    }), 201

@app.route('/orders', methods=['GET'])
def list_orders():
    auth = verify_token(request)
    conn = get_db()
    cur = conn.cursor()
    if auth and auth.get("role") == "admin":
        cur.execute("""
            SELECT id, user_id, items, total, status, shipping_address, created_at
            FROM orders ORDER BY created_at DESC
        """)
    elif auth:
        cur.execute("""
            SELECT id, user_id, items, total, status, shipping_address, created_at
            FROM orders WHERE user_id = %s ORDER BY created_at DESC
        """, (auth.get("user_id"),))
    else:
        cur.execute("""
            SELECT id, user_id, items, total, status, shipping_address, created_at
            FROM orders ORDER BY created_at DESC
        """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify([{
        "id": r[0], "user_id": r[1], "items": r[2],
        "total": float(r[3]), "status": r[4],
        "shipping_address": r[5], "created_at": str(r[6])
    } for r in rows]), 200

@app.route('/orders/<int:order_id>', methods=['GET'])
def get_order(order_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, user_id, items, total, status, shipping_address, created_at
        FROM orders WHERE id = %s
    """, (order_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row:
        return jsonify({
            "id": row[0], "user_id": row[1], "items": row[2],
            "total": float(row[3]), "status": row[4],
            "shipping_address": row[5], "created_at": str(row[6])
        }), 200
    return jsonify({"error": "Order not found"}), 404

@app.route('/orders/<int:order_id>/status', methods=['PATCH'])
def update_status(order_id):
    data = request.json
    status = data.get('status')
    allowed = ['pending', 'payment_confirmed', 'processing', 'shipped', 'delivered', 'cancelled']
    if status not in allowed:
        return jsonify({"error": f"Invalid status. Must be one of {allowed}"}), 400
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE orders SET status = %s, updated_at = NOW() WHERE id = %s RETURNING user_id, total", (status, order_id))
    row = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()

    # Push status update to Firebase
    if row:
        try:
            firebase_db.reference(f'/orders/{order_id}/status').set(status)
            firebase_db.reference(f'/orders/{order_id}/updated_at').set({'.sv': 'timestamp'})
        except Exception as e:
            print(f"Firebase update failed: {e}")

    return jsonify({"message": "Status updated", "status": status}), 200

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5003, debug=True)