from flask import Flask, request, jsonify
import redis
import json
import os

app = Flask(__name__)

redis_client = redis.Redis(
    host=os.environ.get("REDIS_HOST", "redis"),
    port=6379,
    decode_responses=True
)

def cart_key(user_id):
    return f"cart:{user_id}"

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "cart-service is running"}), 200

@app.route('/cart/<int:user_id>', methods=['GET'])
def get_cart(user_id):
    raw = redis_client.get(cart_key(user_id))
    cart = json.loads(raw) if raw else []
    total = sum(item['price'] * item['quantity'] for item in cart)
    return jsonify({"user_id": user_id, "items": cart, "total": round(total, 2), "count": len(cart)}), 200

@app.route('/cart/<int:user_id>/add', methods=['POST'])
def add_to_cart(user_id):
    data = request.json
    product_id = data.get('product_id')
    quantity = data.get('quantity', 1)
    name = data.get('name')
    price = data.get('price')
    image = data.get('image', '')

    if not product_id or not name or price is None:
        return jsonify({"error": "product_id, name, and price are required"}), 400

    raw = redis_client.get(cart_key(user_id))
    cart = json.loads(raw) if raw else []

    # If product already in cart, update quantity
    for item in cart:
        if item['product_id'] == product_id:
            item['quantity'] += quantity
            redis_client.setex(cart_key(user_id), 86400, json.dumps(cart))
            return jsonify({"message": "Cart updated", "cart": cart}), 200

    cart.append({
        "product_id": product_id,
        "name": name,
        "price": price,
        "image": image,
        "quantity": quantity
    })
    redis_client.setex(cart_key(user_id), 86400, json.dumps(cart))
    return jsonify({"message": "Added to cart", "cart": cart}), 200

@app.route('/cart/<int:user_id>/update', methods=['PATCH'])
def update_cart(user_id):
    data = request.json
    product_id = data.get('product_id')
    quantity = data.get('quantity')

    if quantity is None or product_id is None:
        return jsonify({"error": "product_id and quantity required"}), 400

    raw = redis_client.get(cart_key(user_id))
    cart = json.loads(raw) if raw else []

    if quantity <= 0:
        cart = [i for i in cart if i['product_id'] != product_id]
    else:
        for item in cart:
            if item['product_id'] == product_id:
                item['quantity'] = quantity

    redis_client.setex(cart_key(user_id), 86400, json.dumps(cart))
    return jsonify({"message": "Cart updated", "cart": cart}), 200

@app.route('/cart/<int:user_id>/clear', methods=['DELETE'])
def clear_cart(user_id):
    redis_client.delete(cart_key(user_id))
    return jsonify({"message": "Cart cleared"}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5005, debug=True)