from flask import Flask, request, jsonify
import psycopg2
import bcrypt
import os
import time
import jwt
import datetime

app = Flask(__name__)


def required_env(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value

def get_db():
    for attempt in range(10):
        try:
            conn = psycopg2.connect(
                host=os.environ.get("DB_HOST", "postgres"),
                database=os.environ.get("DB_NAME", "cloudcommerce"),
                user=os.environ.get("DB_USER", "admin"),
                password=required_env("DB_PASSWORD")
            )
            return conn
        except psycopg2.OperationalError:
            print(f"DB not ready, retrying... ({attempt+1}/10)")
            time.sleep(3)
    raise Exception("Could not connect to database")

from flask import Flask, request, jsonify
import psycopg2
import psycopg2.errors
import bcrypt
import os
import time
import jwt
import datetime

app = Flask(__name__)
SECRET_KEY = required_env("SECRET_KEY")

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
    # Create table with role column
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username VARCHAR(100) UNIQUE NOT NULL,
            email VARCHAR(200) UNIQUE NOT NULL,
            password VARCHAR(200) NOT NULL,
            role VARCHAR(20) NOT NULL DEFAULT 'customer',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Add role column if it doesn't exist (for existing deployments)
    cur.execute("""
        ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(20) NOT NULL DEFAULT 'customer'
    """)
    cur.execute("""
        ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    """)
    # Create a default admin account if none exists
    cur.execute("SELECT id FROM users WHERE role = 'admin' LIMIT 1")
    if not cur.fetchone():
        admin_username = os.environ.get("ADMIN_USERNAME")
        admin_email = os.environ.get("ADMIN_EMAIL")
        admin_password = os.environ.get("ADMIN_PASSWORD")

        if admin_username and admin_email and admin_password:
            hashed = bcrypt.hashpw(admin_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            try:
                cur.execute(
                    "INSERT INTO users (username, email, password, role) VALUES (%s, %s, %s, %s)",
                    (admin_username, admin_email, hashed, "admin")
                )
                print("Bootstrap admin created from ADMIN_* env vars")
            except Exception:
                pass
        else:
            print("No admin account found. Set ADMIN_USERNAME, ADMIN_EMAIL, and ADMIN_PASSWORD to bootstrap one.")
    conn.commit()
    cur.close()
    conn.close()

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
    hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    try:
        conn = get_db()
        cur = conn.cursor()
        # Customers can only register as customers — role cannot be passed in
        cur.execute(
            "INSERT INTO users (username, email, password, role) VALUES (%s, %s, %s, 'customer') RETURNING id",
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
    role_required = data.get('role')  # 'admin' if coming from admin login

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, password, role, email FROM users WHERE username = %s", (username,))
    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        return jsonify({"error": "Invalid username or password"}), 401

    user_id, hashed, role, email = row

    # If admin login was requested, reject non-admins
    if role_required == 'admin' and role != 'admin':
        return jsonify({"error": "Access denied — not an admin account"}), 403

    if bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8')):
        token = jwt.encode({
            "user_id": user_id,
            "role": role,
            "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=24)
        }, SECRET_KEY, algorithm="HS256")
        return jsonify({
            "message": "Login successful",
            "token": token,
            "user_id": user_id,
            "username": username,
            "email": email,
            "role": role
        }), 200
    else:
        return jsonify({"error": "Invalid username or password"}), 401

@app.route('/users/<int:user_id>', methods=['GET'])
def get_user(user_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, username, email, role, created_at FROM users WHERE id = %s", (user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row:
        return jsonify({
            "id": row[0], "username": row[1],
            "email": row[2], "role": row[3],
            "created_at": str(row[4])
        }), 200
    return jsonify({"error": "User not found"}), 404

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5001, debug=True)