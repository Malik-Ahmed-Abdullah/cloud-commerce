from flask import Flask, request, jsonify
import psycopg2
import os
import time
import redis
import json

app = Flask(__name__)

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
        CREATE TABLE IF NOT EXISTS products (
            id SERIAL PRIMARY KEY,
            name VARCHAR(200) NOT NULL,
            description TEXT,
            price NUMERIC(10, 2) NOT NULL,
            stock INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.commit()
    cur.close()
    conn.close()

redis_client = redis.Redis(host='redis', port=6379, decode_responses=True)

@app.route('/products', methods=['GET'])
def list_products():
    # Try to get from cache first
    cached = redis_client.get('products_list')
    if cached:
        print("Serving from Redis cache")
        return jsonify(json.loads(cached)), 200

    # If not cached, get from DB
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, name, description, price, stock FROM products")
    rows = cur.fetchall()
    cur.close()
    conn.close()

    products = [
        {"id": r[0], "name": r[1], "description": r[2], "price": float(r[3]), "stock": r[4]}
        for r in rows
    ]

    # Save to cache for 60 seconds
    redis_client.setex('products_list', 60, json.dumps(products))
    print("Serving from DB and caching result")
    return jsonify(products), 200

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "product-service is running"}), 200

@app.route('/products', methods=['POST'])
def add_product():
    data = request.json
    name = data.get('name')
    description = data.get('description', '')
    price = data.get('price')
    stock = data.get('stock', 0)

    if not name or price is None:
        return jsonify({"error": "name and price are required"}), 400

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO products (name, description, price, stock) VALUES (%s, %s, %s, %s) RETURNING id",
        (name, description, price, stock)
    )
    product_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    redis_client.delete('products_list')
    return jsonify({"message": "Product added", "product_id": product_id}), 201

@app.route('/products', methods=['GET'])
def list_products():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, name, description, price, stock FROM products")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    products = [
        {"id": r[0], "name": r[1], "description": r[2], "price": float(r[3]), "stock": r[4]}
        for r in rows
    ]
    return jsonify(products), 200

@app.route('/products/<int:product_id>', methods=['GET'])
def get_product(product_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, name, description, price, stock FROM products WHERE id = %s", (product_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row:
        return jsonify({"id": row[0], "name": row[1], "description": row[2], "price": float(row[3]), "stock": row[4]}), 200
    return jsonify({"error": "Product not found"}), 404

@app.route('/products/<int:product_id>/decrement', methods=['PATCH'])
def decrement_stock(product_id):
    data = request.json
    quantity = data.get('quantity', 1)

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT stock FROM products WHERE id = %s", (product_id,))
    row = cur.fetchone()

    if not row:
        return jsonify({"error": "Product not found"}), 404

    current_stock = row[0]
    if current_stock < quantity:
        return jsonify({"error": "Not enough stock"}), 400

    cur.execute(
        "UPDATE products SET stock = stock - %s WHERE id = %s",
        (quantity, product_id)
    )
    conn.commit()
    cur.close()
    conn.close()
    redis_client.delete('products_list')
    return jsonify({"message": "Stock updated"}), 200

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5002, debug=True)