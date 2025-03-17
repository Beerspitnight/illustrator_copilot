from flask import Flask, request, jsonify, g, redirect, url_for, Blueprint
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
from pydantic import BaseModel
from google.oauth2 import service_account
from flask_compress import Compress
from pydantic_settings import BaseSettings
from tenacity import retry, stop_after_attempt, wait_exponential
from functools import lru_cache
from ratelimit import limits, sleep_and_retry, RateLimitException

# Define BookResponse model
class BookResponse(BaseModel):
    title: str
    authors: list[str]
    description: str | None = None

    class Config:
        schema_extra = {
            "example": {
                "title": "The Great Gatsby",
                "authors": ["F. Scott Fitzgerald"],
                "description": "A story of the American dream...",
                "categories": ["Fiction", "Classic"],
                "publisher": "Scribner"
            }
        }

# Define Settings model
class Settings(BaseSettings):
    GOOGLE_BOOKS_API_KEY: str
    GOOGLE_APPLICATION_CREDENTIALS: str
    MAX_RETRIES: int = 3
    CACHE_TIMEOUT: int = 3600

    class Config:
        env_file = '.env'

# Initialize settings
def get_settings():
    return Settings()

# Initialize Blueprint
api_v1 = Blueprint('api_v1', __name__, url_prefix='/api/v1')

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Define RESULTS_DIR outside the route function
RESULTS_DIR = os.path.join(os.getcwd(), "learning", "Results")

# Define function to register routes
def register_routes(api_v1):
    """Registers all the routes for the api_v1 blueprint."""

    @api_v1.route("/list_results")
    def list_results():
        try:
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

    @api_v1.route("/search_books")
    def search_books():
        query = request.args.get("query", "").strip()
        if not query or len(query) < 2:
            return jsonify({"error": "Query must be at least 2 characters"}), 400

        # Add pagination
        per_page = request.args.get("per_page", default=10, type=int)
        if not (0 < per_page <= 40):  # Google Books API limit
            return jsonify({"error": "per_page must be between 1 and 40"}), 400

        # Fetch books
        try:
            books = fetch_books_from_google(query)

            # Validate and structure the book data
            validated_books = []
            for book in books:
                try:
                    validated_books.append(BookResponse(**book).model_dump())
                except Exception as e:
                    logger.error(f"Validation error for book data: {book}. Error: {e}")
                    continue

            drive_link = save_results_to_temp_csv(validated_books, query)
            if drive_link is None:
                return jsonify({"error": "Failed to save results to CSV or upload to Google Drive"}), 500

            return jsonify({"books": validated_books, "csv_link": drive_link})
        except Exception as e:
            logger.error(f"Error in search_books: {e}")
            return jsonify({"error": str(e)}), 500

# Define create_app function
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

    # Register routes with the blueprint
    register_routes(api_v1)

    # Register the blueprint
    app.register_blueprint(api_v1)

    # Define a basic index route
    @app.route("/")
    def index():
        return "<h1>Welcome to the LibraryCloud API!</h1>"

    return app, settings

# Create app instance
app, settings = create_app()

# Define before_request function
@app.before_request
def before_request():
    g.request_id = request.headers.get('X-Request-ID', str(uuid.uuid4()))
    logger.info(f"Processing request {g.request_id}")

# Validate GOOGLE_BOOKS_API_KEY
GOOGLE_BOOKS_API_KEY = settings.GOOGLE_BOOKS_API_KEY
if not GOOGLE_BOOKS_API_KEY or (isinstance(GOOGLE_BOOKS_API_KEY, str) and GOOGLE_BOOKS_API_KEY.strip() == ""):
    raise RuntimeError("GOOGLE_BOOKS_API_KEY is not set. Application cannot start without it.")

# Ensure RESULTS_DIR exists
RESULTS_DIR = os.path.join(os.getcwd(), "learning", "Results")
os.makedirs(RESULTS_DIR, exist_ok=True)

# Define fetch_books_from_google function
@sleep_and_retry
@limits(calls=100, period=60)  # Add rate limiting
def fetch_books_from_google(query):
    """Fetch books from Google Books API with rate limiting and caching."""
    if not query or not isinstance(query, str) or len(query.strip()) == 0:
        raise ValueError("Query parameter must be a non-empty string.")

    url = f"https://www.googleapis.com/books/v1/volumes?q={query}&key={GOOGLE_BOOKS_API_KEY}"
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        return [filter_book_data(item["volumeInfo"]) for item in data.get("items", [])]
    except RateLimitException as e:
        logger.warning(f"Rate limit exceeded: {e}")
        raise
    except Exception as e:
        logger.error(f"Error fetching books: {e}")
        raise

# Log application details
logger.info(f"Results directory: {RESULTS_DIR}")
logger.info(f"Application root: {os.path.dirname(__file__)}")
logger.info(f"Running on Heroku: {bool(os.getenv('HEROKU'))}")

