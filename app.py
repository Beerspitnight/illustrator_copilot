from flask import Flask, request, jsonify
from datetime import datetime
import os
import requests
import base64
import json
import logging
import csv
import tempfile
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2 import service_account

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Google Books API Key with graceful fallback - MOVED BEFORE ANY ROUTES
GOOGLE_BOOKS_API_KEY = os.getenv("GOOGLE_BOOKS_API_KEY")
if not GOOGLE_BOOKS_API_KEY or GOOGLE_BOOKS_API_KEY.strip() == "":
    raise RuntimeError("GOOGLE_BOOKS_API_KEY is not set. Application cannot start without it.")

RESULTS_DIR = os.path.join(os.getcwd(), "learning", "Results")
os.makedirs(RESULTS_DIR, exist_ok=True)  # Ensure directory exists

from tenacity import retry, stop_after_attempt, wait_exponential

@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=2, max=10))
def fetch_books_from_google(query):
    """Fetch books from Google Books API with retry logic."""
    url = f"https://www.googleapis.com/books/v1/volumes?q={query}&key={GOOGLE_BOOKS_API_KEY}"
    try:
        response = requests.get(url)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching data from Google Books API: {e}")
        raise  # Retry on transient errors
    data = response.json()
    return [filter_book_data(item["volumeInfo"]) for item in data.get("items", [])]

logger.info(f"Results directory: {RESULTS_DIR}")
logger.info(f"Application root: {os.path.dirname(__file__)}")
logger.info(f"Running on Heroku: {bool(os.getenv('HEROKU'))}")

def get_drive_service():
    """Returns an authenticated Google Drive service object."""
    google_credentials = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not google_credentials:
        raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS is not set")
    
    try:
        creds_json = base64.b64decode(google_credentials).decode("utf-8")
        credentials_info = json.loads(creds_json)
    except (base64.binascii.Error, json.JSONDecodeError) as e:
        logger.error(f"Invalid GOOGLE_APPLICATION_CREDENTIALS format: {e}")
        raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS must be a valid base64-encoded JSON string.")
    
    creds_json = base64.b64decode(google_credentials).decode("utf-8")
    credentials_info = json.loads(creds_json)
    credentials = service_account.Credentials.from_service_account_info(
        credentials_info, scopes=["https://www.googleapis.com/auth/drive.file"]
    )
def upload_to_google_drive(file_path, file_name):
    """Uploads a CSV file to Google Drive and returns the shareable link."""
    service = get_drive_service()
    try:
        file_metadata = {'name': file_name}
        media = MediaFileUpload(file_path, mimetype='text/csv', resumable=True)
        file = service.files().create(body=file_metadata, media_body=media, fields="id").execute()
        file_id = file.get("id")

        # Make the file publicly accessible
        service.permissions().create(
            fileId=file_id,
            body={"role": "reader", "type": "anyone"},
        ).execute()

        return f"https://drive.google.com/file/d/{file_id}/view?usp=sharing"
    except Exception as e:
        logger.error(f"Error uploading file to Google Drive or setting permissions: {e}", exc_info=True)
        return None

    return f"https://drive.google.com/file/d/{file_id}/view?usp=sharing"

def save_results_to_temp_csv(books, query):
    # Sanitize the query to ensure a valid filename
    sanitized_query = "".join(c for c in query if c.isalnum() or c in (' ', '-', '_')).strip()
    file_name = f"search_results_{sanitized_query}.csv"
    file_path = os.path.join(tempfile.gettempdir(), file_name)  # Use tempfile.gettempdir() for portability
    if not books:
        logger.warning("No books found for the given query.")
        return None

    with open(file_path, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=["title", "authors", "description"])
        writer.writeheader()
        writer.writerows(books)

    return upload_to_google_drive(file_path, file_name)

