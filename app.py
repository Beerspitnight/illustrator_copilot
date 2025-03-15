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

RESULTS_DIR = os.path.join(
    '/app' if os.getenv('HEROKU') else os.path.dirname(__file__),
    'learning',
    'Results'
)
os.makedirs(RESULTS_DIR, exist_ok=True)

for path in [
    os.path.join(os.path.dirname(__file__), 'learning', 'Results'),
    os.path.join('/app', 'learning', 'Results') if os.getenv('HEROKU') else None
]:
    if path:
        os.makedirs(path, exist_ok=True)

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

def filter_book_data(volume_info):
    """Helper function to filter and validate book data with enhanced criteria"""
    # Required fields with cleaning
    title = volume_info.get("title", "").strip()
    authors = volume_info.get("authors", [])
    description = volume_info.get("description", "").strip()
    categories = volume_info.get("categories", [])
    
    # Optional fields with defaults
    published_date = volume_info.get("publishedDate", "Unknown")
    info_link = volume_info.get("infoLink", "#")
    page_count = volume_info.get("pageCount", 0)
    
    # Validation rules
    if not title or title == "Unknown":
        return None
    
    if not authors or all(author == "Unknown" for author in authors):
        return None
    
    # Enhanced description validation
    if len(description) < 100:  # Increased minimum length
        return None
    
    # Category and keyword validation
    design_keywords = {
        'design', 'art', 'graphic', 'typography', 'layout',
        'illustration', 'creative', 'visual', 'adobe',
        'web design', 'user interface', 'ux', 'ui'
    }
    
    # Check if any design-related keywords appear in title or categories
    title_lower = title.lower()
    has_design_focus = any(keyword in title_lower for keyword in design_keywords)
    
    if categories:
        has_design_focus = has_design_focus or any(
            any(keyword in cat.lower() for keyword in design_keywords)
            for cat in categories
        )
    
    if not has_design_focus:
        return None
    
    # Minimum page count for substantive content
    if page_count and page_count < 50:
        return None
    
    # Clean and format the data
    formatted_description = description[:500]
    if len(description) > 500:
        # Find the last complete sentence within the limit
        last_period = formatted_description.rfind('.')
        if last_period > 400:  # Only trim at sentence if it's not too short
            formatted_description = formatted_description[:last_period + 1]
        formatted_description += "..."
    
    return {
        "title": title,
        "author": ", ".join(authors),
        "published_date": published_date,
        "description": formatted_description,
        "info_link": info_link,
        "categories": categories if categories else ["Uncategorized"],
        "page_count": page_count if page_count else "Unknown"
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
        
        # Ensure directory exists
        os.makedirs(RESULTS_DIR, exist_ok=True)
        
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

@app.route('/search_books', methods=['GET'])
def search_books():
    """
    Endpoint to search for books using the Google Books API.

    Query Parameters:
    - query (str): The search term to query books.

    Returns:
    - JSON response containing a list of books with their title, author(s),
      published date, description, and info link.
    - HTTP 400 if the query parameter is missing.
    - HTTP 500 if there is an issue with the Google Books API response.
    """
    query = request.args.get('query')
    if not query:
        return jsonify({"error": "Query parameter is required"}), 400

    google_books_url = "https://www.googleapis.com/books/v1/volumes"
    params = {"q": query, "key": GOOGLE_BOOKS_API_KEY}
    response = requests.get(google_books_url, params=params)

    app.logger.info("API Response received from Google Books API.")
    try:
        data = response.json()
    except ValueError:
        return jsonify({"error": "Invalid JSON response from Google Books API."}), 500

    if not isinstance(data, dict):
        return jsonify({"error": "Invalid response from Google Books API.", "raw_response": data}), 500

    if "items" not in data:
        return jsonify([]), 200

    books = []
    for item in data["items"]:
        volume_info = item.get("volumeInfo", {})
        book_data = filter_book_data(volume_info)
        
        if book_data:
            books.append(book_data)
            
        if len(books) >= 10:
            break

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
        files = os.listdir(RESULTS_DIR)
        csv_files = [f for f in files if f.endswith('.csv')]
        return jsonify({
            "files": csv_files,
            "count": len(csv_files),
            "directory": RESULTS_DIR
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/download_results/<filename>', methods=['GET'])
def download_results(filename):
    """Download a specific CSV file"""
    try:
        return send_from_directory(
            RESULTS_DIR,
            filename,
            as_attachment=True,
            mimetype='text/csv'
        )
    except Exception as e:
        return jsonify({"error": f"File not found: {str(e)}"}), 404

if __name__ == "__main__":
    port_env = os.environ.get("PORT", "5000")
    try:
        port = int(port_env)
        if port <= 0 or port > 65535:
            raise ValueError("Port number must be between 1 and 65535.")
    except ValueError:
        raise RuntimeError(f"Invalid PORT environment variable: {port_env}. Please set it to a valid integer between 1 and 65535.")
    app.run(host="0.0.0.0", port=port)
