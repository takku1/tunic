"""The one authority on what a file is.

Every other module asks here instead of keeping its own suffix set, so
adding a type is a table row, not an edit to the scan, the parser, the
incremental refresh, and the endpoint rules.

Classification never rejects a file. A name nobody recognizes is still
`text`: the failure mode of missing knowledge is a lower coverage rung,
never a silent drop (see docs/architecture/ingestion.md). Only `admit`
turns a file away, and it does so with a reason the ledger reports.

Stdlib only; the optional language pack is consulted for the long tail
when installed, so its absence changes coverage and nothing else.
"""

from collections import namedtuple
from pathlib import PurePosixPath
import re

PYTHON = "python"      # stdlib AST engine, module table member
PROSE = "prose"        # document channel: claims, mentions, sections
NOTEBOOK = "notebook"  # .ipynb cells
CONFIG = "config"      # key-path entities
SCHEMA = "schema"      # schema entities plus references
BLOCK = "block"        # containers split before parsing
CODE = "code"          # source in some other language
TEXT = "text"          # decodable, nothing more is known

FileType = namedtuple("FileType", "family language")

PROSE_EXTENSIONS = frozenset({".md", ".txt", ".rst"})
CONFIG_EXTENSIONS = frozenset({".json", ".yaml", ".yml", ".toml", ".ini"})
SCHEMA_EXTENSIONS = frozenset({".sql", ".graphql", ".gql", ".proto"})
BLOCK_EXTENSIONS = frozenset({".vue", ".svelte", ".astro"})
NOTEBOOK_EXTENSION = ".ipynb"

# Source languages by suffix. Ids are tree-sitter grammar names, so a
# row here is all a new language needs to reach the symbols rung.
CODE_LANGUAGES = {
    ".ts": "typescript", ".tsx": "tsx", ".mts": "typescript",
    ".cts": "typescript", ".js": "javascript", ".jsx": "javascript",
    ".mjs": "javascript", ".cjs": "javascript",
    ".go": "go", ".rs": "rust", ".java": "java", ".c": "c", ".h": "c",
    ".cpp": "cpp", ".hpp": "cpp", ".cc": "cpp", ".cs": "csharp",
    ".rb": "ruby", ".php": "php", ".swift": "swift", ".kt": "kotlin",
    ".kts": "kotlin", ".scala": "scala", ".lua": "lua", ".pl": "perl",
    ".r": "r", ".sh": "bash", ".bash": "bash", ".ps1": "powershell",
    ".psm1": "powershell", ".jl": "julia", ".zig": "zig",
    ".ex": "elixir", ".exs": "elixir", ".f90": "fortran",
    ".groovy": "groovy", ".m": "objc", ".v": "verilog",
    ".pyi": "python", ".pyw": "python", ".zsh": "bash",
    ".cxx": "cpp", ".hh": "cpp", ".hxx": "cpp", ".pm": "perl",
    ".dart": "dart", ".hs": "haskell", ".ml": "ocaml", ".mli": "ocaml",
    ".clj": "clojure", ".erl": "erlang", ".nim": "nim", ".sol": "solidity",
    ".tf": "hcl", ".hcl": "hcl", ".nix": "nix", ".cmake": "cmake",
    ".html": "html", ".htm": "html", ".css": "css", ".scss": "scss",
}

# Whole basenames (lowercase). Prefix rows catch `Dockerfile.dev`.
NAMED_LANGUAGES = {"dockerfile": "dockerfile", "containerfile": "dockerfile",
                   "makefile": "make", "gnumakefile": "make",
                   "rakefile": "ruby",
                   "gemfile": "ruby", "justfile": "just"}
_NAMED_PREFIXES = ("dockerfile.", "containerfile.", "makefile.")

SHEBANG_LANGUAGES = {"sh": "bash", "bash": "bash", "zsh": "bash",
                     "dash": "bash", "python": "python", "python3": "python",
                     "node": "javascript", "ruby": "ruby", "perl": "perl",
                     "lua": "lua", "php": "php", "pwsh": "powershell"}

# Suffixes with a frozen regex-harvest receipt. Grammar-backed symbols
# supersede it only where no edge engine owns the file.
HARVEST_EXTENSIONS = frozenset({
    ".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs", ".astro",
    ".vue", ".svelte", ".go", ".rs", ".java", ".c", ".h", ".cpp", ".hpp",
    ".cc", ".cs", ".rb", ".php", ".swift", ".kt", ".kts", ".scala", ".lua",
    ".pl", ".r", ".sh", ".bash", ".ps1", ".psm1", ".jl", ".ipynb", ".zig",
    ".ex", ".exs", ".f90", ".groovy", ".m", ".v"})

# Machine output: valid text, no evidence value, can dwarf a corpus.
GENERATED_SUFFIXES = frozenset({".lock", ".map", ".log", ".sum", ".snap"})

