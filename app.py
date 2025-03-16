from flask import Flask, request, jsonify, g
from datetime import datetime
import os
import requests
import base64
import binascii
import json
import logging
import csv
import tempfile
import re
import uuid
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2 import service_account
from flask_compress import Compress

# config.py
from pydantic import BaseSettings

class Settings(BaseSettings):
    GOOGLE_BOOKS_API_KEY: str
    GOOGLE_APPLICATION_CREDENTIALS: str
    MAX_RETRIES: int = 3
    CACHE_TIMEOUT: int = 3600
    
    class Config:
        env_file = '.env'

from config.settings import get_settings

def create_app():
    """Application factory pattern"""
    app = Flask(__name__)
    settings = get_settings()
    
    # Configure app with settings
    app.config.update(
        GOOGLE_BOOKS_API_KEY=settings.GOOGLE_BOOKS_API_KEY,
        GOOGLE_APPLICATION_CREDENTIALS=settings.GOOGLE_APPLICATION_CREDENTIALS,
        MAX_RETRIES=settings.MAX_RETRIES,
        CACHE_TIMEOUT=settings.CACHE_TIMEOUT
    )
    
    # Initialize extensions
    compress = Compress()
    compress.init_app(app)
    
    return app, settings

app, settings = create_app()

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@app.before_request
def before_request():
    g.request_id = request.headers.get('X-Request-ID', str(uuid.uuid4()))
    logger.info(f"Processing request {g.request_id}")

# Google Books API Key with graceful fallback - MOVED BEFORE ANY ROUTES
GOOGLE_BOOKS_API_KEY = settings.GOOGLE_BOOKS_API_KEY
if not GOOGLE_BOOKS_API_KEY or (isinstance(GOOGLE_BOOKS_API_KEY, str) and GOOGLE_BOOKS_API_KEY.strip() == ""):
    raise RuntimeError("GOOGLE_BOOKS_API_KEY is not set. Application cannot start without it.")

RESULTS_DIR = os.path.join(os.getcwd(), "learning", "Results")
os.makedirs(RESULTS_DIR, exist_ok=True)  # Ensure directory exists

from tenacity import retry, stop_after_attempt, wait_exponential
from functools import lru_cache
from ratelimit import limits, sleep_and_retry, RateLimitException

@sleep_and_retry
@limits(calls=100, period=60)  # 100 calls per minute
@lru_cache(maxsize=128)  # Cache results
@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=2, max=10))
def fetch_books_from_google(query):
    """Fetch books from Google Books API with rate limiting and caching."""
    url = f"https://www.googleapis.com/books/v1/volumes?q={query}&key={GOOGLE_BOOKS_API_KEY}"
    try:
        response = requests.get(url)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching data from Google Books API: {e}")
        raise  # Retry on transient errors
    except RateLimitException as e:
        logger.warning(f"Rate limit exceeded: {e}")
        raise  # Retry on rate-limit errors
    data = response.json()
    return [filter_book_data(item["volumeInfo"]) for item in data.get("items", [])]

logger.info(f"Results directory: {RESULTS_DIR}")
logger.info(f"Application root: {os.path.dirname(__file__)}")
logger.info(f"Running on Heroku: {bool(os.getenv('HEROKU'))}")

def get_drive_service():
    """Returns an authenticated Google Drive service object.
    
    Returns:
        googleapiclient.discovery.Resource: Authenticated Google Drive service
        
    Raises:
        RuntimeError: If credentials are missing or invalid
        GoogleDriveError: If service creation fails
    """
    google_credentials = app.config['GOOGLE_APPLICATION_CREDENTIALS']
    if not google_credentials:
        logger.error("GOOGLE_APPLICATION_CREDENTIALS environment variable is not set")
        raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS is not set")
    
    try:
        # Decode and parse credentials
        creds_json = base64.b64decode(google_credentials).decode("utf-8")
        credentials_info = json.loads(creds_json)
        
        # Create credentials object with specific scope
        credentials = service_account.Credentials.from_service_account_info(
            credentials_info, 
            scopes=["https://www.googleapis.com/auth/drive.file"]
        )
        
        # Build and return the service
        service = build('drive', 'v3', credentials=credentials)
        logger.info("Successfully created Google Drive service")
        return service
        
    except (binascii.Error, json.JSONDecodeError) as e:
        logger.error(f"Invalid GOOGLE_APPLICATION_CREDENTIALS format: {str(e)}")
        raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS must be a valid base64-encoded JSON string.")
    except Exception as e:
        logger.error(f"Failed to create Drive service: {str(e)}")
        raise GoogleDriveError(f"Drive service creation failed: {str(e)}")

