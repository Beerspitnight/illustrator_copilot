from flask import Flask, request, jsonify
import requests
import os

app = Flask(__name__)

# Google Books API Key from Heroku Config Vars
GOOGLE_BOOKS_API_KEY = os.getenv("GOOGLE_BOOKS_API_KEY")

@app.route('/')
def index():
    return "Hello, Illustrator Co-Pilot!"

@app.route('/search_books', methods=['GET'])
def search_books():
    query = request.args.get('query')
    if not query:
        return jsonify({"error": "Please provide a search term."}), 400

    google_books_url = f"https://www.googleapis.com/books/v1/volumes?q={query}&key={GOOGLE_BOOKS_API_KEY}"

    response = requests.get(google_books_url)
    data = response.json()

    books = []
    if "items" in data:
        for item in data["items"]:
            volume_info = item.get("volumeInfo", {})
            books.append({
                "title": volume_info.get("title", "No title available"),
                "author": ", ".join(volume_info.get("authors", ["Unknown"])),
                "published_date": volume_info.get("publishedDate", "Unknown"),
                "description": volume_info.get("description", "No description available"),
                "info_link": volume_info.get("infoLink", "#")
            })

    return jsonify(books)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
