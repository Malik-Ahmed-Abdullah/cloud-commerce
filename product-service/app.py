from flask import Flask, request, jsonify
import psycopg2
import redis
import requests
import json
import os
import time
import cloudinary
import cloudinary.uploader

app = Flask(__name__)


def required_env(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value

cloudinary.config(
    cloud_name=required_env("CLOUDINARY_CLOUD_NAME"),
    api_key=required_env("CLOUDINARY_API_KEY"),
    api_secret=required_env("CLOUDINARY_API_SECRET")
)

redis_client = redis.Redis(
    host=os.environ.get("REDIS_HOST", "redis"),
    port=6379,
    decode_responses=True
)

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
            print(f"DB not ready, retrying... ({attempt+1}/10)")
            time.sleep(3)
    raise Exception("Could not connect to database")

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id SERIAL PRIMARY KEY,
            name VARCHAR(300) NOT NULL,
            description TEXT,
            price NUMERIC(10, 2) NOT NULL,
            stock INTEGER NOT NULL DEFAULT 0,
            category VARCHAR(100),
            image TEXT,
            rating NUMERIC(3,2) DEFAULT 0,
            rating_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()

    # Seed from Fake Store API if table is empty
    cur.execute("SELECT COUNT(*) FROM products")
    count = cur.fetchone()[0]
    if count == 0:
        print("Seeding products from Fake Store API...")
        try:
            resp = requests.get("https://fakestoreapi.com/products", timeout=10)
            products = resp.json()
            for p in products:
                cur.execute("""
                    INSERT INTO products (name, description, price, stock, category, image, rating, rating_count)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    p['title'],
                    p['description'],
                    p['price'],
                    max(10, int(p['rating']['count'] / 10)),
                    p['category'],
                    p['image'],
                    p['rating']['rate'],
                    p['rating']['count']
                ))
            conn.commit()
            print(f"Seeded {len(products)} products successfully")
        except Exception as e:
            print(f"Failed to seed products: {e}")

    cur.close()
    conn.close()

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "product-service is running"}), 200

@app.route('/products', methods=['GET'])
def list_products():
    category = request.args.get('category')
    search = request.args.get('search')
    sort = request.args.get('sort', 'id')
    order = request.args.get('order', 'asc')
    limit = request.args.get('limit', 20, type=int)
    offset = request.args.get('offset', 0, type=int)

    # Only use cache for plain unfiltered requests
    if not category and not search and sort == 'id' and offset == 0:
        cached = redis_client.get('products_all')
        if cached:
            return jsonify(json.loads(cached)), 200

    conn = get_db()
    cur = conn.cursor()

    query = """
        SELECT id, name, description, price, stock, category, image, rating, rating_count
        FROM products WHERE 1=1
    """
    params = []

    if category:
        query += " AND category = %s"
        params.append(category)
    if search:
        query += " AND (name ILIKE %s OR description ILIKE %s)"
        params.extend([f'%{search}%', f'%{search}%'])

    allowed_sorts = {'id', 'price', 'rating', 'name'}
    sort = sort if sort in allowed_sorts else 'id'
    order = 'DESC' if order == 'desc' else 'ASC'
    query += f" ORDER BY {sort} {order} LIMIT %s OFFSET %s"
    params.extend([limit, offset])

    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    products = [
        {
            "id": r[0], "name": r[1], "description": r[2],
            "price": float(r[3]), "stock": r[4], "category": r[5],
            "image": r[6], "rating": float(r[7]) if r[7] else 0,
            "rating_count": r[8]
        }
        for r in rows
    ]

    if not category and not search and sort == 'id':
        redis_client.setex('products_all', 120, json.dumps(products))

    return jsonify(products), 200

@app.route('/products/categories', methods=['GET'])
def get_categories():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT category FROM products WHERE category IS NOT NULL ORDER BY category")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify([r[0] for r in rows]), 200

@app.route('/products/<int:product_id>', methods=['GET'])
def get_product(product_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, name, description, price, stock, category, image, rating, rating_count
        FROM products WHERE id = %s
    """, (product_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row:
        return jsonify({
            "id": row[0], "name": row[1], "description": row[2],
            "price": float(row[3]), "stock": row[4], "category": row[5],
            "image": row[6], "rating": float(row[7]) if row[7] else 0,
            "rating_count": row[8]
        }), 200
    return jsonify({"error": "Product not found"}), 404

@app.route('/products', methods=['POST'])
def add_product():
    data = request.json
    if not data.get('name') or data.get('price') is None:
        return jsonify({"error": "name and price are required"}), 400
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO products (name, description, price, stock, category, image, rating, rating_count)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
    """, (
        data['name'], data.get('description', ''), data['price'],
        data.get('stock', 0), data.get('category', 'general'),
        data.get('image', ''), data.get('rating', 0), data.get('rating_count', 0)
    ))
    product_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    redis_client.delete('products_all')
    return jsonify({"message": "Product added", "product_id": product_id}), 201


@app.route('/products/upload-image', methods=['POST'])
def upload_image():
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files['file']
    try:
        result = cloudinary.uploader.upload(file, folder="nimbus-products")
        return jsonify({"url": result.get('secure_url')}), 200
    except Exception as e:
        return jsonify({"error": "Upload failed", "detail": str(e)}), 500

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
    if row[0] < quantity:
        return jsonify({"error": "Not enough stock"}), 400
    cur.execute("UPDATE products SET stock = stock - %s WHERE id = %s", (quantity, product_id))
    conn.commit()
    cur.close()
    conn.close()
    redis_client.delete('products_all')
    return jsonify({"message": "Stock updated"}), 200

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5002, debug=True)