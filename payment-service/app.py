from flask import Flask, request, jsonify
import random
import time

app = Flask(__name__)

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "payment-service is running"}), 200

@app.route('/payments/charge', methods=['POST'])
def charge():
    data = request.json
    user_id = data.get('user_id')
    amount = data.get('amount')
    order_id = data.get('order_id')

    if not user_id or not amount or not order_id:
        return jsonify({"error": "user_id, amount, and order_id are required"}), 400

    # Simulate payment processing delay
    time.sleep(1)

    # Simulate 90% success rate
    success = random.random() < 0.9

    if success:
        transaction_id = f"TXN-{random.randint(100000, 999999)}"
        return jsonify({
            "message": "Payment successful",
            "transaction_id": transaction_id,
            "order_id": order_id,
            "amount_charged": amount
        }), 200
    else:
        return jsonify({"error": "Payment declined — please try again"}), 402

@app.route('/payments/refund', methods=['POST'])
def refund():
    data = request.json
    transaction_id = data.get('transaction_id')

    if not transaction_id:
        return jsonify({"error": "transaction_id is required"}), 400

    return jsonify({
        "message": "Refund processed",
        "transaction_id": transaction_id
    }), 200