class GoogleDriveError(Exception):
    """Custom exception for Google Drive operations"""
    pass

class BookAPIError(Exception):
    """Custom exception for Google Books API operations"""
    pass

def upload_to_google_drive(file_path, file_name):
    """
    Uploads a file to Google Drive and sets its permissions to be publicly accessible.

    Args:
        file_path (str): The full path to the file to be uploaded.
        file_name (str): The name to assign to the file on Google Drive.

    Returns:
        str: A shareable Google Drive link to the uploaded file, or None if an error occurs.

    Raises:
        GoogleDriveError: If the file does not exist or the upload fails.
    """
    if not os.path.exists(file_path):
        raise GoogleDriveError(f"File not found: {file_path}")
    
    service = get_drive_service()
    logger.info(f"Attempting to upload file: {file_name} from path: {file_path}") # Added log
    try:
        file_metadata = {'name': file_name}
        media = MediaFileUpload(file_path, mimetype='text/csv', resumable=True)
        logger.info("Creating Google Drive file...") # Added log
        file = service.files().create(body=file_metadata, media_body=media, fields="id").execute()
        logger.info("Google Drive file created, setting permissions...") # Added log
        file_id = file.get("id")

        # Make the file publicly accessible
        try:
            service.permissions().create(
                fileId=file_id,
                body={"role": "reader", "type": "anyone"},
            ).execute()
            logger.info(f"File permissions set, shareable link generated.") # Added log
        except Exception as e:
            logger.error(f"Error setting file permissions: {e}", exc_info=True)
            return None

        return f"https://drive.google.com/file/d/{file_id}/view?usp=sharing"
    except Exception as e:
        raise GoogleDriveError(f"Upload failed: {str(e)}")
    
