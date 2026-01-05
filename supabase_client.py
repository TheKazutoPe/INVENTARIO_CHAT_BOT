
from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_API_KEY, SUPABASE_SERVICE_ROLE_KEY

_client: Client | None = None


def get_supabase() -> Client:
    global _client
    if _client is None:
        key = SUPABASE_SERVICE_ROLE_KEY or SUPABASE_API_KEY
        _client = create_client(SUPABASE_URL, key)
    return _client
