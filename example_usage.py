"""
Example usage of Asymptote API

This script demonstrates how to interact with the Asymptote API programmatically.
Make sure the server is running before executing this script.
"""

import requests
from pathlib import Path


# API base URL
BASE_URL = "http://localhost:8000"


def upload_document(pdf_path: Path):
    """Upload a PDF document to Asymptote."""
    url = f"{BASE_URL}/documents/upload"

    with open(pdf_path, "rb") as f:
        files = {"files": (pdf_path.name, f, "application/pdf")}
        response = requests.post(url, files=files)

    if response.status_code == 201:
        print(f"✓ Uploaded {pdf_path.name}")
        data = response.json()
        print(f"  Pages: {data['total_pages']}, Chunks: {data['total_chunks']}")
        return data
    else:
        print(f"✗ Failed to upload {pdf_path.name}")
        print(f"  Error: {response.text}")
        return None


def search_documents(query: str, top_k: int = 5):
    """Search indexed documents."""
    url = f"{BASE_URL}/search"

    payload = {
        "query": query,
        "top_k": top_k
    }

    response = requests.post(url, json=payload)

    if response.status_code == 200:
        data = response.json()
        print(f"\n🔍 Search: '{query}'")
        print(f"Found {data['total_results']} results:\n")

        for i, result in enumerate(data["results"], 1):
            print(f"{i}. {result['filename']} (page {result['page_number']})")
            print(f"   Score: {result['similarity_score']:.3f}")
            print(f"   Snippet: {result['text_snippet'][:100]}...")
            print(f"   📄 PDF: {result['pdf_url']}")
            print(f"   🔗 Page: {result['page_url']}")
            print()

        return data
    else:
        print(f"✗ Search failed: {response.text}")
        return None


def list_documents():
    """List all indexed documents."""
    url = f"{BASE_URL}/documents"

    response = requests.get(url)

    if response.status_code == 200:
        data = response.json()
        print(f"\n📚 Indexed Documents ({data['total_documents']}):\n")

        for doc in data["documents"]:
            print(f"- {doc['filename']}")
            print(f"  ID: {doc['document_id']}")
            print(f"  Pages: {doc['num_pages']}, Chunks: {doc['num_chunks']}")
            print()

        return data
    else:
        print(f"✗ Failed to list documents: {response.text}")
        return None


def delete_document(document_id: str):
    """Delete a document from the index."""
    url = f"{BASE_URL}/documents/{document_id}"

    response = requests.delete(url)

    if response.status_code == 200:
        data = response.json()
        print(f"✓ Deleted document {document_id}")
        print(f"  Chunks removed: {data['chunks_deleted']}")
        return data
    else:
        print(f"✗ Failed to delete document: {response.text}")
        return None


def main():
    """Example usage workflow."""
    print("=" * 60)
    print("Asymptote API - Example Usage")
    print("=" * 60)

    # 1. Upload a document (replace with your PDF path)
    # pdf_path = Path("path/to/your/document.pdf")
    # if pdf_path.exists():
    #     upload_document(pdf_path)
    # else:
    #     print("⚠ No PDF file found. Please update the path in the script.")

    # 2. List all documents
    list_documents()

    # 3. Search for something
    search_documents("machine learning algorithms", top_k=3)

    # 4. Search with a different query
    search_documents("data preprocessing techniques", top_k=3)

    # 5. Delete a document (uncomment and add document_id)
    # delete_document("your-document-id-here")


if __name__ == "__main__":
    main()
