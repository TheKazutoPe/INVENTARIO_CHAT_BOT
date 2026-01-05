
import os
from dotenv import load_dotenv

# Carga variables desde .env
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_API_KEY = os.getenv("SUPABASE_API_KEY") or os.getenv("SUPABASE_ANON_KEY")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not (SUPABASE_SERVICE_ROLE_KEY or SUPABASE_API_KEY):
    raise RuntimeError(
        "Falta SUPABASE_URL y/o SUPABASE_SERVICE_ROLE_KEY (o SUPABASE_API_KEY) en variables de entorno."
    )

# Tablas
BITACORAS_TABLE = os.getenv("BITACORAS_TABLE", "bitacoras")
ACUMULADO_TABLE = os.getenv("ACUMULADO_TABLE", "materiales_acumulado")

CATALOGO_CLARO_TABLE = os.getenv("CATALOGO_CLARO_TABLE", "catalogo_claro_resumido")
CATALOGO_CICSA_TABLE = os.getenv("CATALOGO_CICSA_TABLE", "catalogo_cicsa_materiales")

# Columnas de catálogo
CLARO_CODE_COL = os.getenv("CLARO_CODE_COL", "cod_sap")
CLARO_DESC_COL = os.getenv("CLARO_DESC_COL", "descripcion")
CLARO_SIMPLE_COL = os.getenv("CLARO_SIMPLE_COL", "nombre_simple")

CICSA_CODE_COL = os.getenv("CICSA_CODE_COL", "cod_ax")
CICSA_DESC_COL = os.getenv("CICSA_DESC_COL", "descripcion")
CICSA_SIMPLE_COL = os.getenv("CICSA_SIMPLE_COL", "nombre_simple")

CATALOGO_ACTIVO_COL = os.getenv("CATALOGO_ACTIVO_COL", "activo")
