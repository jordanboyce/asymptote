"""Code extractor for source files across multiple programming languages.

This module provides intelligent code-aware text extraction and chunking
for codebases, preserving symbol boundaries and extracting metadata
useful for RAG-based code understanding.

Supported languages:
- Curly-brace: JavaScript, TypeScript, C#, Java, Go, Rust, C, C++, PHP, Swift, Kotlin
- Indentation-based: Python, Ruby
- Legacy: Pascal, Delphi, Modula-2, Assembly
"""

import re
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum


class CodeLanguage(Enum):
    """Supported code languages."""
    # Modern languages
    PYTHON = "python"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    CSHARP = "csharp"
    JAVA = "java"
    GO = "go"
    RUST = "rust"
    C = "c"
    CPP = "cpp"
    PHP = "php"
    RUBY = "ruby"
    SWIFT = "swift"
    KOTLIN = "kotlin"
    SCALA = "scala"
    # Legacy languages
    PASCAL = "pascal"
    DELPHI = "delphi"
    MODULA2 = "modula2"
    ASSEMBLY = "assembly"
    # Fallback
    UNKNOWN = "unknown"


@dataclass
class CodeSymbol:
    """Represents a code symbol (procedure, function, class, etc.)."""
    name: str
    symbol_type: str  # procedure, function, class, record, unit, module, label, etc.
    line_start: int
    line_end: int
    text: str
    unit_name: Optional[str] = None
    parent_symbol: Optional[str] = None  # For nested symbols
    parameters: Optional[str] = None
    return_type: Optional[str] = None
    visibility: Optional[str] = None  # public, private, protected
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CodeChunk:
    """A chunk of code with metadata for indexing."""
    chunk_id: str
    document_id: str
    filename: str
    text: str
    line_start: int
    line_end: int
    language: str
    unit_name: Optional[str] = None
    symbol_name: Optional[str] = None
    symbol_type: Optional[str] = None
    parent_symbol: Optional[str] = None
    chunk_index: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


