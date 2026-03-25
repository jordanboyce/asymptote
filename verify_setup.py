"""
Setup verification script for Asymptote API

Run this script to verify your installation is working correctly.
"""

import sys
from pathlib import Path


def check_python_version():
    """Verify Python version is 3.8+"""
    print("Checking Python version...", end=" ")
    version = sys.version_info
    if version.major >= 3 and version.minor >= 8:
        print(f"✓ Python {version.major}.{version.minor}.{version.micro}")
        return True
    else:
        print(f"✗ Python {version.major}.{version.minor}.{version.micro} (need 3.8+)")
        return False


def check_dependencies():
    """Verify required packages are installed"""
    print("Checking dependencies...", end=" ")
    required = [
        "fastapi",
        "uvicorn",
        "pypdf",
        "pdfplumber",
        "sentence_transformers",
        "faiss",
        "pydantic",
    ]

    missing = []
    for package in required:
        try:
            __import__(package)
        except ImportError:
            missing.append(package)

    if not missing:
        print(f"✓ All packages installed")
        return True
    else:
        print(f"✗ Missing: {', '.join(missing)}")
        print("  Run: pip install -r requirements.txt")
        return False


def check_project_structure():
    """Verify project structure is correct"""
    print("Checking project structure...", end=" ")
    required_files = [
        "main.py",
        "config.py",
        "requirements.txt",
        "models/schemas.py",
        "services/document_extractor.py",
        "services/chunker.py",
        "services/embedder.py",
        "services/vector_store.py",
        "services/indexing/indexer.py",
    ]

    missing = []
    for file_path in required_files:
        if not Path(file_path).exists():
            missing.append(file_path)

    if not missing:
        print("✓ All files present")
        return True
    else:
        print(f"✗ Missing files: {', '.join(missing)}")
        return False


def check_data_directory():
    """Verify data directory setup"""
    print("Checking data directory...", end=" ")
    data_dir = Path("./data")

    if not data_dir.exists():
        data_dir.mkdir(parents=True)
        (data_dir / "documents").mkdir()
        (data_dir / "indexes").mkdir()
        print("✓ Created data directory")
    else:
        print("✓ Data directory exists")

    return True


def test_import():
    """Test importing main modules"""
    print("Testing module imports...", end=" ")
    try:
        from config import settings
        from services.document_extractor import DocumentExtractor
        from services.chunker import TextChunker
        from services.embedder import EmbeddingService
        from services.vector_store import VectorStore
        from models.schemas import SearchRequest, SearchResponse
        print("✓ All modules import successfully")
        return True
    except Exception as e:
        print(f"✗ Import failed: {e}")
        return False


def main():
    """Run all verification checks"""
    print("=" * 60)
    print("Asymptote API - Setup Verification")
    print("=" * 60)
    print()

    checks = [
        check_python_version(),
        check_dependencies(),
        check_project_structure(),
        check_data_directory(),
        test_import(),
    ]

    print()
    print("=" * 60)

    if all(checks):
        print("✓ All checks passed! You're ready to run Asymptote.")
        print()
        print("To start the server, run:")
        print("  python main.py")
        print()
        print("Or use the startup script:")
        print("  ./run.sh    (Linux/Mac)")
        print("  run.bat     (Windows)")
        print()
        return 0
    else:
        print("✗ Some checks failed. Please fix the issues above.")
        print()
        return 1


if __name__ == "__main__":
    sys.exit(main())
