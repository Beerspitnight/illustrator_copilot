from flask import Flask, request, jsonify, send_from_directory
import requests
import os
import logging
import csv
from datetime import datetime

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Google Books API Key with graceful fallback
GOOGLE_BOOKS_API_KEY = os.getenv("GOOGLE_BOOKS_API_KEY")
if not GOOGLE_BOOKS_API_KEY:
    logger.error("GOOGLE_BOOKS_API_KEY is not set. Please configure the environment variable.")
    raise RuntimeError("GOOGLE_BOOKS_API_KEY is not set. Application cannot start without it.")
RESULTS_DIR = os.path.join(os.getcwd(), "learning", "Results")
os.makedirs(RESULTS_DIR, exist_ok=True)  # Ensure directory exists

logger.info(f"Results directory: {RESULTS_DIR}")
logger.info(f"Application root: {os.path.dirname(__file__)}")
logger.info(f"Running on Heroku: {bool(os.getenv('HEROKU'))}")

@app.route('/')
def index():
    if not GOOGLE_BOOKS_API_KEY:
        return jsonify({
            "status": "configuration_error",
            "message": "API key not configured. Please set GOOGLE_BOOKS_API_KEY environment variable."
        }), 503
    return jsonify({"status": "running", "message": "Welcome to the Ill-Co-P Learns API!"}), 200

def filter_book_data(volume_info):
    """Extract relevant book information and format description."""
    
    title = volume_info.get("title", "Unknown Title")
    authors = volume_info.get("authors", ["Unknown Author"])
    description = volume_info.get("description", "")

    # Initialize last_period with a default value
    last_period = -1

    # Only try to find the last period if we have a description
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
        
        fieldnames = ['title', 'author', 'published_date', 'description', 
                     'info_link', 'categories', 'page_count']
        
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

@app.route("/search_books", methods=["GET"])
def search_books():
    query = request.args.get("query", "")
    if not query:
        return jsonify({"error": "No search query provided"}), 400

    google_books_url = "https://www.googleapis.com/books/v1/volumes"
    params = {"q": query, "key": GOOGLE_BOOKS_API_KEY}
    response = requests.get(google_books_url, params=params)

    app.logger.info("API Response received from Google Books API.")
    try:
        data = response.json()
    except ValueError:
        return jsonify({"error": "Invalid JSON response from Google Books API."}), 500

    if not isinstance(data, dict):
        return jsonify({
            "error": "Unexpected response format from Google Books API. Expected a dictionary.",
            "raw_response": data
        }), 500

    if "items" not in data:
        return jsonify({"error": "No books found"}), 404
        
    books = []
    for item in data["items"]:
        if len(books) >= 10:  # Stop iterating once 10 books are collected
            break
        volume_info = item.get("volumeInfo", {})
        book_data = filter_book_data(volume_info)
        if book_data:
            books.append(book_data)

    # Save results to CSV and handle the response
    csv_filename = None
    if books:  # Only try to save if we have results
        csv_filename = save_results_to_csv(books, query)
        if csv_filename:
            logger.info(f"CSV file created: {csv_filename}")
        else:
            logger.warning("Failed to create CSV file")

    response_data = {
        "books": books,
        "total_results": len(books),
    }
    
    if csv_filename:
        response_data["csv_file"] = csv_filename
        response_data["csv_path"] = os.path.join(RESULTS_DIR, csv_filename)

    logger.info(f"Returning {len(books)} filtered results")
    return jsonify(response_data), 200

@app.route('/list_results', methods=['GET'])
def list_results():
    """List all saved CSV results files"""
    try:
        if not os.path.exists(RESULTS_DIR):
            return jsonify({
                "error": "Results directory does not exist.",
                "directory": RESULTS_DIR
            }), 404
        result_files = [f for f in os.listdir(RESULTS_DIR) if f.endswith('.csv')]
        return jsonify({
            "files": result_files,
            "count": len(result_files),
            "directory": RESULTS_DIR
        })
    except Exception as e:
    except Exception as e:
        return jsonify({"error": str(e)}), 500
if __name__ == "__main__":
    port_env = os.environ.get("PORT", "5000")
    try:
        port = int(port_env)
        if port <= 0 or port > 65535:
            raise ValueError("Port number must be between 1 and 65535.")
    except ValueError:
        raise RuntimeError(f"Invalid PORT environment variable: {port_env}. Please set it to a valid integer between 1 and 65535.")
    app.run(host="0.0.0.0", port=port)
