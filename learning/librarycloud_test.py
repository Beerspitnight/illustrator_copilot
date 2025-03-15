import requests
import sys
import csv
import os
import urllib.parse

def extract_first_string(value, key=None):
    """Extracts clean text from complex nested structures."""
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                # Check for title with subtitle
                if "title" in item and "subTitle" in item:
                    return f"{item['title']} - {item['subTitle']}"
                # Try direct key lookup
                if key and key in item:
                    return str(item[key]).strip()
                # Try #text field
                if "#text" in item:
                    return str(item["#text"]).strip()
            elif isinstance(item, str):
                return item.strip()
    elif isinstance(value, dict):
        # Handle title with subtitle
        if "title" in value and "subTitle" in value:
            return f"{value['title']} - {value['subTitle']}"
        # Try direct key lookup
        if key and key in value:
            return str(value[key]).strip()
        # Try #text field
        if "#text" in value:
            return str(value["#text"]).strip()
    return str(value).strip() if value else "Unknown"

def extract_year(date_info):
    """Extract year from date information."""
    if isinstance(date_info, dict):
        if "dateIssued" in date_info:
            date = date_info["dateIssued"]
            if isinstance(date, list):
                for d in date:
                    if isinstance(d, dict) and "#text" in d:
                        return ''.join(filter(str.isdigit, d["#text"]))
                    elif isinstance(d, str):
                        return ''.join(filter(str.isdigit, d))
            elif isinstance(date, str):
                return ''.join(filter(str.isdigit, date))
    return ""

# Define the 12 principles of design as search queries
search_queries = [
    "contrast", "balance", "emphasis", "proportion", "hierarchy", 
    "repetition", "rhythm", "pattern", "white space", "movement", 
    "variety", "unity"
]
# Create results directory if it doesn't exist
results_dir = os.path.join(os.getcwd(), 'results')
os.makedirs(results_dir, exist_ok=True)

for search_query in search_queries:
    print(f"\n🔍 Searching for: {search_query}")

    url = f"https://api.lib.harvard.edu/v2/items.json?title={search_query}&limit=5"
    encoded_query = urllib.parse.quote(search_query)
    url = f"https://api.lib.harvard.edu/v2/items.json?title={encoded_query}"

    # Send request
    response = requests.get(url)

    # Check response
    if response.status_code == 200:
        data = response.json()  # Ensure JSON decoding is consistent
        
        # Define output file name for each search term
        output_file = os.path.join(results_dir, f"library_results_{search_query.replace(' ', '_')}.csv")

        # Open CSV file for writing
        with open(output_file, mode="w", newline="", encoding="utf-8") as file:  # Encoding explicitly specified
            writer = csv.writer(file)
            writer.writerow(["No.", "Title", "Author", "Year"])  # CSV Header
            
            print(f"\n📚 Search Results for '{search_query}':\n")
            print("{:<5} {:<50} {:<30} {:<10}".format("No.", "Title", "Author", "Year"))
            print("-" * 100)
            
            mods = data.get("items", {}).get("mods", [])
            if not isinstance(mods, list):
                print(f"⚠️ No valid results found for '{search_query}'.")
                continue

            for i, item in enumerate(mods[:20]):
                # Extract title
                title_info = item.get("titleInfo", {})
                title = extract_first_string(title_info, "title")
                
                # Extract author
                name_info = item.get("name", [])
                if isinstance(name_info, list):
                    author = next((extract_first_string(n, "namePart") 
                                 for n in name_info 
                                 if isinstance(n, dict) and "namePart" in n), "Unknown")
                else:
                    author = extract_first_string(name_info, "namePart")
                
                # Extract year
                origin_info = item.get("originInfo", {})
                year = extract_year(origin_info)

                # Clean and format output
                title = title[:50].strip()
                author = author[:30].strip()
                year = year[:4].strip()

                # Print formatted output
                formatted_output = (
                    f"{i+1:<5} {title:<50} {author:<30} {year:<10}"
                )
                print(formatted_output)

                # Write to CSV
                writer.writerow([i+1, title, author, year])

        print(f"\n✅ Results saved to: {output_file}")

    else:
        print(f"❌ Error fetching data for '{search_query}':", response.status_code)

