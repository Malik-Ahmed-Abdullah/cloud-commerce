from flask import Flask, request, jsonify
import psycopg2
import requests
import os
import time
import jwt

SECRET_KEY = os.environ.get("SECRET_KEY", "supersecretkey")

def verify_token(request):
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        return payload.get("user_id")
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None

app = Flask(__name__)

USER_SERVICE_URL = os.environ.get("USER_SERVICE_URL", "http://user-service:5001")
PRODUCT_SERVICE_URL = os.environ.get("PRODUCT_SERVICE_URL", "http://product-service:5002")

def get_db():
    for attempt in range(10):
        try:
            conn = psycopg2.connect(
                host=os.environ.get("DB_HOST", "postgres"),
                database=os.environ.get("DB_NAME", "cloudcommerce"),
                user=os.environ.get("DB_USER", "admin"),
                password=os.environ.get("DB_PASSWORD", "secret")
            )
            return conn
        except psycopg2.OperationalError:
            print(f"DB not ready, retrying... ({attempt+1}/10)")
            time.sleep(3)
    raise Exception("Could not connect to database")

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1,
            status VARCHAR(50) NOT NULL DEFAULT 'placed',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    cur.close()
    conn.close()

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "order-service is running"}), 200

@app.route('/orders', methods=['POST'])
def place_order():
    # Verify JWT token
    token_user_id = verify_token(request)
    if not token_user_id:
        return jsonify({"error": "Unauthorized — please login first"}), 401

    data = request.json
    user_id = data.get('user_id')
    product_id = data.get('product_id')
    quantity = data.get('quantity', 1)

    if not user_id or not product_id:
        return jsonify({"error": "user_id and product_id are required"}), 400

    # Make sure the token belongs to the user placing the order
    if token_user_id != user_id:
        return jsonify({"error": "Unauthorized — token does not match user_id"}), 403

    if not user_id or not product_id:
        return jsonify({"error": "user_id and product_id are required"}), 400

    # Validate user exists by calling the user-service
    try:
        user_resp = requests.get(f"{USER_SERVICE_URL}/users/{user_id}", timeout=5)
        if user_resp.status_code != 200:
            return jsonify({"error": f"User {user_id} not found"}), 404
    except requests.exceptions.ConnectionError:
        return jsonify({"error": "Could not reach user-service"}), 503

    # Validate product exists by calling the product-service
    try:
        product_resp = requests.get(f"{PRODUCT_SERVICE_URL}/products/{product_id}", timeout=5)
        if product_resp.status_code != 200:
            return jsonify({"error": f"Product {product_id} not found"}), 404
    except requests.exceptions.ConnectionError:
        return jsonify({"error": "Could not reach product-service"}), 503

    # Get current stock
    product_data = product_resp.json()
    if product_data['stock'] < quantity:
        return jsonify({"error": "Not enough stock available"}), 400

    # Decrement stock via a new product-service endpoint
    decrement_resp = requests.patch(
        f"{PRODUCT_SERVICE_URL}/products/{product_id}/decrement",
        json={"quantity": quantity},
        timeout=5
    )
    if decrement_resp.status_code != 200:
        return jsonify({"error": "Failed to update stock"}), 500
    
    # Place the order
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO orders (user_id, product_id, quantity) VALUES (%s, %s, %s) RETURNING id",
        (user_id, product_id, quantity)
    )
    order_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()

    # Charge the user after placing the order
    product_price = product_data['price']
    total = product_price * quantity

    try:
        payment_resp = requests.post(
            "http://payment-service:5004/payments/charge",
            json={"user_id": user_id, "amount": total, "order_id": order_id},
            timeout=10
        )
        payment_result = payment_resp.json()
    except requests.exceptions.ConnectionError:
        payment_result = {"error": "Payment service unreachable"}

    return jsonify({
        "message": "Order placed",
        "order_id": order_id,
        "payment": payment_result
    }), 201

@app.route('/orders', methods=['GET'])
def list_orders():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, user_id, product_id, quantity, status, created_at FROM orders")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    orders = [
        {"id": r[0], "user_id": r[1], "product_id": r[2], "quantity": r[3], "status": r[4], "created_at": str(r[5])}
        for r in rows
    ]
    return jsonify(orders), 200

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5003, debug=True)