from flask import Flask, request, jsonify
import requests
import os
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Google Books API Key with graceful fallback
GOOGLE_BOOKS_API_KEY = os.getenv("GOOGLE_BOOKS_API_KEY")
if not GOOGLE_BOOKS_API_KEY:
    logger.error("GOOGLE_BOOKS_API_KEY is not set. Please configure the environment variable.")

@app.route('/')
def index():
    if not GOOGLE_BOOKS_API_KEY:
        return jsonify({
            "status": "configuration_error",
            "message": "API key not configured. Please set GOOGLE_BOOKS_API_KEY environment variable."
        }), 503
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

        title = volume_info.get("title", "Unknown")
        authors = volume_info.get("authors", ["Unknown"])
        description = volume_info.get("description", "").strip()
        info_link = volume_info.get("infoLink", "#")

        # Filter out bad results
        if "Unknown" in authors or not description:
            continue
        
        books.append({
            "title": title,
            "author": ", ".join(authors),
            "published_date": volume_info.get("publishedDate", "Unknown"),
            "description": description,
            "info_link": info_link
        })

    # Pretty-print response
    return jsonify(books), 200


if __name__ == "__main__":
    port_env = os.environ.get("PORT", "5000")
    try:
        port = int(port_env)
        if port <= 0 or port > 65535:
            raise ValueError("Port number must be between 1 and 65535.")
    except ValueError:
        raise RuntimeError(f"Invalid PORT environment variable: {port_env}. Please set it to a valid integer between 1 and 65535.")
    app.run(host="0.0.0.0", port=port)
