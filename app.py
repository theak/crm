from flask import Flask, request, jsonify, render_template
import sqlite3
import enum
import os

class CustomerStatus(enum.Enum):
    NEED_TO_RESPOND = 1  # You need to respond to them
    WAITING_ON_THEM = 2  # They need to get back to you
    NO_ACTION = 3        # No action needed

app = Flask(__name__, template_folder='templates')

# Database setup
DATABASE_PATH = "customer_tracker.db"

def init_db():
    """Initialize the database and create tables if they don't exist"""
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        # Create customers table if it doesn't exist
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT UNIQUE NOT NULL,
            status INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status_changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        conn.commit()

def get_db_connection():
    """Get a database connection"""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row  # This enables column access by name
    return conn

@app.route('/api/set_customer_status', methods=['POST'])
def set_customer_status():
    """Set or update a customer's status"""
    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 400

    data = request.get_json()

    # Validate required fields
    if 'domain' not in data or 'status' not in data:
        return jsonify({"error": "Both domain and status are required"}), 400

    domain = data['domain'].strip().lower()
    status = data['status']

    # Validate domain
    if not domain or '.' not in domain:
        return jsonify({"error": "Invalid domain format"}), 400

    # Validate status
    try:
        status = int(status)
        if status not in [s.value for s in CustomerStatus]:
            valid_statuses = [f"{s.value}: {s.name}" for s in CustomerStatus]
            return jsonify({
                "error": f"Invalid status. Valid options are: {', '.join(valid_statuses)}"
            }), 400
    except ValueError:
        return jsonify({"error": "Status must be a number"}), 400

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()

            # Check if customer exists and get current status if it does
            cursor.execute("SELECT id, status FROM customers WHERE domain = ?", (domain,))
            result = cursor.fetchone()

            if result:
                # If status has changed, update status_changed_at timestamp
                if result['status'] != status:
                    cursor.execute(
                        "UPDATE customers SET status = ?, status_changed_at = CURRENT_TIMESTAMP WHERE domain = ?",
                        (status, domain)
                    )
                else:
                    # Status hasn't changed, only update the status field
                    cursor.execute(
                        "UPDATE customers SET status = ? WHERE domain = ?",
                        (status, domain)
                    )
            else:
                # Insert new customer with current timestamp for both created_at and status_changed_at
                cursor.execute(
                    "INSERT INTO customers (domain, status, created_at, status_changed_at) VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                    (domain, status)
                )

            conn.commit()

            return jsonify({
                "success": True,
                "message": f"Customer {domain} status has been set to {CustomerStatus(status).name}"
            })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/get_customers', methods=['GET'])
def get_customers():
    """Get all customers and their statuses"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    domain,
                    status,
                    created_at,
                    status_changed_at,
                    (julianday('now') - julianday(status_changed_at)) as days_since_status_change
                FROM customers
                ORDER BY domain
            """)
            rows = cursor.fetchall()

            customers = []
            for row in rows:
                status_enum = CustomerStatus(row['status'])
                customers.append({
                    "domain": row['domain'],
                    "status": row['status'],
                    "status_name": status_enum.name,
                    "created_at": row['created_at'],
                    "status_changed_at": row['status_changed_at'],
                    "days_since_status_change": round(row['days_since_status_change'], 1)
                })

            return jsonify({
                "success": True,
                "customers": customers,
                "count": len(customers)
            })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/get_customer_status/<domain>', methods=['GET'])
def get_customer_status(domain):
    """Get a specific customer's status by domain"""
    domain = domain.strip().lower()

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    domain,
                    status,
                    created_at,
                    status_changed_at,
                    (julianday('now') - julianday(status_changed_at)) as days_since_status_change
                FROM customers
                WHERE domain = ?
            """, (domain,))
            row = cursor.fetchone()

            if not row:
                return jsonify({
                    "error": f"Customer with domain '{domain}' not found"
                }), 404

            status_enum = CustomerStatus(row['status'])
            return jsonify({
                "success": True,
                "domain": row['domain'],
                "status": row['status'],
                "status_name": status_enum.name,
                "created_at": row['created_at'],
                "status_changed_at": row['status_changed_at'],
                "days_since_status_change": round(row['days_since_status_change'], 1)
            })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Root route to serve the frontend
@app.route('/')
def index():
    return render_template('index.html')

# Initialize database
def init_app(app):
    with app.app_context():
        init_db()

# Create the database file if it doesn't exist
if not os.path.exists(DATABASE_PATH):
    init_db()

if __name__ == '__main__':
    # Initialize the database
    init_app(app)
    app.run(debug=True)
