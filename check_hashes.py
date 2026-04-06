"""
check_hashes.py
Diagnóstico: Muestra el formato del password_hash de cada usuario en la BD.
No revela la contraseña completa, solo el prefijo/tipo de hash.
"""
from supabase import create_client, Client
from config import Config

supabase: Client = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)

print("=" * 65)
print("  DIAGNÓSTICO DE HASHES DE CONTRASEÑA EN `users`")
print("=" * 65)

res = supabase.table('users').select('id, email, role, is_active, password_hash, failed_attempts').execute()

if not res.data:
    print("⚠️  No se encontraron usuarios en la tabla.")
else:
    for u in res.data:
        ph = u.get('password_hash') or ''
        email = u.get('email', 'N/A')
        role = u.get('role', 'N/A')
        is_active = u.get('is_active', True)
        intentos = u.get('failed_attempts', 0)

        # Determinar tipo de hash
        if not ph:
            tipo = '❌ VACÍO / NULL'
        elif ph.startswith('pbkdf2:sha256:') or ph.startswith('pbkdf2:sha1:'):
            tipo = '✅ Werkzeug (pbkdf2)  ← compatible con check_password_hash'
        elif ph.startswith('scrypt:'):
            tipo = '✅ Werkzeug (scrypt)   ← compatible con check_password_hash'
        elif ph.startswith('$2b$') or ph.startswith('$2a$'):
            tipo = '🔴 bcrypt              ← NO compatible (requiere bcrypt.checkpw)'
        elif ph.startswith('$argon2'):
            tipo = '🔴 argon2              ← NO compatible'
        elif len(ph) == 32:
            tipo = '🔴 MD5 (texto plano)   ← NO compatible'
        elif len(ph) == 40:
            tipo = '🔴 SHA-1               ← NO compatible'
        elif len(ph) == 64:
            tipo = '🔴 SHA-256             ← NO compatible'
        elif ' ' not in ph and len(ph) < 20:
            tipo = '🔴 TEXTO PLANO         ← NO compatible (nunca hashear así)'
        else:
            tipo = f'❓ Desconocido         ← prefijo: "{ph[:30]}..."'

        estado_cuenta = '🔒 BLOQUEADO' if intentos >= 5 else ('⚠️ Suspendido' if not is_active else '✅ Activo')

        print(f"\n  📧 {email}")
        print(f"     Rol         : {role}")
        print(f"     Cuenta      : {estado_cuenta}  (intentos fallidos: {intentos})")
        print(f"     Hash tipo   : {tipo}")
        print(f"     Hash (50c)  : {ph[:50]}...")

print("\n" + "=" * 65)
print("  RESUMEN:")
print("  - Solo hashes Werkzeug (pbkdf2/scrypt) funcionan con check_password_hash.")
print("  - Los demás tipos necesitan re-hashear o lógica dual de verificación.")
print("  - Cuentas con failed_attempts >= 5 están BLOQUEADAS por el sistema.")
print("=" * 65)
