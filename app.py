import enum
import functools  # Added for decorator
import json
import os
import re
import sqlite3
from email.utils import parseaddr

import requests
from flask import (
    Flask,
    Response,  # Added for Basic Auth response
    g,  # Added for app context db connection
    jsonify,
    render_template,
    request,
)
from jinja2 import Template


class CustomerStatus(enum.Enum):
    NEED_TO_RESPOND = 1  # You need to respond to them
    WAITING_ON_THEM = 2  # They need to get back to you
    NO_ACTION = 3  # No action needed


app = Flask(__name__, template_folder="templates")

# Database setup
DATABASE_PATH = "customer_tracker.db"


def get_db():
    """Get a database connection for the current request context"""
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE_PATH)
        db.row_factory = sqlite3.Row  # This enables column access by name
    return db


@app.teardown_appcontext
def close_connection(exception):
    """Close the database connection at the end of the request"""
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()


def init_db():
    """Initialize the database and create tables if they don't exist"""
    # Use a separate connection for initialization
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        # Create customers table if it doesn't exist
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT UNIQUE NOT NULL,
            status INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status_changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        # Create settings table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        # Insert default settings if they don't exist
        cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('user_email', '')"
        )
        cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('password', '')"
        )  # Default password is empty

        conn.commit()


# --- Authentication ---
def check_auth(password_attempt):
    """Check if the provided password matches the one in the DB."""
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = 'password'")
    result = cursor.fetchone()
    stored_password = result["value"] if result else None
    # If no password is set in DB, auth is effectively disabled
    return stored_password and password_attempt == stored_password


def authenticate():
    """Sends a 401 response that enables basic auth"""
    return Response(
        "Could not verify your access level for that URL.\n"
        "You have to login with proper credentials",
        401,
        {"WWW-Authenticate": 'Basic realm="Login Required"'},
    )


def require_auth(f):
    """Decorator to enforce basic authentication if password is set"""

    @functools.wraps(f)
    def decorated(*args, **kwargs):
        db = get_db()
        cursor = db.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = 'password'")
        result = cursor.fetchone()
        stored_password = result["value"] if result and result["value"] else None

        # Only enforce auth if a password is set in the database
        if stored_password:
            auth = request.authorization
            # If no auth header or incorrect password, request authentication
            if not auth or not check_auth(auth.password):
                return authenticate()
        # If no password is set OR auth is successful, proceed to the view
        return f(*args, **kwargs)

    return decorated


# --- End Authentication ---


@app.route("/api/set_customer_status", methods=["POST"])
# No auth required for this endpoint by default
def set_customer_status():
    """Set or update a customer's status"""
    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 400

    data = request.get_json()

    # Validate required fields
    if "domain" not in data or "status" not in data:
        return jsonify({"error": "Both domain and status are required"}), 400

    domain = data["domain"].strip().lower()
    status = data["status"]

    # Validate domain
    if not domain or "." not in domain:
        return jsonify({"error": "Invalid domain format"}), 400

    # Validate status
    try:
        status = int(status)
        if status not in [s.value for s in CustomerStatus]:
            valid_statuses = [f"{s.value}: {s.name}" for s in CustomerStatus]
            return jsonify(
                {
                    "error": f"Invalid status. Valid options are: {', '.join(valid_statuses)}"
                }
            ), 400
    except ValueError:
        return jsonify({"error": "Status must be a number"}), 400

    try:
        db = get_db()
        cursor = db.cursor()

        # Check if customer exists and get current status if it does
        cursor.execute("SELECT id, status FROM customers WHERE domain = ?", (domain,))
        result = cursor.fetchone()

        if result:
            # If status has changed, update status_changed_at timestamp
            if result["status"] != status:
                cursor.execute(
                    "UPDATE customers SET status = ?, status_changed_at = CURRENT_TIMESTAMP WHERE domain = ?",
                    (status, domain),
                )
            else:
                # Status hasn't changed, only update the status field
                cursor.execute(
                    "UPDATE customers SET status = ? WHERE domain = ?",
                    (status, domain),
                )
        else:
            # Insert new customer with current timestamp for both created_at and status_changed_at
            cursor.execute(
                "INSERT INTO customers (domain, status, created_at, status_changed_at) VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                (domain, status),
            )

        db.commit()

        return jsonify(
            {
                "success": True,
                "message": f"Customer {domain} status has been set to {CustomerStatus(status).name}",
            }
        )

    except Exception as e:
        # Rollback in case of error might be needed depending on transaction handling
        return jsonify({"error": str(e)}), 500