class CodeExtractor:
    """Extracts and chunks code from various programming language files."""

    # File extension to language mapping
    EXTENSION_MAP = {
        # Python
        '.py': CodeLanguage.PYTHON,
        '.pyw': CodeLanguage.PYTHON,
        '.pyi': CodeLanguage.PYTHON,  # Type stubs
        # JavaScript/TypeScript
        '.js': CodeLanguage.JAVASCRIPT,
        '.jsx': CodeLanguage.JAVASCRIPT,
        '.mjs': CodeLanguage.JAVASCRIPT,
        '.cjs': CodeLanguage.JAVASCRIPT,
        '.ts': CodeLanguage.TYPESCRIPT,
        '.tsx': CodeLanguage.TYPESCRIPT,
        '.mts': CodeLanguage.TYPESCRIPT,
        '.cts': CodeLanguage.TYPESCRIPT,
        # C#
        '.cs': CodeLanguage.CSHARP,
        # Java
        '.java': CodeLanguage.JAVA,
        # Go
        '.go': CodeLanguage.GO,
        # Rust
        '.rs': CodeLanguage.RUST,
        # C/C++
        '.c': CodeLanguage.C,
        '.h': CodeLanguage.C,
        '.cpp': CodeLanguage.CPP,
        '.cxx': CodeLanguage.CPP,
        '.cc': CodeLanguage.CPP,
        '.hpp': CodeLanguage.CPP,
        '.hxx': CodeLanguage.CPP,
        '.hh': CodeLanguage.CPP,
        # PHP
        '.php': CodeLanguage.PHP,
        '.phtml': CodeLanguage.PHP,
        # Ruby
        '.rb': CodeLanguage.RUBY,
        '.rake': CodeLanguage.RUBY,
        '.gemspec': CodeLanguage.RUBY,
        # Swift
        '.swift': CodeLanguage.SWIFT,
        # Kotlin
        '.kt': CodeLanguage.KOTLIN,
        '.kts': CodeLanguage.KOTLIN,
        # Scala
        '.scala': CodeLanguage.SCALA,
        '.sc': CodeLanguage.SCALA,
        # Pascal/Delphi
        '.pas': CodeLanguage.DELPHI,
        '.dpr': CodeLanguage.DELPHI,  # Delphi project
        '.dpk': CodeLanguage.DELPHI,  # Delphi package
        '.pp': CodeLanguage.PASCAL,   # Free Pascal
        '.inc': CodeLanguage.DELPHI,  # Include files
        '.dfm': CodeLanguage.DELPHI,  # Delphi form (treat as text)
        # Modula-2
        '.mod': CodeLanguage.MODULA2,
        '.def': CodeLanguage.MODULA2,  # Definition module
        '.mi': CodeLanguage.MODULA2,   # Implementation module
        # Assembly
        '.asm': CodeLanguage.ASSEMBLY,
        '.s': CodeLanguage.ASSEMBLY,
    }

    # Patterns for Pascal/Delphi
    DELPHI_PATTERNS = {
        'unit': re.compile(
            r'^\s*unit\s+(\w+)\s*;',
            re.IGNORECASE | re.MULTILINE
        ),
        'program': re.compile(
            r'^\s*program\s+(\w+)\s*;',
            re.IGNORECASE | re.MULTILINE
        ),
        'library': re.compile(
            r'^\s*library\s+(\w+)\s*;',
            re.IGNORECASE | re.MULTILINE
        ),
        'interface': re.compile(
            r'^\s*interface\s*$',
            re.IGNORECASE | re.MULTILINE
        ),
        'implementation': re.compile(
            r'^\s*implementation\s*$',
            re.IGNORECASE | re.MULTILINE
        ),
        'procedure': re.compile(
            r'^\s*(class\s+)?(procedure|constructor|destructor)\s+(\w+(?:\.\w+)?)\s*(\([^)]*\))?\s*;',
            re.IGNORECASE | re.MULTILINE
        ),
        'function': re.compile(
            r'^\s*(class\s+)?function\s+(\w+(?:\.\w+)?)\s*(\([^)]*\))?\s*:\s*(\w+)\s*;',
            re.IGNORECASE | re.MULTILINE
        ),
        'class': re.compile(
            r'^\s*(\w+)\s*=\s*class\s*(\([^)]*\))?\s*$',
            re.IGNORECASE | re.MULTILINE
        ),
        'record': re.compile(
            r'^\s*(\w+)\s*=\s*(packed\s+)?record\s*$',
            re.IGNORECASE | re.MULTILINE
        ),
        'type_section': re.compile(
            r'^\s*type\s*$',
            re.IGNORECASE | re.MULTILINE
        ),
        'var_section': re.compile(
            r'^\s*var\s*$',
            re.IGNORECASE | re.MULTILINE
        ),
        'const_section': re.compile(
            r'^\s*const\s*$',
            re.IGNORECASE | re.MULTILINE
        ),
        'uses': re.compile(
            r'^\s*uses\s+([^;]+);',
            re.IGNORECASE | re.MULTILINE | re.DOTALL
        ),
        'end_block': re.compile(
            r'^\s*end\s*[;.]',
            re.IGNORECASE | re.MULTILINE
        ),
    }

    # Patterns for Modula-2
    MODULA2_PATTERNS = {
        'module': re.compile(
            r'^\s*(DEFINITION|IMPLEMENTATION)?\s*MODULE\s+(\w+)\s*;',
            re.IGNORECASE | re.MULTILINE
        ),
        'procedure': re.compile(
            r'^\s*PROCEDURE\s+(\w+)\s*(\([^)]*\))?\s*(:\s*\w+)?\s*;',
            re.IGNORECASE | re.MULTILINE
        ),
        'type': re.compile(
            r'^\s*TYPE\s*$',
            re.IGNORECASE | re.MULTILINE
        ),
        'var': re.compile(
            r'^\s*VAR\s*$',
            re.IGNORECASE | re.MULTILINE
        ),
        'const': re.compile(
            r'^\s*CONST\s*$',
            re.IGNORECASE | re.MULTILINE
        ),
        'import': re.compile(
            r'^\s*(FROM\s+\w+\s+)?IMPORT\s+([^;]+);',
            re.IGNORECASE | re.MULTILINE
        ),
        'export': re.compile(
            r'^\s*EXPORT\s+([^;]+);',
            re.IGNORECASE | re.MULTILINE
        ),
        'end_module': re.compile(
            r'^\s*END\s+\w+\s*[;.]',
            re.IGNORECASE | re.MULTILINE
        ),
    }

    # Patterns for Assembly
    ASM_PATTERNS = {
        'label': re.compile(
            r'^(\w+)\s*:',
            re.MULTILINE
        ),
        'proc': re.compile(
            r'^\s*(\w+)\s+(PROC|proc)\s*(FAR|NEAR|far|near)?\s*$',
            re.MULTILINE
        ),
        'endp': re.compile(
            r'^\s*(\w+)\s+(ENDP|endp)\s*$',
            re.MULTILINE
        ),
        'macro': re.compile(
            r'^\s*(\w+)\s+(MACRO|macro)\s*',
            re.MULTILINE
        ),
        'endm': re.compile(
            r'^\s*(ENDM|endm)\s*$',
            re.MULTILINE
        ),
        'segment': re.compile(
            r'^\s*(\w+)\s+(SEGMENT|segment)\s*',
            re.MULTILINE
        ),
        'ends': re.compile(
            r'^\s*(\w+)\s+(ENDS|ends)\s*$',
            re.MULTILINE
        ),
        'comment_block': re.compile(
            r'^\s*;[-=]+\s*$',  # Comment separator lines
            re.MULTILINE
        ),
    }

    # Patterns for Python
    PYTHON_PATTERNS = {
        'class': re.compile(
            r'^(\s*)class\s+(\w+)\s*(?:\([^)]*\))?\s*:',
            re.MULTILINE
        ),
        'function': re.compile(
            r'^(\s*)(?:async\s+)?def\s+(\w+)\s*\([^)]*\)\s*(?:->\s*[^:]+)?\s*:',
            re.MULTILINE
        ),
        'method': re.compile(
            r'^(\s+)(?:async\s+)?def\s+(\w+)\s*\([^)]*\)\s*(?:->\s*[^:]+)?\s*:',
            re.MULTILINE
        ),
    }

    # Patterns for curly-brace languages (JS/TS/C#/Java/Go/Rust/C/C++/PHP/Swift/Kotlin/Scala)
    CURLY_BRACE_PATTERNS = {
        # JavaScript/TypeScript
        'js_function': re.compile(
            r'^(\s*)(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*(?:<[^>]*>)?\s*\([^)]*\)',
            re.MULTILINE
        ),
        'js_arrow': re.compile(
            r'^(\s*)(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?(?:\([^)]*\)|[^=]+)\s*=>',
            re.MULTILINE
        ),
        'js_class': re.compile(
            r'^(\s*)(?:export\s+)?(?:abstract\s+)?class\s+(\w+)(?:\s+extends\s+\w+)?(?:\s+implements\s+[^{]+)?\s*\{',
            re.MULTILINE
        ),
        'js_method': re.compile(
            r'^(\s+)(?:static\s+)?(?:async\s+)?(?:get\s+|set\s+)?(\w+)\s*\([^)]*\)\s*(?::\s*[^{]+)?\s*\{',
            re.MULTILINE
        ),
        # TypeScript interface/type
        'ts_interface': re.compile(
            r'^(\s*)(?:export\s+)?interface\s+(\w+)(?:<[^>]*>)?(?:\s+extends\s+[^{]+)?\s*\{',
            re.MULTILINE
        ),
        'ts_type': re.compile(
            r'^(\s*)(?:export\s+)?type\s+(\w+)(?:<[^>]*>)?\s*=',
            re.MULTILINE
        ),
        # C#/Java
        'csharp_class': re.compile(
            r'^(\s*)(?:public|private|protected|internal|static|abstract|sealed|partial|\s)*class\s+(\w+)(?:<[^>]*>)?(?:\s*:\s*[^{]+)?\s*\{',
            re.MULTILINE
        ),
        'csharp_interface': re.compile(
            r'^(\s*)(?:public|private|protected|internal|\s)*interface\s+(\w+)(?:<[^>]*>)?(?:\s*:\s*[^{]+)?\s*\{',
            re.MULTILINE
        ),
        'csharp_method': re.compile(
            r'^(\s*)(?:public|private|protected|internal|static|virtual|override|abstract|async|\s)*(?:\w+(?:<[^>]*>)?(?:\[\])?)\s+(\w+)\s*(?:<[^>]*>)?\s*\([^)]*\)\s*(?:where\s+[^{]+)?\s*\{',
            re.MULTILINE
        ),
        'csharp_property': re.compile(
            r'^(\s*)(?:public|private|protected|internal|static|virtual|override|abstract|\s)*(?:\w+(?:<[^>]*>)?(?:\[\])?)\s+(\w+)\s*\{\s*(?:get|set)',
            re.MULTILINE
        ),
        # Go
        'go_func': re.compile(
            r'^func\s+(?:\([^)]+\)\s+)?(\w+)\s*(?:\[[^\]]*\])?\s*\([^)]*\)',
            re.MULTILINE
        ),
        'go_type': re.compile(
            r'^type\s+(\w+)\s+(?:struct|interface)\s*\{',
            re.MULTILINE
        ),
        # Rust
        'rust_fn': re.compile(
            r'^(\s*)(?:pub\s+)?(?:async\s+)?fn\s+(\w+)(?:<[^>]*>)?\s*\([^)]*\)',
            re.MULTILINE
        ),
        'rust_struct': re.compile(
            r'^(\s*)(?:pub\s+)?struct\s+(\w+)(?:<[^>]*>)?',
            re.MULTILINE
        ),
        'rust_impl': re.compile(
            r'^(\s*)impl(?:<[^>]*>)?\s+(?:\w+\s+for\s+)?(\w+)(?:<[^>]*>)?\s*\{',
            re.MULTILINE
        ),
        'rust_trait': re.compile(
            r'^(\s*)(?:pub\s+)?trait\s+(\w+)(?:<[^>]*>)?\s*\{',
            re.MULTILINE
        ),
        'rust_enum': re.compile(
            r'^(\s*)(?:pub\s+)?enum\s+(\w+)(?:<[^>]*>)?\s*\{',
            re.MULTILINE
        ),
        # PHP
        'php_function': re.compile(
            r'^(\s*)(?:public|private|protected|static|\s)*function\s+(\w+)\s*\([^)]*\)',
            re.MULTILINE
        ),
        'php_class': re.compile(
            r'^(\s*)(?:abstract|final|\s)*class\s+(\w+)(?:\s+extends\s+\w+)?(?:\s+implements\s+[^{]+)?\s*\{',
            re.MULTILINE
        ),
        # Ruby
        'ruby_class': re.compile(
            r'^(\s*)class\s+(\w+)(?:\s*<\s*\w+)?',
            re.MULTILINE
        ),
        'ruby_module': re.compile(
            r'^(\s*)module\s+(\w+)',
            re.MULTILINE
        ),
        'ruby_def': re.compile(
            r'^(\s*)def\s+(?:self\.)?(\w+[?!=]?)',
            re.MULTILINE
        ),
        # Swift
        'swift_func': re.compile(
            r'^(\s*)(?:public|private|internal|open|fileprivate|static|class|\s)*func\s+(\w+)(?:<[^>]*>)?\s*\([^)]*\)',
            re.MULTILINE
        ),
        'swift_class': re.compile(
            r'^(\s*)(?:public|private|internal|open|fileprivate|final|\s)*class\s+(\w+)(?:<[^>]*>)?(?:\s*:\s*[^{]+)?\s*\{',
            re.MULTILINE
        ),
        'swift_struct': re.compile(
            r'^(\s*)(?:public|private|internal|fileprivate|\s)*struct\s+(\w+)(?:<[^>]*>)?(?:\s*:\s*[^{]+)?\s*\{',
            re.MULTILINE
        ),
        # Kotlin
        'kotlin_fun': re.compile(
            r'^(\s*)(?:public|private|protected|internal|inline|suspend|\s)*fun\s+(?:<[^>]*>\s*)?(\w+)\s*\([^)]*\)',
            re.MULTILINE
        ),
        'kotlin_class': re.compile(
            r'^(\s*)(?:public|private|protected|internal|open|abstract|sealed|data|enum|\s)*class\s+(\w+)(?:<[^>]*>)?',
            re.MULTILINE
        ),
        # Scala
        'scala_def': re.compile(
            r'^(\s*)(?:override\s+)?def\s+(\w+)(?:\[[^\]]*\])?\s*(?:\([^)]*\))*',
            re.MULTILINE
        ),
        'scala_class': re.compile(
            r'^(\s*)(?:abstract\s+|sealed\s+|final\s+)?(?:case\s+)?class\s+(\w+)(?:\[[^\]]*\])?',
            re.MULTILINE
        ),
        'scala_object': re.compile(
            r'^(\s*)(?:case\s+)?object\s+(\w+)',
            re.MULTILINE
        ),
        'scala_trait': re.compile(
            r'^(\s*)(?:sealed\s+)?trait\s+(\w+)(?:\[[^\]]*\])?',
            re.MULTILINE
        ),
    }

    def __init__(self, chunk_size: int = 1500, chunk_overlap: int = 200):
        """Initialize the code extractor.

        Args:
            chunk_size: Maximum characters per chunk (larger for code)
            chunk_overlap: Overlap between chunks for context
        """
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def detect_language(self, file_path: str) -> CodeLanguage:
        """Detect the programming language from file extension."""
        ext = Path(file_path).suffix.lower()
        return self.EXTENSION_MAP.get(ext, CodeLanguage.UNKNOWN)

    def extract_file(self, file_path: str, encoding: str = 'utf-8') -> Tuple[str, CodeLanguage, Optional[str]]:
        """Read a code file and detect its language.

        Returns:
            Tuple of (content, language, unit_name)
        """
        path = Path(file_path)

        # Try multiple encodings
        encodings = [encoding, 'utf-8', 'latin-1', 'cp1252', 'iso-8859-1']
        content = None

        for enc in encodings:
            try:
                content = path.read_text(encoding=enc)
                break
            except UnicodeDecodeError:
                continue

        if content is None:
            # Last resort: read as binary and decode with errors='replace'
            content = path.read_bytes().decode('utf-8', errors='replace')

        language = self.detect_language(file_path)
        unit_name = self._extract_unit_name(content, language)

        return content, language, unit_name

    def _extract_unit_name(self, content: str, language: CodeLanguage) -> Optional[str]:
        """Extract the main unit/module name from code."""
        if language in (CodeLanguage.PASCAL, CodeLanguage.DELPHI):
            # Try unit first
            match = self.DELPHI_PATTERNS['unit'].search(content)
            if match:
                return match.group(1)
            # Try program
            match = self.DELPHI_PATTERNS['program'].search(content)
            if match:
                return match.group(1)
            # Try library
            match = self.DELPHI_PATTERNS['library'].search(content)
            if match:
                return match.group(1)

        elif language == CodeLanguage.MODULA2:
            match = self.MODULA2_PATTERNS['module'].search(content)
            if match:
                return match.group(2)

        elif language == CodeLanguage.ASSEMBLY:
            # Use filename as unit name for assembly
            return None

        return None

    def extract_symbols(self, content: str, language: CodeLanguage) -> List[CodeSymbol]:
        """Extract code symbols (procedures, functions, classes, etc.) from content."""
        if language in (CodeLanguage.PASCAL, CodeLanguage.DELPHI):
            return self._extract_delphi_symbols(content)
        elif language == CodeLanguage.MODULA2:
            return self._extract_modula2_symbols(content)
        elif language == CodeLanguage.ASSEMBLY:
            return self._extract_asm_symbols(content)
        elif language == CodeLanguage.PYTHON:
            return self._extract_python_symbols(content)
        elif language in (CodeLanguage.RUBY,):
            return self._extract_ruby_symbols(content)
        elif language in (CodeLanguage.JAVASCRIPT, CodeLanguage.TYPESCRIPT,
                          CodeLanguage.CSHARP, CodeLanguage.JAVA,
                          CodeLanguage.GO, CodeLanguage.RUST,
                          CodeLanguage.C, CodeLanguage.CPP,
                          CodeLanguage.PHP, CodeLanguage.SWIFT,
                          CodeLanguage.KOTLIN, CodeLanguage.SCALA):
            return self._extract_curly_brace_symbols(content, language)
        return []

    def _extract_delphi_symbols(self, content: str) -> List[CodeSymbol]:
        """Extract symbols from Pascal/Delphi code."""
        symbols = []
        lines = content.split('\n')
        unit_name = None
        current_section = None  # interface, implementation

        # First pass: find unit name
        match = self.DELPHI_PATTERNS['unit'].search(content)
        if match:
            unit_name = match.group(1)

        i = 0
        while i < len(lines):
            line = lines[i]
            line_num = i + 1  # 1-indexed

            # Check for section markers
            if self.DELPHI_PATTERNS['interface'].match(line):
                current_section = 'interface'
                i += 1
                continue
            elif self.DELPHI_PATTERNS['implementation'].match(line):
                current_section = 'implementation'
                i += 1
                continue

            # Check for procedures
            match = self.DELPHI_PATTERNS['procedure'].match(line)
            if match:
                is_class_method = bool(match.group(1))
                symbol_type = match.group(2).lower()
                name = match.group(3)
                params = match.group(4) or ''

                # Find the end of the procedure
                end_line = self._find_procedure_end(lines, i, language=CodeLanguage.DELPHI)
                proc_text = '\n'.join(lines[i:end_line + 1])

                symbols.append(CodeSymbol(
                    name=name,
                    symbol_type=f"class_{symbol_type}" if is_class_method else symbol_type,
                    line_start=line_num,
                    line_end=end_line + 1,
                    text=proc_text,
                    unit_name=unit_name,
                    parameters=params.strip('()'),
                    visibility='public' if current_section == 'interface' else 'private',
                ))
                i = end_line + 1
                continue

            # Check for functions
            match = self.DELPHI_PATTERNS['function'].match(line)
            if match:
                is_class_method = bool(match.group(1))
                name = match.group(2)
                params = match.group(3) or ''
                return_type = match.group(4)

                end_line = self._find_procedure_end(lines, i, language=CodeLanguage.DELPHI)
                func_text = '\n'.join(lines[i:end_line + 1])

                symbols.append(CodeSymbol(
                    name=name,
                    symbol_type="class_function" if is_class_method else "function",
                    line_start=line_num,
                    line_end=end_line + 1,
                    text=func_text,
                    unit_name=unit_name,
                    parameters=params.strip('()'),
                    return_type=return_type,
                    visibility='public' if current_section == 'interface' else 'private',
                ))
                i = end_line + 1
                continue

            # Check for class declarations
            match = self.DELPHI_PATTERNS['class'].match(line)
            if match:
                name = match.group(1)
                parent = match.group(2)

                end_line = self._find_block_end(lines, i)
                class_text = '\n'.join(lines[i:end_line + 1])

                symbols.append(CodeSymbol(
                    name=name,
                    symbol_type="class",
                    line_start=line_num,
                    line_end=end_line + 1,
                    text=class_text,
                    unit_name=unit_name,
                    parent_symbol=parent.strip('()') if parent else None,
                ))
                i = end_line + 1
                continue

            # Check for record declarations
            match = self.DELPHI_PATTERNS['record'].match(line)
            if match:
                name = match.group(1)

                end_line = self._find_block_end(lines, i)
                record_text = '\n'.join(lines[i:end_line + 1])

                symbols.append(CodeSymbol(
                    name=name,
                    symbol_type="record",
                    line_start=line_num,
                    line_end=end_line + 1,
                    text=record_text,
                    unit_name=unit_name,
                ))
                i = end_line + 1
                continue

            i += 1

        return symbols

    def _extract_modula2_symbols(self, content: str) -> List[CodeSymbol]:
        """Extract symbols from Modula-2 code."""
        symbols = []
        lines = content.split('\n')
        module_name = None
        module_type = None  # DEFINITION or IMPLEMENTATION

        # Find module name
        match = self.MODULA2_PATTERNS['module'].search(content)
        if match:
            module_type = match.group(1) or 'PROGRAM'
            module_name = match.group(2)

        i = 0
        while i < len(lines):
            line = lines[i]
            line_num = i + 1

            # Check for procedures
            match = self.MODULA2_PATTERNS['procedure'].match(line)
            if match:
                name = match.group(1)
                params = match.group(2) or ''
                return_type = match.group(3) or ''

                end_line = self._find_procedure_end(lines, i, language=CodeLanguage.MODULA2)
                proc_text = '\n'.join(lines[i:end_line + 1])

                symbols.append(CodeSymbol(
                    name=name,
                    symbol_type="procedure",
                    line_start=line_num,
                    line_end=end_line + 1,
                    text=proc_text,
                    unit_name=module_name,
                    parameters=params.strip('()'),
                    return_type=return_type.strip(': ') if return_type else None,
                ))
                i = end_line + 1
                continue

            i += 1

        return symbols

    def _extract_asm_symbols(self, content: str) -> List[CodeSymbol]:
        """Extract symbols from Assembly code."""
        symbols = []
        lines = content.split('\n')
        current_segment = None

        i = 0
        while i < len(lines):
            line = lines[i]
            line_num = i + 1

            # Check for segment
            match = self.ASM_PATTERNS['segment'].match(line)
            if match:
                current_segment = match.group(1)
                i += 1
                continue

            # Check for PROC
            match = self.ASM_PATTERNS['proc'].match(line)
            if match:
                name = match.group(1)
                proc_type = match.group(3) or 'NEAR'

                # Find ENDP
                end_line = i
                for j in range(i + 1, len(lines)):
                    endp_match = self.ASM_PATTERNS['endp'].match(lines[j])
                    if endp_match and endp_match.group(1).upper() == name.upper():
                        end_line = j
                        break

                proc_text = '\n'.join(lines[i:end_line + 1])

                symbols.append(CodeSymbol(
                    name=name,
                    symbol_type="procedure",
                    line_start=line_num,
                    line_end=end_line + 1,
                    text=proc_text,
                    metadata={'segment': current_segment, 'type': proc_type},
                ))
                i = end_line + 1
                continue

            # Check for MACRO
            match = self.ASM_PATTERNS['macro'].match(line)
            if match:
                name = match.group(1)

                # Find ENDM
                end_line = i
                for j in range(i + 1, len(lines)):
                    if self.ASM_PATTERNS['endm'].match(lines[j]):
                        end_line = j
                        break

                macro_text = '\n'.join(lines[i:end_line + 1])

                symbols.append(CodeSymbol(
                    name=name,
                    symbol_type="macro",
                    line_start=line_num,
                    line_end=end_line + 1,
                    text=macro_text,
                    metadata={'segment': current_segment},
                ))
                i = end_line + 1
                continue

            # Check for standalone labels (potential entry points)
            match = self.ASM_PATTERNS['label'].match(line)
            if match:
                name = match.group(1)
                # Skip common assembler directives
                if name.upper() not in ('DB', 'DW', 'DD', 'DQ', 'ASSUME', 'ORG', 'OFFSET'):
                    # Find the extent of this label's code block
                    end_line = self._find_asm_label_end(lines, i)
                    label_text = '\n'.join(lines[i:end_line + 1])

                    # Only add if it has meaningful content
                    if len(label_text.strip()) > len(line):
                        symbols.append(CodeSymbol(
                            name=name,
                            symbol_type="label",
                            line_start=line_num,
                            line_end=end_line + 1,
                            text=label_text,
                            metadata={'segment': current_segment},
                        ))

            i += 1

        return symbols

    def _find_procedure_end(self, lines: List[str], start: int, language: CodeLanguage) -> int:
        """Find the end line of a procedure/function."""
        depth = 0
        in_body = False

        for i in range(start, len(lines)):
            line = lines[i].strip().lower()

            # Skip empty lines and comments
            if not line or line.startswith('//') or line.startswith('{') or line.startswith('(*'):
                continue

            # Track begin/end pairs
            if 'begin' in line:
                depth += 1
                in_body = True

            # Check for end
            if language in (CodeLanguage.PASCAL, CodeLanguage.DELPHI):
                if line.startswith('end') and (line == 'end;' or line == 'end' or line.startswith('end;')):
                    if depth <= 1:
                        return i
                    depth -= 1
            elif language == CodeLanguage.MODULA2:
                if line.startswith('end') and ';' in line:
                    if depth <= 1:
                        return i
                    depth -= 1

        # If no end found, return a reasonable block
        return min(start + 50, len(lines) - 1)

    def _find_block_end(self, lines: List[str], start: int) -> int:
        """Find the end of a class/record block."""
        for i in range(start + 1, len(lines)):
            line = lines[i].strip().lower()
            if line.startswith('end;') or line == 'end;':
                return i
        return min(start + 30, len(lines) - 1)

    def _find_asm_label_end(self, lines: List[str], start: int) -> int:
        """Find the end of an assembly label's code block."""
        # Look for next label, PROC, or significant separator
        for i in range(start + 1, min(start + 100, len(lines))):
            line = lines[i]

            # Check for next label
            if self.ASM_PATTERNS['label'].match(line):
                return i - 1

            # Check for next PROC
            if self.ASM_PATTERNS['proc'].match(line):
                return i - 1

            # Check for RET (likely end of subroutine)
            if re.match(r'^\s*(RET|RETN|RETF|ret|retn|retf)\s*', line):
                return i

        return min(start + 30, len(lines) - 1)

    def _extract_python_symbols(self, content: str) -> List[CodeSymbol]:
        """Extract symbols from Python code."""
        symbols = []
        lines = content.split('\n')

        i = 0
        while i < len(lines):
            line = lines[i]
            line_num = i + 1

            # Check for class
            match = self.PYTHON_PATTERNS['class'].match(line)
            if match:
                indent = len(match.group(1))
                name = match.group(2)

                end_line = self._find_python_block_end(lines, i, indent)
                class_text = '\n'.join(lines[i:end_line + 1])

                symbols.append(CodeSymbol(
                    name=name,
                    symbol_type="class",
                    line_start=line_num,
                    line_end=end_line + 1,
                    text=class_text,
                ))
                i = end_line + 1
                continue

            # Check for function/method
            match = self.PYTHON_PATTERNS['function'].match(line)
            if match:
                indent = len(match.group(1))
                name = match.group(2)
                is_method = indent > 0

                end_line = self._find_python_block_end(lines, i, indent)
                func_text = '\n'.join(lines[i:end_line + 1])

                symbols.append(CodeSymbol(
                    name=name,
                    symbol_type="method" if is_method else "function",
                    line_start=line_num,
                    line_end=end_line + 1,
                    text=func_text,
                ))
                i = end_line + 1
                continue

            i += 1

        return symbols

    def _find_python_block_end(self, lines: List[str], start: int, base_indent: int) -> int:
        """Find the end of a Python indented block."""
        for i in range(start + 1, len(lines)):
            line = lines[i]

            # Skip empty lines and comments
            if not line.strip() or line.strip().startswith('#'):
                continue

            # Count leading whitespace
            stripped = line.lstrip()
            current_indent = len(line) - len(stripped)

            # If we hit a line with same or less indent, block ended on previous line
            if current_indent <= base_indent:
                return i - 1

        return len(lines) - 1

    def _extract_ruby_symbols(self, content: str) -> List[CodeSymbol]:
        """Extract symbols from Ruby code."""
        symbols = []
        lines = content.split('\n')

        i = 0
        while i < len(lines):
            line = lines[i]
            line_num = i + 1

            # Check for class
            match = self.CURLY_BRACE_PATTERNS['ruby_class'].match(line)
            if match:
                indent = len(match.group(1))
                name = match.group(2)

                end_line = self._find_ruby_block_end(lines, i, indent)
                class_text = '\n'.join(lines[i:end_line + 1])

                symbols.append(CodeSymbol(
                    name=name,
                    symbol_type="class",
                    line_start=line_num,
                    line_end=end_line + 1,
                    text=class_text,
                ))
                i = end_line + 1
                continue

            # Check for module
            match = self.CURLY_BRACE_PATTERNS['ruby_module'].match(line)
            if match:
                indent = len(match.group(1))
                name = match.group(2)

                end_line = self._find_ruby_block_end(lines, i, indent)
                mod_text = '\n'.join(lines[i:end_line + 1])

                symbols.append(CodeSymbol(
                    name=name,
                    symbol_type="module",
                    line_start=line_num,
                    line_end=end_line + 1,
                    text=mod_text,
                ))
                i = end_line + 1
                continue

            # Check for def
            match = self.CURLY_BRACE_PATTERNS['ruby_def'].match(line)
            if match:
                indent = len(match.group(1))
                name = match.group(2)

                end_line = self._find_ruby_block_end(lines, i, indent)
                def_text = '\n'.join(lines[i:end_line + 1])

                symbols.append(CodeSymbol(
                    name=name,
                    symbol_type="method" if indent > 0 else "function",
                    line_start=line_num,
                    line_end=end_line + 1,
                    text=def_text,
                ))
                i = end_line + 1
                continue

            i += 1

        return symbols

    def _find_ruby_block_end(self, lines: List[str], start: int, base_indent: int) -> int:
        """Find the end of a Ruby block (looking for matching 'end')."""
        depth = 1
        block_keywords = {'class', 'module', 'def', 'if', 'unless', 'case', 'while',
                          'until', 'for', 'do', 'begin'}

        for i in range(start + 1, len(lines)):
            line = lines[i].strip()

            # Skip empty lines and comments
            if not line or line.startswith('#'):
                continue

            # Check for block openers
            words = line.split()
            if words:
                first_word = words[0].rstrip(':')
                if first_word in block_keywords:
                    depth += 1
                elif first_word == 'end':
                    depth -= 1
                    if depth == 0:
                        return i

        return min(start + 50, len(lines) - 1)

    def _extract_curly_brace_symbols(self, content: str, language: CodeLanguage) -> List[CodeSymbol]:
        """Extract symbols from curly-brace languages (JS/TS/C#/Java/Go/Rust/C/C++/PHP/Swift/Kotlin/Scala)."""
        symbols = []
        lines = content.split('\n')

        # Determine which patterns to use based on language
        patterns_to_check = []

        if language in (CodeLanguage.JAVASCRIPT, CodeLanguage.TYPESCRIPT):
            patterns_to_check = [
                ('js_class', 'class'),
                ('ts_interface', 'interface'),
                ('ts_type', 'type'),
                ('js_function', 'function'),
                ('js_arrow', 'function'),
                ('js_method', 'method'),
            ]
        elif language in (CodeLanguage.CSHARP, CodeLanguage.JAVA):
            patterns_to_check = [
                ('csharp_class', 'class'),
                ('csharp_interface', 'interface'),
                ('csharp_method', 'method'),
                ('csharp_property', 'property'),
            ]
        elif language == CodeLanguage.GO:
            patterns_to_check = [
                ('go_type', 'type'),
                ('go_func', 'function'),
            ]
        elif language == CodeLanguage.RUST:
            patterns_to_check = [
                ('rust_impl', 'impl'),
                ('rust_trait', 'trait'),
                ('rust_struct', 'struct'),
                ('rust_enum', 'enum'),
                ('rust_fn', 'function'),
            ]
        elif language in (CodeLanguage.C, CodeLanguage.CPP):
            # C/C++ use a simpler approach - just find functions
            patterns_to_check = [
                ('csharp_class', 'class'),  # C++ classes
                ('csharp_method', 'function'),  # Functions look similar
            ]
        elif language == CodeLanguage.PHP:
            patterns_to_check = [
                ('php_class', 'class'),
                ('php_function', 'function'),
            ]
        elif language == CodeLanguage.SWIFT:
            patterns_to_check = [
                ('swift_class', 'class'),
                ('swift_struct', 'struct'),
                ('swift_func', 'function'),
            ]
        elif language == CodeLanguage.KOTLIN:
            patterns_to_check = [
                ('kotlin_class', 'class'),
                ('kotlin_fun', 'function'),
            ]
        elif language == CodeLanguage.SCALA:
            patterns_to_check = [
                ('scala_class', 'class'),
                ('scala_object', 'object'),
                ('scala_trait', 'trait'),
                ('scala_def', 'function'),
            ]

        i = 0
        while i < len(lines):
            line = lines[i]
            line_num = i + 1
            matched = False

            for pattern_name, symbol_type in patterns_to_check:
                pattern = self.CURLY_BRACE_PATTERNS.get(pattern_name)
                if not pattern:
                    continue

                match = pattern.match(line)
                if match:
                    # Extract name - position varies by pattern
                    groups = match.groups()
                    name = None
                    for g in groups:
                        if g and not g.isspace() and len(g) > 0:
                            # Skip indentation groups (all whitespace)
                            if not g.strip():
                                continue
                            name = g.strip()
                            break

                    if not name:
                        continue

                    # Find the end of the block
                    end_line = self._find_curly_brace_end(lines, i)
                    block_text = '\n'.join(lines[i:end_line + 1])

                    symbols.append(CodeSymbol(
                        name=name,
                        symbol_type=symbol_type,
                        line_start=line_num,
                        line_end=end_line + 1,
                        text=block_text,
                    ))
                    i = end_line + 1
                    matched = True
                    break

            if not matched:
                i += 1

        return symbols

    def _find_curly_brace_end(self, lines: List[str], start: int) -> int:
        """Find the end of a curly-brace block."""
        depth = 0
        in_string = False
        string_char = None
        in_multiline_comment = False

        for i in range(start, len(lines)):
            line = lines[i]
            j = 0

            while j < len(line):
                char = line[j]

                # Handle multiline comments
                if in_multiline_comment:
                    if j < len(line) - 1 and line[j:j+2] == '*/':
                        in_multiline_comment = False
                        j += 2
                        continue
                    j += 1
                    continue

                # Check for multiline comment start
                if j < len(line) - 1 and line[j:j+2] == '/*':
                    in_multiline_comment = True
                    j += 2
                    continue

                # Check for single-line comment
                if j < len(line) - 1 and line[j:j+2] == '//':
                    break  # Rest of line is comment

                # Handle strings
                if in_string:
                    if char == string_char and (j == 0 or line[j-1] != '\\'):
                        in_string = False
                    j += 1
                    continue

                if char in ('"', "'", '`'):
                    in_string = True
                    string_char = char
                    j += 1
                    continue

                # Count braces
                if char == '{':
                    depth += 1
                elif char == '}':
                    depth -= 1
                    if depth == 0:
                        return i

                j += 1

        return min(start + 100, len(lines) - 1)

    def chunk_code(
        self,
        content: str,
        document_id: str,
        filename: str,
        language: CodeLanguage,
        unit_name: Optional[str] = None,
    ) -> List[CodeChunk]:
        """Chunk code intelligently, preserving symbol boundaries.

        This method extracts symbols and creates chunks that respect
        procedure/function/class boundaries as much as possible.
        """
        chunks = []
        chunk_index = 0

        # Extract all symbols
        symbols = self.extract_symbols(content, language)

        if symbols:
            # Create chunks from symbols
            for symbol in symbols:
                # If symbol is too large, split it
                if len(symbol.text) > self.chunk_size:
                    sub_chunks = self._split_large_symbol(symbol, document_id, filename, language, unit_name)
                    for sub_chunk in sub_chunks:
                        sub_chunk.chunk_index = chunk_index
                        chunks.append(sub_chunk)
                        chunk_index += 1
                else:
                    chunks.append(CodeChunk(
                        chunk_id=f"{document_id}_{chunk_index}",
                        document_id=document_id,
                        filename=filename,
                        text=symbol.text,
                        line_start=symbol.line_start,
                        line_end=symbol.line_end,
                        language=language.value,
                        unit_name=unit_name or symbol.unit_name,
                        symbol_name=symbol.name,
                        symbol_type=symbol.symbol_type,
                        parent_symbol=symbol.parent_symbol,
                        chunk_index=chunk_index,
                        metadata={
                            'parameters': symbol.parameters,
                            'return_type': symbol.return_type,
                            'visibility': symbol.visibility,
                            **symbol.metadata,
                        }
                    ))
                    chunk_index += 1

        # Also chunk any code not covered by symbols (global code, declarations, etc.)
        uncovered_chunks = self._chunk_uncovered_code(
            content, symbols, document_id, filename, language, unit_name, chunk_index
        )
        chunks.extend(uncovered_chunks)

        return chunks

    def _split_large_symbol(
        self,
        symbol: CodeSymbol,
        document_id: str,
        filename: str,
        language: CodeLanguage,
        unit_name: Optional[str],
    ) -> List[CodeChunk]:
        """Split a large symbol into smaller chunks with overlap."""
        chunks = []
        lines = symbol.text.split('\n')
        current_chunk_lines = []
        current_start_line = symbol.line_start
        current_length = 0

        for i, line in enumerate(lines):
            line_length = len(line) + 1  # +1 for newline

            if current_length + line_length > self.chunk_size and current_chunk_lines:
                # Create chunk
                chunk_text = '\n'.join(current_chunk_lines)
                chunks.append(CodeChunk(
                    chunk_id="",  # Will be set by caller
                    document_id=document_id,
                    filename=filename,
                    text=chunk_text,
                    line_start=current_start_line,
                    line_end=symbol.line_start + i - 1,
                    language=language.value,
                    unit_name=unit_name or symbol.unit_name,
                    symbol_name=symbol.name,
                    symbol_type=symbol.symbol_type,
                    parent_symbol=symbol.parent_symbol,
                    metadata={
                        'parameters': symbol.parameters,
                        'return_type': symbol.return_type,
                        'visibility': symbol.visibility,
                        'is_partial': True,
                        **symbol.metadata,
                    }
                ))

                # Start new chunk with overlap
                overlap_lines = max(1, self.chunk_overlap // 80)  # Assume ~80 chars per line
                current_chunk_lines = current_chunk_lines[-overlap_lines:] if overlap_lines < len(current_chunk_lines) else []
                current_start_line = symbol.line_start + i - len(current_chunk_lines)
                current_length = sum(len(l) + 1 for l in current_chunk_lines)

            current_chunk_lines.append(line)
            current_length += line_length

        # Add final chunk
        if current_chunk_lines:
            chunk_text = '\n'.join(current_chunk_lines)
            chunks.append(CodeChunk(
                chunk_id="",
                document_id=document_id,
                filename=filename,
                text=chunk_text,
                line_start=current_start_line,
                line_end=symbol.line_end,
                language=language.value,
                unit_name=unit_name or symbol.unit_name,
                symbol_name=symbol.name,
                symbol_type=symbol.symbol_type,
                parent_symbol=symbol.parent_symbol,
                metadata={
                    'parameters': symbol.parameters,
                    'return_type': symbol.return_type,
                    'visibility': symbol.visibility,
                    'is_partial': len(chunks) > 0,
                    **symbol.metadata,
                }
            ))

        return chunks

    def _chunk_uncovered_code(
        self,
        content: str,
        symbols: List[CodeSymbol],
        document_id: str,
        filename: str,
        language: CodeLanguage,
        unit_name: Optional[str],
        start_index: int,
    ) -> List[CodeChunk]:
        """Chunk code that isn't part of any extracted symbol."""
        chunks = []
        lines = content.split('\n')

        # Find line ranges covered by symbols
        covered_ranges = [(s.line_start - 1, s.line_end) for s in symbols]  # Convert to 0-indexed
        covered_ranges.sort()

        # Merge overlapping ranges
        merged_ranges = []
        for start, end in covered_ranges:
            if merged_ranges and start <= merged_ranges[-1][1]:
                merged_ranges[-1] = (merged_ranges[-1][0], max(end, merged_ranges[-1][1]))
            else:
                merged_ranges.append((start, end))

        # Find uncovered ranges
        uncovered_ranges = []
        prev_end = 0
        for start, end in merged_ranges:
            if prev_end < start:
                uncovered_ranges.append((prev_end, start))
            prev_end = end
        if prev_end < len(lines):
            uncovered_ranges.append((prev_end, len(lines)))

        # Create chunks from uncovered ranges
        chunk_index = start_index
        for start, end in uncovered_ranges:
            text = '\n'.join(lines[start:end]).strip()
            if len(text) < 50:  # Skip tiny fragments
                continue

            # Split if too large
            if len(text) > self.chunk_size:
                sub_chunks = self._split_text_chunk(
                    text, start + 1, document_id, filename, language, unit_name
                )
                for sub_chunk in sub_chunks:
                    sub_chunk.chunk_id = f"{document_id}_{chunk_index}"
                    sub_chunk.chunk_index = chunk_index
                    chunks.append(sub_chunk)
                    chunk_index += 1
            else:
                chunks.append(CodeChunk(
                    chunk_id=f"{document_id}_{chunk_index}",
                    document_id=document_id,
                    filename=filename,
                    text=text,
                    line_start=start + 1,
                    line_end=end,
                    language=language.value,
                    unit_name=unit_name,
                    symbol_name=None,
                    symbol_type="code_block",
                    chunk_index=chunk_index,
                ))
                chunk_index += 1

        return chunks

    def _split_text_chunk(
        self,
        text: str,
        line_start: int,
        document_id: str,
        filename: str,
        language: CodeLanguage,
        unit_name: Optional[str],
    ) -> List[CodeChunk]:
        """Split a large text block into smaller chunks."""
        chunks = []
        lines = text.split('\n')
        current_chunk_lines = []
        current_length = 0
        current_start = line_start

        for i, line in enumerate(lines):
            line_length = len(line) + 1

            if current_length + line_length > self.chunk_size and current_chunk_lines:
                chunk_text = '\n'.join(current_chunk_lines)
                chunks.append(CodeChunk(
                    chunk_id="",  # Will be set by caller
                    document_id=document_id,
                    filename=filename,
                    text=chunk_text,
                    line_start=current_start,
                    line_end=line_start + i - 1,
                    language=language.value,
                    unit_name=unit_name,
                    symbol_type="code_block",
                ))

                # Overlap
                overlap_lines = max(1, self.chunk_overlap // 80)
                current_chunk_lines = current_chunk_lines[-overlap_lines:] if overlap_lines < len(current_chunk_lines) else []
                current_start = line_start + i - len(current_chunk_lines)
                current_length = sum(len(l) + 1 for l in current_chunk_lines)

            current_chunk_lines.append(line)
            current_length += line_length

        if current_chunk_lines:
            chunk_text = '\n'.join(current_chunk_lines)
            chunks.append(CodeChunk(
                chunk_id="",
                document_id=document_id,
                filename=filename,
                text=chunk_text,
                line_start=current_start,
                line_end=line_start + len(lines) - 1,
                language=language.value,
                unit_name=unit_name,
                symbol_type="code_block",
            ))

        return chunks


# Supported file extensions for easy checking
SUPPORTED_CODE_EXTENSIONS = {
    # Python
    '.py', '.pyw', '.pyi',
    # JavaScript/TypeScript
    '.js', '.jsx', '.mjs', '.cjs', '.ts', '.tsx', '.mts', '.cts',
    # C#
    '.cs',
    # Java
    '.java',
    # Go
    '.go',
    # Rust
    '.rs',
    # C/C++
    '.c', '.h', '.cpp', '.cxx', '.cc', '.hpp', '.hxx', '.hh',
    # PHP
    '.php', '.phtml',
    # Ruby
    '.rb', '.rake', '.gemspec',
    # Swift
    '.swift',
    # Kotlin
    '.kt', '.kts',
    # Scala
    '.scala', '.sc',
    # Pascal/Delphi
    '.pas', '.dpr', '.dpk', '.pp', '.inc', '.dfm',
    # Modula-2
    '.mod', '.def', '.mi',
    # Assembly
    '.asm', '.s',
}


def is_code_file(filename: str) -> bool:
    """Check if a file is a supported code file."""
    return Path(filename).suffix.lower() in SUPPORTED_CODE_EXTENSIONS
