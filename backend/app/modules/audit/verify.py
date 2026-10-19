"""Check the audit hash chain.  Usage (from backend/):  python -m app.modules.audit.verify"""

import sys

from app.db import get_engine
from app.modules.audit.service import verify_chain

if __name__ == "__main__":
    with get_engine().connect() as conn:
        ok, count, bad_seq = verify_chain(conn)
    print(f"Audit chain {'OK' if ok else 'BROKEN at seq ' + str(bad_seq)}: {count} events checked")
    sys.exit(0 if ok else 1)
