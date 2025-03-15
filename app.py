from flask import Flask, request, jsonify
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

RESULTS_DIR = os.path.join(os.path.dirname(__file__), 'learning', 'Results')
os.makedirs(RESULTS_DIR, exist_ok=True)

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
    """Save search results to a CSV file"""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'search_results_{timestamp}.csv'
    filepath = os.path.join(RESULTS_DIR, filename)
    
    fieldnames = ['title', 'author', 'published_date', 'description', 
                 'info_link', 'categories', 'page_count']
    
    try:
        with open(filepath, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            for book in books:
                # Convert categories list to string for CSV
                book['categories'] = ', '.join(book['categories'])
                writer.writerow(book)
        logger.info(f"Search results saved to {filepath}")
        return filename
    except Exception as e:
        logger.error(f"Error saving results to CSV: {e}")
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
            
        # Limit results to prevent overwhelming responses
        if len(books) >= 10:
            break

    # Save results to CSV
    csv_filename = save_results_to_csv(books, query)
    
    logger.info(f"Filtered {len(data['items'])} books to {len(books)} quality results")
    return jsonify({
        "books": books,
        "csv_file": csv_filename,
        "total_results": len(books)
    }), 200


if __name__ == "__main__":
    port_env = os.environ.get("PORT", "5000")
    try:
        port = int(port_env)
        if port <= 0 or port > 65535:
            raise ValueError("Port number must be between 1 and 65535.")
    except ValueError:
        raise RuntimeError(f"Invalid PORT environment variable: {port_env}. Please set it to a valid integer between 1 and 65535.")
    app.run(host="0.0.0.0", port=port)
