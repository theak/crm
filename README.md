# Customer Status Tracker

A simple Flask application with SQLite to track customer statuses.

## Features

- Track customer status with three possible states:
  1. You need to respond to them
  2. They need to get back to you
  3. No action needed
- Add or update customer statuses
- View all customers with their current statuses
- Search for specific customers by domain

## API Endpoints

1. `POST /api/set_customer_status` - Set or update a customer's status
   - Request body: `{"domain": "example.com", "status": 1}`
   - Status codes: 1 (Need to respond), 2 (Waiting on them), 3 (No action needed)

2. `GET /api/get_customers` - Get all customers and their statuses

3. `GET /api/get_customer_status/<domain>` - Get a specific customer's status

## Project Structure

```
customer-status-tracker/
├── app.py              # Main Flask application
├── customer_tracker.db # SQLite database (created on first run)
└── templates/
    └── index.html      # Frontend template
```

## Installation & Setup

1. Make sure you have Python 3.6+ installed
2. Install Flask:
   ```
   pip install flask
   ```
3. Create the project structure:
   ```
   mkdir -p customer-status-tracker/templates
   cd customer-status-tracker
   ```
4. Copy the `app.py` file to the main directory
5. Copy the `index.html` file to the `templates` directory

## Running the Application

1. Start the application:
   ```
   python app.py
   ```
2. Open your web browser and navigate to `http://127.0.0.1:5000`

## Usage Examples

### Adding a customer via API
```bash
curl -X POST http://127.0.0.1:5000/api/set_customer_status \
  -H "Content-Type: application/json" \
  -d '{"domain": "example.com", "status": 1}'
```

### Getting all customers via API
```bash
curl -X GET http://127.0.0.1:5000/api/get_customers
```

### Getting a specific customer's status via API
```bash
curl -X GET http://127.0.0.1:5000/api/get_customer_status/example.com
```