@app.route("/api/get_customers", methods=["GET"])
@require_auth  # Protect this endpoint
def get_customers():
    """Get all customers and their statuses"""
    try:
        db = get_db()
        cursor = db.cursor()
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
            status_enum = CustomerStatus(row["status"])
            customers.append(
                {
                    "domain": row["domain"],
                    "status": row["status"],
                    "status_name": status_enum.name,
                    "created_at": row["created_at"],
                    "status_changed_at": row["status_changed_at"],
                    "days_since_status_change": round(
                        row["days_since_status_change"], 1
                    ),
                }
            )

        return jsonify(
            {"success": True, "customers": customers, "count": len(customers)}
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/get_customer_status/<domain>", methods=["GET"])
@require_auth  # Protect this endpoint
def get_customer_status(domain):
    """Get a specific customer's status by domain"""
    domain = domain.strip().lower()

    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            """
            SELECT
                domain,
                status,
                created_at,
                status_changed_at,
                (julianday('now') - julianday(status_changed_at)) as days_since_status_change
            FROM customers
            WHERE domain = ?
        """,
            (domain,),
        )
        row = cursor.fetchone()

        if not row:
            return jsonify({"error": f"Customer with domain '{domain}' not found"}), 404

        status_enum = CustomerStatus(row["status"])
        return jsonify(
            {
                "success": True,
                "domain": row["domain"],
                "status": row["status"],
                "status_name": status_enum.name,
                "created_at": row["created_at"],
                "status_changed_at": row["status_changed_at"],
                "days_since_status_change": round(row["days_since_status_change"], 1),
            }
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# Settings endpoints
@app.route("/api/settings", methods=["GET"])
@require_auth  # Protect this endpoint
def get_settings():
    """Get all settings"""
    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute("SELECT key, value, updated_at FROM settings")
        rows = cursor.fetchall()

        settings = {}
        for row in rows:
            # Mask password if it exists
            if row["key"] == "password" and row["value"]:
                settings[row["key"]] = {
                    "value": "********",  # Mask the password
                    "updated_at": row["updated_at"],
                }
            else:
                settings[row["key"]] = {
                    "value": row["value"],
                    "updated_at": row["updated_at"],
                }

        return jsonify({"success": True, "settings": settings})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/settings/<key>", methods=["GET"])
@require_auth  # Protect this endpoint
def get_setting(key):
    """Get a specific setting by key"""
    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            "SELECT key, value, updated_at FROM settings WHERE key = ?", (key,)
        )
        row = cursor.fetchone()

        if not row:
            return jsonify({"error": f"Setting with key '{key}' not found"}), 404

        value_to_return = row["value"]
        # Mask password if requested key is 'password' and value is not empty
        if key == "password" and value_to_return:
            value_to_return = "********"

        return jsonify(
            {
                "success": True,
                "key": row["key"],
                "value": value_to_return,
                "updated_at": row["updated_at"],
            }
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/settings/<key>", methods=["POST"])
@require_auth  # Protect this endpoint
def update_setting(key):
    """Update a specific setting by key"""
    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 400

    data = request.get_json()

    # Validate required fields
    if "value" not in data:
        return jsonify({"error": "Value is required"}), 400

    value = data["value"]

    # Special validation for email
    if key == "user_email" and value:
        # Basic email validation
        if not re.match(r"[^@]+@[^@]+\.[^@]+", value):
            return jsonify({"error": "Invalid email format"}), 400

    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            "UPDATE settings SET value = ?, updated_at = CURRENT_TIMESTAMP WHERE key = ?",
            (value, key),
        )

        if cursor.rowcount == 0:
            # Setting doesn't exist, check if it's a valid key before inserting
            if key not in ["user_email", "password"]:  # Only allow known keys
                return jsonify({"error": f"Setting key '{key}' is not allowed"}), 400
            cursor.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                (key, value),
            )

        db.commit()

        return jsonify(
            {"success": True, "message": f"Setting '{key}' has been updated"}
        )

    except Exception as e:
        # Rollback might be needed
        return jsonify({"error": str(e)}), 500


# Root route to serve the frontend
@app.route("/")
@require_auth  # Protect this endpoint
def index():
    return render_template("index.html")


# Settings page
@app.route("/settings")
@require_auth  # Protect this endpoint
def settings():
    return render_template("settings.html")


# Helper function to extract domain from email address
def extract_domain_from_email(email):
    """Extract domain from email address"""
    _, email_address = parseaddr(email)
    if not email_address or "@" not in email_address:
        return None

    # Get the domain part after @
    domain = email_address.split("@")[1].lower()
    return domain