# Snapshot bytes reach sqlite and, through retrieval, model context, so
# credentials are refused rather than merely ranked low. Applies to every
# family: being parseable is no reason to keep a secret. Templates are
# the documented shape of a secret and carry none.
SENSITIVE_NAMES = frozenset({
    ".npmrc", ".pypirc", ".netrc", ".git-credentials", ".htpasswd", ".pgpass",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "credentials",
    "credentials.json", "credentials.yaml", "credentials.yml",
    "secrets.json", "secrets.yaml", "secrets.yml", "secrets.toml",
    "service-account.json", "service_account.json"})
SENSITIVE_SUFFIXES = frozenset({".pem", ".key", ".p12", ".pfx", ".jks",
                                ".keystore", ".kdbx", ".ppk", ".gpg", ".asc"})
_ENV_FILE = re.compile(r"(?:\.env|.*\.env)(?:\..*)?\Z")
_TEMPLATE_SUFFIXES = (".example", ".sample", ".template", ".dist", ".tmpl")
_PRIVATE_KEY = re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY")

# Files nobody is known to read are bounded; known families are not.
MAX_TEXT_BYTES = 1024 * 1024
SNIFF_BYTES = 8192


def suffix_of(path):
    """Lowercased final suffix: `.R`, `.MD`, and `.Yml` are the same types."""
    return PurePosixPath(path).suffix.lower()


def is_python(path):
    return suffix_of(path) == ".py"


def is_prose(path):
    return suffix_of(path) in PROSE_EXTENSIONS


def is_sensitive(path):
    """True for names that conventionally hold credentials."""
    name = PurePosixPath(path).name.lower()
    if name.endswith(_TEMPLATE_SUFFIXES):
        return False
    return (name in SENSITIVE_NAMES or suffix_of(path) in SENSITIVE_SUFFIXES
            or _ENV_FILE.match(name) is not None)


def _pack_language(path):
    """Long-tail language id from the optional pack, else None."""
    try:
        import tree_sitter_language_pack as pack
        return pack.detect_language_from_path(path)
    except (ImportError, AttributeError, ValueError):
        return None


def _shebang_language(head):
    if not head.startswith(b"#!"):
        return None
    words = head[2:].split(b"\n", 1)[0].decode("ascii", "ignore").split()
    if not words:
        return None
    command = words[0].rsplit("/", 1)[-1]
    if command == "env":
        rest = [word for word in words[1:] if not word.startswith("-")]
        command = rest[0] if rest else ""
    return SHEBANG_LANGUAGES.get(command.rstrip("0123456789.")
                                 or command) or SHEBANG_LANGUAGES.get(command)


def classify(path, head=b""):
    """FileType for a path; head is the file start, used for shebangs."""
    suffix = suffix_of(path)
    if suffix == ".py":
        return FileType(PYTHON, "python")
    if suffix in PROSE_EXTENSIONS:
        return FileType(PROSE, None)
    if suffix == NOTEBOOK_EXTENSION:
        return FileType(NOTEBOOK, "python")
    if suffix in CONFIG_EXTENSIONS:
        return FileType(CONFIG, None)
    if suffix in SCHEMA_EXTENSIONS:
        return FileType(SCHEMA, None)
    if suffix in BLOCK_EXTENSIONS:
        return FileType(BLOCK, "typescript")
    name = PurePosixPath(path).name.lower()
    language = NAMED_LANGUAGES.get(name)
    if language is None and name.startswith(_NAMED_PREFIXES):
        language = NAMED_LANGUAGES[name.split(".", 1)[0]]
    language = language or CODE_LANGUAGES.get(suffix) \
        or _shebang_language(head) or _pack_language(path)
    return FileType(CODE, language) if language else FileType(TEXT, None)


def is_known(path):
    """True when the type alone guarantees the file is worth reading whole.

    These families have always been read unconditionally; the size and
    binary gates apply only to files admitted on content.
    """
    suffix = suffix_of(path)
    return (suffix == ".py" or suffix in PROSE_EXTENSIONS
            or suffix in CONFIG_EXTENSIONS or suffix in SCHEMA_EXTENSIONS
            or suffix in HARVEST_EXTENSIONS)


def admit(path, size, head):
    """None when the file enters the snapshot, else the ledger reason.

    Known families skip this: they have always been read in full.
    """
    if suffix_of(path) in GENERATED_SUFFIXES:
        return "generated"
    if size == 0:
        return "empty"
    if size > MAX_TEXT_BYTES:
        return "oversize"
    if b"\0" in head[:SNIFF_BYTES]:
        return "binary"
    if _PRIVATE_KEY.search(head):
        return "sensitive"
    return None


def decodes(data):
    """Text the document channel can cite; other encodings are ledgered."""
    try:
        data.decode("utf-8")
    except UnicodeError:
        return False
    return True
