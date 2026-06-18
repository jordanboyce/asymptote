# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file for Asymptote Desktop

import os
import sys
import certifi
from PyInstaller.utils.hooks import collect_all, collect_submodules

# Collect all docling files (data, binaries, submodules)
docling_datas, docling_binaries, docling_hiddenimports = collect_all('docling')
docling_models_datas, docling_models_binaries, docling_models_hiddenimports = collect_all('docling_core')

# Collect faster-whisper / ctranslate2 (native libs PyInstaller often misses)
try:
    ctranslate2_datas, ctranslate2_binaries, ctranslate2_hiddenimports = collect_all('ctranslate2')
except Exception:
    ctranslate2_datas, ctranslate2_binaries, ctranslate2_hiddenimports = [], [], []
try:
    faster_whisper_datas, faster_whisper_binaries, faster_whisper_hiddenimports = collect_all('faster_whisper')
except Exception:
    faster_whisper_datas, faster_whisper_binaries, faster_whisper_hiddenimports = [], [], []

# Get paths
spec_root = os.path.abspath(SPECPATH)
project_root = os.path.dirname(spec_root)

block_cipher = None

# Get certifi CA bundle path for SSL
certifi_path = os.path.dirname(certifi.__file__)

# Explicitly exclude runtime user data from desktop bundles.
project_data_dir = os.path.normpath(os.path.join(project_root, 'data'))

def _is_project_runtime_data(src_path):
    abs_src = os.path.normpath(os.path.abspath(src_path))
    return abs_src == project_data_dir or abs_src.startswith(project_data_dir + os.sep)

a = Analysis(
    [
        os.path.join(spec_root, 'asymptote_desktop.py'),
        os.path.join(project_root, 'main.py'),  # Include main FastAPI app
    ],
    pathex=[project_root],
    binaries=(
        []
        + docling_binaries
        + docling_models_binaries
        + ctranslate2_binaries
        + faster_whisper_binaries
    ),
    datas=[entry for entry in ([
        # Frontend build
        (os.path.join(project_root, 'static'), 'static'),
        # NOTE: data/ is NOT included — created at runtime to avoid bundling user data
        # Config template
        (os.path.join(project_root, '.env.example'), '.'),
        # Desktop tray icon
        (os.path.join(spec_root, 'icon.ico'), 'desktop'),
        # Application source directories
        (os.path.join(project_root, 'services'), 'services'),
        (os.path.join(project_root, 'models'), 'models'),
        (os.path.join(project_root, 'middleware'), 'middleware'),
        # SSL certificates for HuggingFace downloads
        (os.path.join(certifi_path, 'cacert.pem'), 'certifi'),
        # Corporate CA certificates (for environments behind proxies/firewalls)
        (os.path.join(project_root, 'certs'), 'certs'),
    ]
    + docling_datas
    + docling_models_datas
    + ctranslate2_datas
    + faster_whisper_datas
    ) if not _is_project_runtime_data(entry[0])],
    hiddenimports=[
        # Entry points
        'main',
        'config',

        # ── Core services ──────────────────────────────────────────────────
        'services',
        'services.ai_service',
        'services.agent_tools',
        'services.app_database',
        'services.audio_transcriber',
        'services.backup_service',
        'services.bm25_service',
        'services.brief_generator',
        'services.chunker',
        'services.code_extractor',
        'services.collection_overview',
        'services.collection_service',
        'services.config_manager',
        'services.db_backend',
        'services.db_postgres',
        'services.document_extractor',
        'services.embedder',
        'services.expertise_store',
        'services.file_picker',
        'services.form_field_extractor',
        'services.index_repair',
        'services.indexer_manager',
        'services.indexing',
        'services.indexing.indexer',
        'services.llm_role_inference',
        'services.mcp_server',
        'services.metadata_store',
        'services.ocr_engine',
        'services.prompt_injection_detector',
        'services.reindex_service',
        'services.sharing_service',
        'services.structured_chat',
        'services.structured_store',
        'services.upload_service',
        'services.vector_store',

        # ── Market data ───────────────────────────────────────────────────
        'services.market_data',
        'services.market_data.classification',
        'services.market_data.company',
        'services.market_data.corporate_events',
        'services.market_data.enrich',
        'services.market_data.price_history',

        # ── Financial helpers ─────────────────────────────────────────────
        'services.financial',
        'services.financial.metrics',
        'services.financial.roles',
        'services.financial.type_hints',

        # ── Ingest profiles (vendor schemas) ─────────────────────────────
        'services.ingest_profiles',
        'services.ingest_profiles.netx360',

        # ── Middleware ────────────────────────────────────────────────────
        'middleware',
        'middleware.user_context',

        # ── Models ───────────────────────────────────────────────────────
        'models',
        'models.schemas',

        # ── Uvicorn ──────────────────────────────────────────────────────
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

        # ── FastAPI / Starlette ───────────────────────────────────────────
        'starlette.middleware',
        'starlette.middleware.cors',
        'starlette.routing',

        # ── AI providers ─────────────────────────────────────────────────
        'anthropic',
        'openai',
        'httpx',

        # ── Document processing ───────────────────────────────────────────
        'pypdf',
        'pdfplumber',
        'docx',
        'pandas',
        'openpyxl',

        # ── ML / Vector search ────────────────────────────────────────────
        'sentence_transformers',
        'faiss',
        'torch',
        'transformers',
        'huggingface_hub',

        # ── Audio transcription ───────────────────────────────────────────
        'faster_whisper',
        'ctranslate2',

        # ── Market data ───────────────────────────────────────────────────
        'yfinance',
        'yfinance.base',

        # ── MCP server ────────────────────────────────────────────────────
        'mcp',
        'mcp.server',
        'mcp.server.fastmcp',

        # ── OCR — docling ─────────────────────────────────────────────────
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

        # ── SSL / Certificates ────────────────────────────────────────────
        'certifi',
        'ssl',
    ]
    + docling_hiddenimports
    + docling_models_hiddenimports
    + ctranslate2_hiddenimports
    + faster_whisper_hiddenimports,
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
    name='Asymptote',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,  # Set to False to hide console window
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
    name='Asymptote',
)
