# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file for Finn Desktop

import os
import sys
import certifi
from PyInstaller.utils.hooks import collect_all, collect_submodules

# Docling is an optional OCR fallback. If the user has installed it into the
# build venv we bundle it; otherwise we ship a slimmer build (~700 MB lighter).
# The runtime already guards docling imports behind DOCLING_AVAILABLE.
try:
    docling_datas, docling_binaries, docling_hiddenimports = collect_all('docling')
    docling_models_datas, docling_models_binaries, docling_models_hiddenimports = collect_all('docling_core')
    docling_extra_hiddenimports = [
        'docling',
        'docling.document_converter',
        'docling.datamodel',
        'docling.datamodel.base_models',
        'docling.datamodel.document',
        'docling.pipeline',
        'docling.pipeline.standard_pdf_pipeline',
        'docling_core',
        'docling_core.types',
        'docling_core.types.doc',
    ]
except Exception:
    docling_datas, docling_binaries, docling_hiddenimports = [], [], []
    docling_models_datas, docling_models_binaries, docling_models_hiddenimports = [], [], []
    docling_extra_hiddenimports = []

# Get paths
spec_root = os.path.abspath(SPECPATH)
project_root = os.path.dirname(spec_root)

block_cipher = None

# Get certifi CA bundle path for SSL
certifi_path = os.path.dirname(certifi.__file__)

# Explicitly exclude runtime user data from desktop bundles.
project_data_dir = os.path.normpath(os.path.join(project_root, 'data'))

# Third-party license manifest (generated at build time by
# desktop/generate_third_party_licenses.py). If it's missing, the build
# scripts forgot to run the generator — we fall back gracefully and warn.
third_party_licenses = os.path.join(project_root, 'THIRD_PARTY_LICENSES.txt')
third_party_licenses_entry = (
    [(third_party_licenses, '.')] if os.path.exists(third_party_licenses) else []
)
if not third_party_licenses_entry:
    print('WARNING: THIRD_PARTY_LICENSES.txt missing — run desktop/generate_third_party_licenses.py before PyInstaller')

def _is_project_runtime_data(src_path):
    abs_src = os.path.normpath(os.path.abspath(src_path))
    return abs_src == project_data_dir or abs_src.startswith(project_data_dir + os.sep)

a = Analysis(
    [
        os.path.join(spec_root, 'finn_desktop.py'),
        os.path.join(project_root, 'main.py'),  # Include main FastAPI app
    ],
    pathex=[project_root],
    binaries=[] + docling_binaries + docling_models_binaries,
    datas=[entry for entry in (third_party_licenses_entry + [
        # Include the entire static folder (frontend build)
        (os.path.join(project_root, 'static'), 'static'),
        # NOTE: data/ directory is NOT included - it's created at runtime
        # This prevents accidentally bundling user data, API keys, or indexed documents
        # Include .env.example as template
        (os.path.join(project_root, '.env.example'), '.'),
        # Include desktop icon for tray
        (os.path.join(spec_root, 'icon.ico'), 'desktop'),
        # Include all Python source directories
        (os.path.join(project_root, 'services'), 'services'),
        (os.path.join(project_root, 'models'), 'models'),
        # Include SSL certificates for HuggingFace downloads
        (os.path.join(certifi_path, 'cacert.pem'), 'certifi'),
        # Include corporate CA certificates (for environments behind corporate proxies/firewalls)
        (os.path.join(project_root, 'certs'), 'certs'),
    ] + docling_datas + docling_models_datas) if not _is_project_runtime_data(entry[0])],
    hiddenimports=[
        # Main app and dependencies
        'main',
        'config',
        # Application modules
        'services',
        'services.ai_service',
        'services.embedder',
        'services.vector_store',
        'services.metadata_store',
        'services.document_extractor',
        'services.chunker',
        'services.indexer_manager',
        'services.collection_service',
        'services.backup_service',
        'services.reindex_service',
        'services.config_manager',
        'services.app_database',
        'services.mcp_server',
        'services.indexing',
        'services.indexing.indexer',
        'models',
        'models.schemas',
        # Uvicorn
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        # AI providers
        'anthropic',
        'openai',
        # Document processing
        'pypdf',
        'pdfplumber',
        'docx',
        'pandas',
        # ML/Vector
        'sentence_transformers',
        'faiss',
        'torch',
        'transformers',
        'huggingface_hub',
        # SSL/Certificates
        'certifi',
        'ssl',
        # FastAPI dependencies
        'starlette.middleware',
        'starlette.middleware.cors',
        # MCP server
        'mcp',
        'mcp.server',
        'mcp.server.fastmcp',
    ] + docling_extra_hiddenimports + docling_hiddenimports + docling_models_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Finn',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # Hidden when launched by Electron; stdout/stderr piped to the parent.
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(project_root, 'desktop', 'icon.ico') if os.path.exists(os.path.join(project_root, 'desktop', 'icon.ico')) else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Finn',
)