@app.route('/')
def index():
    """Single index route that returns HTML for better browser display"""
    logger.info("Index route accessed!")
    if not GOOGLE_BOOKS_API_KEY:
        logger.error("API key not configured")
        return jsonify({
            "status": "configuration_error",
            "message": "API key not configured. Please set GOOGLE_BOOKS_API_KEY environment variable."
        }), 503
    
    # Return HTML instead of JSON for better browser experience
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>LibraryCloud API</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 40px; line-height: 1.6; }
            h1 { color: #333; }
            .endpoint { background: #f4f4f4; padding: 10px; border-radius: 5px; margin-bottom: 10px; }
        </style>
    </head>
    <body>
        <h1>Welcome to the Ill-Co-P Learns API!</h1>
        <p>The API is running successfully.</p>
        <h2>Available Endpoints:</h2>
        <div class="endpoint">
            <p><strong>Search Books:</strong> /search_books?query=your_search_term</p>
            <p>Example: <a href="/search_books?query=design">/search_books?query=design</a></p>
        </div>
        <div class="endpoint">
            <p><strong>List Results:</strong> /list_results</p>
            <p>Example: <a href="/list_results">/list_results</a></p>
        </div>
    </body>
    </html>
    """

def filter_book_data(volume_info):
    """Extract relevant book information and format description."""
    
    title = volume_info.get("title", "Unknown Title")
    authors = volume_info.get("authors", ["Unknown Author"])
    description = volume_info.get("description", "")
    # Use regex to efficiently truncate the description at the last complete sentence
    if description:
        import re
        match = re.search(r'(.{400,}?\.)(?:\s|$)', description)
        if match:
            description = match.group(1)
    if description:
        last_period = description.rfind(".")
        if last_period > 400:  # Only trim at sentence if it's not too short
            description = description[: last_period + 1]

    return {
        "title": title,
        "authors": ", ".join(authors),
        "description": description,
    }

def save_results_to_csv(books, query):
    """Save search results to a CSV file with enhanced error handling"""
    try:
        # Create timestamp and filename
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'search_results_{query.replace(" ", "_")}_{timestamp}.csv'
        filepath = os.path.join(RESULTS_DIR, filename)
        
        logger.info(f"Attempting to save results to: {filepath}")
        logger.info(f"Number of books to save: {len(books)}")
        
        # Define fields once and consistently - Fixed
        fieldnames = ['title', 'authors', 'description']
        
        # Create a deep copy of books to avoid modifying the original data
        books_to_save = []
        for book in books:
            book_copy = book.copy()
            if isinstance(book_copy.get('categories'), list):
                book_copy['categories'] = ', '.join(book_copy['categories'])
            books_to_save.append(book_copy)
        
        with open(filepath, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(books_to_save)
        
        logger.info(f"Successfully saved {len(books)} results to {filepath}")
        return filename
        
    except Exception as e:
        logger.error(f"Error saving results to CSV: {str(e)}", exc_info=True)
        return None

@app.route("/search_books")
def search_books():
    query = request.args.get("query", "").strip()
    if not query:
        return jsonify({"error": "Missing query parameter"}), 400

    books = fetch_books_from_google(query)  # Existing function to fetch books
    if not books:
        return jsonify({"message": "No results found"}), 404
    drive_link = save_results_to_csv(books, query)
    if not drive_link:
        return jsonify({"error": "Failed to save results to CSV or upload to Google Drive"}), 500

    return jsonify({"books": books, "csv_link": drive_link})

    return jsonify({"books": books, "csv_link": drive_link})


@app.route('/list_results', methods=['GET'])
def list_results():
    """List all CSV files in the results directory."""
    try:
        # List all CSV files in the results directory
        result_files = [f for f in os.listdir(RESULTS_DIR) if f.endswith('.csv')]
        return jsonify({
            "files": result_files,
            "count": len(result_files),
            "directory": RESULTS_DIR
        })
    except Exception as e:
        logger.error(f"Error listing results: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 500
    port_env = os.environ.get("PORT", "5000")
    if not port_env.isdigit():
        raise RuntimeError(f"Invalid PORT environment variable: {port_env}. Please set it to a valid integer.")
    try:
        port = int(port_env)
        if port <= 0 or port > 65535:
            raise ValueError("Port number must be between 1 and 65535.")
    except ValueError as e:
        raise RuntimeError(f"Invalid PORT environment variable: {port_env}. {str(e)}")
    app.run(host="0.0.0.0", port=port, debug=False)