# Define get_drive_service function
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
        # Validate if the string is base64-encoded
        try:
            base64.b64decode(google_credentials, validate=True)
        except binascii.Error:
            logger.error("GOOGLE_APPLICATION_CREDENTIALS is not a valid base64-encoded string")
            raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS must be a valid base64-encoded string.")

        # Decode and parse credentials
        creds_json = base64.b64decode(google_credentials).decode("utf-8")
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
    except Exception as e:
        logger.error(f"Failed to upload file to Google Drive: {e}", exc_info=True)
        raise GoogleDriveError(f"Error uploading file to Google Drive: {e}")
    except Exception as e:
        logger.error(f"Failed to create Drive service: {str(e)}")
        raise GoogleDriveError(f"Drive service creation failed: {str(e)}")

# Define custom exceptions
class GoogleDriveError(Exception):
    """Custom exception for Google Drive operations"""
    pass

class BookAPIError(Exception):
    """Custom exception for Google Books API operations"""
    pass

# Define upload_to_google_drive function
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
    logger.info(f"Attempting to upload file: {file_name} from path: {file_path}")

    try:
        file_metadata = {'name': file_name}
        media = MediaFileUpload(file_path, resumable=True)
        file = service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        file_id = file.get('id')

        if not file_id:
            raise GoogleDriveError("Failed to get file ID after upload")

        # Make the file publicly accessible with retry logic
        @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
        def set_file_permissions():
            service.permissions().create(
                fileId=file_id,
                body={"role": "reader", "type": "anyone"}
            ).execute()

        try:
            set_file_permissions()
            logger.info("File permissions set, shareable link generated.")
            return f"https://drive.google.com/file/d/{file_id}/view?usp=sharing"
        except Exception as e:
            logger.error(f"Error setting file permissions: {e}", exc_info=True)
            return None

    except Exception as e:
        logger.error(f"Error uploading file: {e}")
        return None

# Define save_results_to_temp_csv function
def save_results_to_temp_csv(books, query):
    logger.info("save_results_to_temp_csv function CALLED!")
    if not books:
        logger.warning("No books found for the given query.")
        return None

    try:
        # Create a temporary file
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.csv')
        file_path = temp_file.name
        file_name = f'search_results_{query}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'

        # Validate that books is a list of dictionaries with the expected keys
        if not isinstance(books, list) or not all(isinstance(book, dict) for book in books):
            logger.error("Invalid data type for books. Expected a list of dictionaries.")
            raise ValueError("Books must be a list of dictionaries.")

        expected_keys = {"title", "authors", "description"}
        for book in books:
            if not expected_keys.issubset(book.keys()):
                logger.error(f"Book entry missing required keys: {book}")
                raise ValueError(f"Each book must contain the keys: {expected_keys}")

        with open(file_path, "w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=["title", "authors", "description"])
            writer.writeheader()
            writer.writerows(books)

        return upload_to_google_drive(file_path, file_name)
    finally:
        if 'file_path' in locals() and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError:
                logger.warning(f"Failed to remove temporary file: {file_path}")

# Pre-compile the regex pattern for truncating descriptions
DESCRIPTION_TRUNCATION_REGEX = re.compile(r'(.{400,}?\.)(?:\s|$)')

# Define filter_book_data function
def filter_book_data(volume_info):
    """Extract relevant book information and format description."""

    title = volume_info.get("title", "Unknown Title")
    authors_raw = volume_info.get("authors", ["Unknown Author"])  # Get raw authors data

    logger.info(f"Raw authors data from Google Books API: {authors_raw}")  # Log raw authors data

    # Forcefully ensure authors is ALWAYS a list of strings
    if isinstance(authors_raw, str):  # Check if authors_raw is a string
        authors = [authors_raw]       # Convert it to a list containing that string
    elif isinstance(authors_raw, list):  # If it's already a list
        authors = [str(author) for author in authors_raw]  # Convert each element to string just to be safe
    else:  # Fallback for unexpected types
        authors = ["Unknown Author"]  # Default to Unknown Author list

    logger.info(f"Processed authors data: {authors}")  # Log processed authors data

    description = volume_info.get("description", "")

    # Use pre-compiled regex to efficiently truncate the description
    if description:
        match = DESCRIPTION_TRUNCATION_REGEX.search(description)
        if match:
            description = match.group(1)

    # The regex truncation already handles description truncation.

    return {
        "title": title,
        "authors": authors,  # authors is now guaranteed to be a list of strings
        "description": description,
    }

# Define save_results_to_csv function
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

        # Create a deep copy of books to save
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

# Define validate_port function
def validate_port(port_str):
    if not port_str.isdigit():
        raise RuntimeError(f"Invalid PORT environment variable: {port_str}. Must be a numeric value.")
    port = int(port_str)
    if port <= 0 or port > 65535:
        raise ValueError("Port number must be between 1 and 65535.")
    return port

# Run the app
if __name__ == "__main__":
    port_env = os.environ.get("PORT", "5000")
    try:
        port = validate_port(port_env)
        debug_mode = os.environ.get("FLASK_ENV", "production") == "development"
        app.run(host="0.0.0.0", port=port, debug=debug_mode)
    except (ValueError, RuntimeError) as e:
        logger.error(f"Failed to start application: {e}")
        raise