# Helper function to use Anthropic API for email analysis
def analyze_email_with_anthropic(user_email, sender_domain, email_subject, email_body):
    """Analyze email content with Anthropic API"""
    try:
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
        if not anthropic_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable not found")

        prompt = get_llm_prompt(user_email)

        # Prepare the API request
        headers = {
            "Content-Type": "application/json",
            "X-API-Key": anthropic_key,
            "anthropic-version": "2023-06-01",
        }

        # Format for Anthropic's Claude API
        payload = {
            "model": "claude-3-sonnet-20240229",
            "max_tokens": 1000,
            "messages": [
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": f"Sender domain: {sender_domain}\nSubject: {email_subject}\nBody: {email_body}",
                },
            ],
            "tools": [
                {
                    "name": "set_customer_status",
                    "description": "Set the status for a customer based on email analysis",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "domain": {
                                "type": "string",
                                "description": "The domain of the customer",
                            },
                            "status": {
                                "type": "integer",
                                "description": "The status to set for the customer (1: NEED_TO_RESPOND, 2: WAITING_ON_THEM, 3: NO_ACTION)",
                            },
                            "reasoning": {
                                "type": "string",
                                "description": "Explanation for why this status was chosen",
                            },
                        },
                        "required": ["domain", "status", "reasoning"],
                    },
                }
            ],
            "tool_choice": {"type": "tool", "name": "set_customer_status"},
        }

        response = requests.post(
            "https://api.anthropic.com/v1/messages", headers=headers, json=payload
        )
        response_data = response.json()

        if "content" not in response_data or not response_data["content"]:
            raise ValueError(f"Unexpected API response: {response_data}")

        # Extract tool calls from content
        tool_calls = [
            item for item in response_data["content"] if item.get("type") == "tool_use"
        ]

        if not tool_calls:
            raise ValueError("API did not return expected tool call")

        # Get the first tool call
        tool_call = tool_calls[0]
        if tool_call["name"] != "set_customer_status":
            raise ValueError(f"Unexpected tool name: {tool_call['name']}")

        # Return the input object
        return tool_call["input"]

    except Exception as e:
        raise Exception(f"Anthropic API error: {str(e)}")


@app.route("/api/process-email", methods=["POST"])
def process_email():
    """Process an incoming email from SendGrid webhook and update customer status"""
    try:
        # Extract data from SendGrid webhook payload
        # SendGrid sends form data with email details
        if request.content_type == "application/json":
            data = request.get_json()
        else:
            # Handle form data
            data = request.form.to_dict()

        # Extract email details
        sender = data.get("from", "")
        subject = data.get("subject", "")
        body_html = data.get("html", "")
        body_text = data.get("text", "")

        # Prefer plain text over HTML for analysis
        email_body = body_text if body_text else body_html

        # Extract domain from sender email
        sender_domain = extract_domain_from_email(sender)
        if not sender_domain:
            return jsonify(
                {
                    "success": False,
                    "error": "Could not extract valid domain from sender email",
                }
            ), 400

        # Get user email from settings
        db = get_db()
        cursor = db.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = 'user_email'")
        row = cursor.fetchone()
        user_email = row["value"] if row else ""

        # Decide which LLM to use based on available API keys
        if os.environ.get("ANTHROPIC_API_KEY"):
            result = analyze_email_with_anthropic(
                user_email, sender_domain, subject, email_body
            )
            llm_used = "Anthropic"
        else:
            return jsonify(
                {
                    "success": False,
                    "error": "No LLM API key found in environment variables",
                }
            ), 500

        # Call set_customer_status endpoint internally
        status_request = {
            "domain": result.get("domain", sender_domain),
            "status": result.get("status"),
        }

        # Make internal request to set_customer_status
        status_response = set_customer_status()
        status_data = json.loads(status_response.data)

        return jsonify(
            {
                "success": True,
                "message": "Email processed successfully",
                "sender_domain": sender_domain,
                "status_set": CustomerStatus(result.get("status")).name,
                "llm_used": llm_used,
                "reasoning": result.get("reasoning", ""),
                "status_response": status_data,
            }
        )

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# Debugging route for email processing
@app.route("/api/process-email-debug", methods=["GET"])
def process_email_debug():
    """Debug endpoint for email processing logic"""
    try:
        # Get user email from settings
        db = get_db()
        cursor = db.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = 'user_email'")
        row = cursor.fetchone()
        user_email = row["value"] if row else ""

        return jsonify(
            {
                "success": True,
                "message": "Email processing endpoint is set up",
                "user_email": user_email,
                "prompt": get_llm_prompt(user_email),
                "openai_key_available": bool(os.environ.get("OPENAI_KEY")),
                "anthropic_key_available": bool(os.environ.get("ANTHROPIC_API_KEY")),
                "webhook_setup_instructions": "Set up SendGrid Inbound Parse webhook to point to your /api/process-email endpoint",
            }
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# Function to get the LLM prompt template
def get_llm_prompt(user_email):
    """Get the LLM prompt template with the user's email filled in"""
    prompt_path = os.path.join(os.path.dirname(__file__), "templates", "llm_prompt.txt")

    # Check if template exists
    if not os.path.exists(prompt_path):
        return "Error: llm_prompt.txt template not found."

    try:
        with open(prompt_path, "r") as file:
            template_content = file.read()
    except IOError as e:
        return f"Error reading llm_prompt.txt: {e}"

    template = Template(template_content)
    return template.render(user_email=user_email)


# Initialize database within app context
def init_app(current_app):
    with current_app.app_context():
        init_db()
        # Ensure the database connection is available for the first request if needed
        get_db()


# Create the database file if it doesn't exist and initialize schema
if not os.path.exists(DATABASE_PATH):
    # Need an app context to initialize the DB properly if using g
    temp_app = Flask(__name__)
    with temp_app.app_context():
        init_db()

if __name__ == "__main__":
    # Initialize the app (registers teardown)
    init_app(app)
    app.run(debug=True, port=7000)