def save_results_to_temp_csv(books, query):
    logger.info("save_results_to_temp_csv function CALLED!") # Added log
    file_path = None
    try:
        # Sanitize the query to ensure a valid filename
        sanitized_query = "".join(c for c in query if c.isalnum() or c in (' ', '-', '_')).strip()
        file_name = f"search_results_{sanitized_query}.csv"
        file_path = os.path.join(tempfile.gettempdir(), file_name)  # Use tempfile.gettempdir() for portability
        
        # Check if books is None or empty
        if not books:
            logger.warning("No books found for the given query.")
            return None

        with open(file_path, "w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=["title", "authors", "description"])
            writer.writeheader()
            writer.writerows(books)

        return upload_to_google_drive(file_path, file_name)
    finally:
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError:
                logger.warning(f"Failed to remove temporary file: {file_path}")

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
# Pre-compile the regex pattern for truncating descriptions
DESCRIPTION_TRUNCATION_REGEX = re.compile(r'(.{400,}?\.)(?:\s|$)')

def filter_book_data(volume_info):
    """Extract relevant book information and format description."""
    
    title = volume_info.get("title", "Unknown Title")
    authors = volume_info.get("authors", ["Unknown Author"])
    description = volume_info.get("description", "")
    # Use pre-compiled regex to efficiently truncate the description at the last complete sentence
    if description:
        match = DESCRIPTION_TRUNCATION_REGEX.search(description)
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
    """Save search results to a CSV file with enhanced error handling
    
    Args:
        books (list): List of book dictionaries to save
        query (str): Search query used to fetch the books
        
    Returns:
        str: Name of the saved file or None if save failed
    """
    if not books:
        logger.warning("No books to save")
        return None
        
    try:
        # Create timestamp and filename
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'search_results_{query.replace(" ", "_")}_{timestamp}.csv'
        filepath = os.path.join(RESULTS_DIR, filename)
        
        logger.info(f"Attempting to save results to: {filepath}")
        logger.info(f"Number of books to save: {len(books)}")
        
        # Define fields once and consistently
        fieldnames = ['title', 'authors', 'description']
        
        # Create a deep copy of books to avoid modifying the original data
        books_to_save = []
        for book in books:
            book_copy = book.copy()
            if isinstance(book_copy.get('categories'), list):
                book_copy['categories'] = ', '.join(book_copy['categories'])
            books_to_save.append(book_copy)
        
        # Write CSV file with proper indentation
        with open(filepath, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(books_to_save)
        
        logger.info(f"Successfully saved {len(books)} results to {filepath}")
        return filename
        
    except Exception as e:
        logger.error(f"Error saving results to CSV: {str(e)}", exc_info=True)
        return None

@app.route("/api/v1/search_books")
def search_books():
    query = request.args.get("query", "").strip()
    if not query or len(query) < 2:
        return jsonify({"error": "Query must be at least 2 characters"}), 400

    # Sanitize query to allow only alphanumeric characters, spaces, and safe symbols
    if not re.match(r'^[a-zA-Z0-9 _-]+$', query):
        return jsonify({"error": "Query contains invalid characters"}), 400
    
    # Add pagination
    per_page = request.args.get("per_page", default=10, type=int)
    
    if not (0 < per_page <= 40):  # Google Books API limit
        return jsonify({"error": "per_page must be between 1 and 40"}), 400

    books = fetch_books_from_google(query)  # Existing function to fetch books
    # Validate and structure the book data using BookResponse
    validated_books = [BookResponse(**book).dict() for book in books]
    validated_books = [BookResponse(**book).dict() for book in books]
    drive_link = save_results_to_temp_csv(books, query) # <-- CORRECT LINE - Call save_results_to_temp_csv
    if drive_link is None:
        logger.error("Failed to save results to CSV or upload to Google Drive")
        return jsonify({"error": "Failed to save results to CSV or upload to Google Drive"}), 500
    return jsonify({"books": validated_books, "csv_link": drive_link})

@app.route('/health')
def health_check():
    """Health check endpoint for monitoring"""
    try:
        # Test Google Books API connection
        fetch_books_from_google("test")
        # Test Google Drive connection
        try:
            service = get_drive_service()
            logger.info("Drive service test successful")
        except Exception as e:
            logger.error(f"Drive service test failed: {str(e)}")
        return jsonify({"status": "healthy"}), 200
    except Exception as e:
        return jsonify({
            "status": "unhealthy",
            "error": str(e)
        }), 503

@app.route("/api/v1/list_results")
def list_results():
    try:
        # Check if the results directory exists
        if not os.path.exists(RESULTS_DIR):
            logger.warning(f"Results directory does not exist: {RESULTS_DIR}")
            return jsonify({
                "error": "Results directory does not exist",
                "directory": RESULTS_DIR
            }), 404

        # List all CSV files in the results directory
        result_files = [f for f in os.listdir(RESULTS_DIR) if f.endswith('.csv')]
        return jsonify({
            "files": result_files,
            "count": len(result_files),
            "directory": RESULTS_DIR
        })
    except Exception as e:
        logger.error(f"Error listing results: {str(e)}")
        return jsonify({"error": str(e)}), 500

def validate_port(port_str):
    if not port_str.isdigit():
        raise RuntimeError(f"Invalid PORT environment variable: {port_str}. Must be a numeric value.")
    port = int(port_str)
    if port <= 0 or port > 65535:
        raise ValueError("Port number must be between 1 and 65535.")
    return port

if __name__ == "__main__":
    port_env = os.environ.get("PORT", "5000")
    try:
        port = validate_port(port_env)
        app.run(host="0.0.0.0", port=port, debug=False)
    except (ValueError, RuntimeError) as e:
        logger.error(f"Failed to start server: {str(e)}")
        raise

from pydantic import BaseModel, Field
from typing import List, Optional

class BookResponse(BaseModel):
    title: str
    authors: List[str]
    description: Optional[str]
    
    class Config:
        schema_extra = {
            "example": {
                "title": "The Great Gatsby",
                "authors": ["F. Scott Fitzgerald"],
                "description": "A story of the American dream..."
            }
        }
