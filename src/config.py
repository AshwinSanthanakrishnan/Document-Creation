"""Central configuration: paths, firm details, and the fixed safety banner."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_DIR = PROJECT_ROOT / "outputs"
LOG_DIR = PROJECT_ROOT / "logs"
CLIENTS_FILE = DATA_DIR / "clients.json"
REQUIREMENTS_FILE = DATA_DIR / "case_requirements.json"

# Printed at the top of every page of every generated document. Not configurable on purpose.
SAFETY_HEADER = "DRAFT — FOR ATTORNEY REVIEW. NOT LEGAL ADVICE."

FIRM_NAME = "Sample Immigration Firm"
FIRM_ADDRESS = "100 Example Plaza, Suite 400  ·  Austin, TX 78701"
FIRM_PHONE = "(512) 555-0100"
FIRM_CONTACT = f"{FIRM_PHONE}  ·  intake@sample-immigration.example"

DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"
GEMINI_TIMEOUT_SECONDS = 30
GEMINI_RETRY_DELAY_SECONDS = 3.0

# Clients are asked to return missing documents within this many days of the letter date.
DOCUMENT_REQUEST_DUE_DAYS = 14
