from flask import Flask, request, jsonify
import requests
import os

app = Flask(__name__)

# Google Books API Key from Heroku Config Vars
GOOGLE_BOOKS_API_KEY = os.getenv("GOOGLE_BOOKS_API_KEY")
if not GOOGLE_BOOKS_API_KEY:
    raise RuntimeError("GOOGLE_BOOKS_API_KEY environment variable is not set. Please configure it before running the application.")

@app.route('/')
def index():
    return "Hello, Illustrator Co-Pilot!"

@app.route('/search_books', methods=['GET'])
def search_books():
    query = request.args.get('query')
    if not query:
        return jsonify({"error": "Query parameter is required"}), 400
        
    google_books_url = "https://www.googleapis.com/books/v1/volumes"
    params = {"q": query, "key": GOOGLE_BOOKS_API_KEY}
    response = requests.get(google_books_url, params=params)

    app.logger.debug("API Response received from Google Books API.")
    try:
        data = response.json()
    except ValueError:
        return jsonify({"error": "Invalid JSON response from Google Books API."}), 500

    if not isinstance(data, dict) or "items" not in data:
        return jsonify({"error": "No books found or API issue.", "raw_response": data}), 500
        return jsonify({"error": "No books found or API issue.", "raw_response": data}), 500

    books = []
    for item in data["items"]:
        volume_info = item.get("volumeInfo", {})
        books.append({
            "title": volume_info.get("title", "Unknown"),
            "author": ", ".join(volume_info.get("authors", ["Unknown"])),
            "published_date": volume_info.get("publishedDate", "Unknown"),
            "description": volume_info.get("description", "No description available"),
            "info_link": volume_info.get("infoLink", "#")
        })

    return jsonify(books)


if __name__ == "__main__":
    port_env = os.environ.get("PORT", "5000")
    try:
        port = int(port_env)
        if port <= 0 or port > 65535:
            raise ValueError("Port number must be between 1 and 65535.")
    except ValueError:
        raise RuntimeError(f"Invalid PORT environment variable: {port_env}. Please set it to a valid integer between 1 and 65535.")
    app.run(host="0.0.0.0", port=port)
