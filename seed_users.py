import os
from supabase import create_client, Client
from config import Config
from werkzeug.security import generate_password_hash

url = Config.SUPABASE_URL
key = Config.SUPABASE_KEY
supabase: Client = create_client(url, key)

users = [
    { 'email': 'admin@demo.com', 'password_hash': generate_password_hash('admin123'), 'role': 'admin' },
    { 'email': 'user@demo.com', 'password_hash': generate_password_hash('user123'), 'role': 'user' },
    { 'email': 'helpdesk@demo.com', 'password_hash': generate_password_hash('helpdesk123'), 'role': 'helpdesk' },
    { 'email': 'useram@demo.com', 'password_hash': generate_password_hash('useram123'), 'role': 'user_am' }
]

print("Intentando inyectar usuarios semilla...")
for u in users:
    try:
        supabase.table('users').insert(u).execute()
        print(f"✅ Registrado {u['email']} exitosamente.")
    except Exception as e:
        print(f"⚠️ Error con {u['email']}: Posiblemente la tabla no existe o ya estaba creado. {e}")
