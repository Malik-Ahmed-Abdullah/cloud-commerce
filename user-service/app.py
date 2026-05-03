from flask import Flask, request, jsonify
import psycopg2
import bcrypt
import os
import time
import jwt
import datetime

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
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username VARCHAR(100) UNIQUE NOT NULL,
            email VARCHAR(200) UNIQUE NOT NULL,
            password VARCHAR(200) NOT NULL
        )
    """)
    conn.commit()
    cur.close()
    conn.close()

SECRET_KEY = os.environ.get("SECRET_KEY", "supersecretkey")

@app.route('/users/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username')
    password = data.get('password')

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, password FROM users WHERE username = %s", (username,))
    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        return jsonify({"error": "Invalid username or password"}), 401

    user_id, hashed = row
    if bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8')):
        # Generate a JWT token valid for 24 hours
        token = jwt.encode({
            "user_id": user_id,
            "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=24)
        }, SECRET_KEY, algorithm="HS256")
        return jsonify({"message": "Login successful", "token": token}), 200
    else:
        return jsonify({"error": "Invalid username or password"}), 401
    
@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "user-service is running"}), 200

@app.route('/users/register', methods=['POST'])
def register():
    data = request.json
    username = data.get('username')
    email = data.get('email')
    password = data.get('password')

    if not username or not email or not password:
        return jsonify({"error": "username, email, and password are required"}), 400

    # Hash the password before saving
    hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO users (username, email, password) VALUES (%s, %s, %s) RETURNING id",
            (username, email, hashed)
        )
        user_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"message": "User registered", "user_id": user_id}), 201
    except psycopg2.errors.UniqueViolation:
        return jsonify({"error": "Username or email already exists"}), 409

@app.route('/users/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username')
    password = data.get('password')

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, password FROM users WHERE username = %s", (username,))
    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        return jsonify({"error": "Invalid username or password"}), 401

    user_id, hashed = row
    if bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8')):
        return jsonify({"message": "Login successful", "user_id": user_id}), 200
    else:
        return jsonify({"error": "Invalid username or password"}), 401

@app.route('/users/<int:user_id>', methods=['GET'])
def get_user(user_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, username, email FROM users WHERE id = %s", (user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row:
        return jsonify({"id": row[0], "username": row[1], "email": row[2]}), 200
    return jsonify({"error": "User not found"}), 404

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5001, debug=True)