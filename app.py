import os
import datetime
import io
import urllib.parse
from functools import wraps
import pandas as pd
from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for, session
from werkzeug.security import check_password_hash
from argon2 import PasswordHasher as Argon2Hasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError

# Hasher compartido (thread-safe)
_argon2 = Argon2Hasher()

def verify_password(stored_hash: str, plain_password: str) -> bool:
    """Verifica contraseña soportando argon2 (Supabase/Postgres) y werkzeug pbkdf2.
    Argon2 es el formato nativo de los usuarios existentes en Supabase.
    Werkzeug es fallback para cuentas creadas localmente con seed_users.py.
    """
    if not stored_hash:
        return False
    if stored_hash.startswith('$argon2'):
        try:
            _argon2.verify(stored_hash, plain_password)
            return True
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False
    # Fallback: hash werkzeug (pbkdf2:sha256 / scrypt)
    return check_password_hash(stored_hash, plain_password)
from supabase import create_client, Client
from config import Config

app = Flask(__name__)
app.config.from_object(Config)
app.secret_key = 'inventario_premium_2026_super_key'

# --- CONEXIÓN SUPABASE ---
try:
    supabase: Client = create_client(app.config['SUPABASE_URL'], app.config['SUPABASE_KEY'])
    print("✅ Conexión a Supabase establecida.")
except Exception as e:
    print(f"❌ Error crítico conectando a Supabase: {e}")

# =====================================================================
#  ROLES DEL SISTEMA (mapeados a la tabla users)
# =====================================================================
# admin       → Supervisor CICSA   → Acceso TOTAL (incluye exportar)
# user        → Contrata           → Reportes + Monitor + Stock (NO exportar)
# helpdesk    → Soporte interno    → BLOQUEADO en este módulo
# user_am     → Cliente Claro      → BLOQUEADO en este módulo

ROLES_PERMITIDOS = ('admin', 'user', 'contrata')   # Únicos roles que pueden entrar al sistema
ROLES_ADMIN      = ('admin',)           # Acceso exclusivo: exportación masiva de Excel
ROLES_ALL        = ('admin', 'user', 'contrata')   # Acceso completo sin exportar

def login_required(f):
    """Verifica que el usuario esté autenticado."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_email' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    """Sólo para supervisores (admin). Redirige con error si es user/contrata."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_email' not in session:
            return redirect(url_for('login'))
        if session.get('role') not in ROLES_ADMIN:
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'error': 'Acceso denegado. Solo supervisores.'}), 403
            return redirect(url_for('acceso_denegado'))
        return f(*args, **kwargs)
    return decorated_function

def role_required(*roles):
    """Generico: restringe a los roles especificados."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_email' not in session:
                return redirect(url_for('login'))
            if session.get('role') not in roles:
                if request.is_json or request.path.startswith('/api/'):
                    return jsonify({'error': 'Acceso denegado.'}), 403
                return redirect(url_for('acceso_denegado'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# =====================================================================
#  FUNCIONES AUXILIARES
# =====================================================================
def get_identifier(b_data):
    """
    Determina qué mostrar como identificador principal siguiendo la prioridad:
    1. INCIDENCIA  2. TAS  3. SOT  4. CÓDIGO BD (Correlativo)
    """
    if not b_data: return "S/N"
    inc = str(b_data.get('nroincidencia_bd') or '').strip()
    tas = str(b_data.get('nrotas_bd') or '').strip()
    sot = str(b_data.get('nrosot_bd') or '').strip()
    correlativo = str(b_data.get('codigo_bd') or '').strip()
    anio = str(b_data.get('anio_bd') or '').strip()
    id_unico = str(b_data.get('id') or '').strip()

    invalidos = ['NONE', 'NULL', 'NAN', 'NO TIENE', '', '0']

    if inc and inc.upper() not in invalidos: return inc
    if tas and tas.upper() not in invalidos: return tas
    if sot and sot.upper() not in invalidos: return sot
    if correlativo and anio and anio not in invalidos:
        return f"{correlativo}-{anio}"
    if correlativo and correlativo not in invalidos:
        return correlativo
    return id_unico

def get_brigada_zone_map():
    """Devuelve un mapa {brigada_main: {zona, contrata}} desde brigada_tabla."""
    try:
        res = supabase.table('brigada_tabla').select('brigada_main, "ZONA", contrata_bd').execute()
        result = {}
        for r in (res.data or []):
            bm = r.get('brigada_main', '')
            if bm:
                result[bm] = {
                    'zona':     r.get('ZONA', 'SIN ZONA'),
                    'contrata': r.get('contrata_bd', '') or ''
                }
        return result
    except:
        return {}

def zone_of(zone_map, brigada, fallback='SIN ZONA'):
    """Extrae la zona string del mapa, tolerante a dict o str."""
    val = zone_map.get(brigada, {})
    if isinstance(val, dict):
        return val.get('zona', fallback)
    return str(val) if val else fallback

def contrata_of(zone_map, brigada):
    """Extrae la contrata string del mapa."""
    val = zone_map.get(brigada, {})
    if isinstance(val, dict):
        return val.get('contrata', '')
    return ''

# =====================================================================
#  AUTENTICACIÓN
# =====================================================================
@app.route('/')
def index():
    if 'user_email' in session:
        role = session.get('role', '')
        if role in ('admin', 'user', 'contrata'):
            return redirect(url_for('reportes_view'))
    return redirect(url_for('login'))

@app.route('/acceso-denegado')
def acceso_denegado():
    return render_template('login.html',
                           error="⛔ Tu rol no tiene permisos para acceder a este módulo."), 403

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        try:
            res = supabase.table('users').select('*').eq('email', email).execute()

            if not res.data:
                return render_template('login.html', error="El usuario no existe.")

            user = res.data[0]
            role = user.get('role', '')

            # ─── Roles completamente bloqueados en este módulo ───
            if role in ('user_am', 'helpdesk'):
                return render_template('login.html',
                                       error="⛔ Este módulo no está habilitado para tu perfil.")

            # ─── Cuenta desactivada ───
            if not user.get('is_active', True):
                return render_template('login.html',
                                       error="⚠️ Cuenta suspendida. Contacta a tu administrador.")

            # ─── Verificar contraseña ───
            if verify_password(user['password_hash'], password):
                # Login exitoso: resetear intentos fallidos
                try:
                    supabase.table('users').update({
                        'failed_attempts': 0,
                        'last_login': datetime.datetime.now(datetime.timezone.utc).isoformat()
                    }).eq('id', str(user['id'])).execute()
                except:
                    pass

                session['user_email'] = user['email']
                session['role']       = role
                session['user_name']  = user.get('nombre', email.split('@')[0])
                session['user_id']    = str(user['id'])

                # Routing por rol:
                # admin  → Ve todo (reportes, monitor, stock)
                # user   → Solo reportes (sin stock ni exportación)
                return redirect(url_for('reportes_view'))

            else:
                # Incrementar intentos fallidos
                intentos = int(user.get('failed_attempts', 0)) + 1
                try:
                    supabase.table('users').update({
                        'failed_attempts': intentos
                    }).eq('id', str(user['id'])).execute()
                except:
                    pass

                if intentos >= 5:
                    return render_template('login.html',
                                           error=f"⚠️ Cuenta bloqueada por {intentos} intentos fallidos. Contacta al administrador.")
                return render_template('login.html',
                                       error=f"Credenciales incorrectas. Intento {intentos}/5.")

        except Exception as e:
            print(f"Error login: {e}")
            return render_template('login.html', error="Error en el servidor. Inténtalo de nuevo.")
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# =====================================================================
#  VISTA: TÉCNICO (REGISTRO DE MATERIALES POR BITÁCORA)
# =====================================================================
@app.route('/materiales/<bitacora_id>')
def materiales_view(bitacora_id):
    if not bitacora_id.isdigit():
        return render_template('not_found.html', error="ID inválido"), 400
    try:
        res = supabase.table('bitacoras').select("*").eq('id', int(bitacora_id)).execute()
        if not res.data:
            return render_template('not_found.html', error="Bitácora no encontrada"), 404
        bitacora = res.data[0]

        historial_res = supabase.table(Config.ACUMULADO_TABLE) \
            .select("*") \
            .eq('bitacora_id', str(bitacora_id)) \
            .order('id', desc=True) \
            .execute()
        historial = historial_res.data if historial_res.data else []

        brigadas = []
        INVALIDOS = {'', 'NONE', 'NULL', 'NAN', 'NO TIENE', '-', '—', '0', 'NINGUNO'}
        seen_vals = set()
        bd_names_to_resolve = []  # solo los _bd que no tengan _oficial

        for i in range(1, 6):
            oficial = (bitacora.get(f'bri{i}_oficial') or '').strip()
            if oficial and oficial.upper() not in INVALIDOS:
                if oficial not in seen_vals:
                    brigadas.append({'val': oficial, 'lbl': oficial})
                    seen_vals.add(oficial)
            else:
                # Sin nombre oficial → intentar con bri{i}_bd
                bd_name = (bitacora.get(f'bri{i}_bd') or '').strip()
                if bd_name and bd_name.upper() not in INVALIDOS and bd_name not in seen_vals:
                    bd_names_to_resolve.append(bd_name)
                    seen_vals.add(bd_name)

        # Resolver los _bd pendientes vía brigada_tabla
        if bd_names_to_resolve:
            try:
                map_res = supabase.table('brigada_tabla').select('name_brigada_bd, brigada_main').in_('name_brigada_bd', bd_names_to_resolve).execute()
                mapping = {x['name_brigada_bd']: x['brigada_main'] for x in map_res.data}
                for bd_name in bd_names_to_resolve:
                    short = mapping.get(bd_name, bd_name)
                    if short not in seen_vals:
                        brigadas.append({'val': short, 'lbl': short})
                        seen_vals.add(short)
                    else:
                        # El _bd resolvió al mismo nombre oficial que ya existe → skip
                        pass
            except:
                for n in bd_names_to_resolve:
                    brigadas.append({'val': n, 'lbl': n})

        # Obtener stock disponible por brigada para mostrar advertencias
        stock_brigadas = {}
        if brigadas:
            bri_names = [b['val'] for b in brigadas]
            try:
                stock_res = supabase.table('stock_brigadas').select('brigada, cod_material, nombre_material, stock_actual, stock_inicial, nombre_comercial').in_('brigada', bri_names).execute()
                for s in (stock_res.data or []):
                    key = s['brigada']
                    if key not in stock_brigadas:
                        stock_brigadas[key] = []
                    stock_brigadas[key].append(s)
            except:
                pass

        # Detectar si ya existe una marca de sin consumo
        historial_visible = [h for h in historial if h.get('cod_material') != 'SIN_CONSUMO']
        sc_record = next((h for h in historial if h.get('cod_material') == 'SIN_CONSUMO'), None)
        ya_sin_consumo = bool(sc_record)
        id_sin_consumo = sc_record['id'] if sc_record else None

        return render_template('materiales_form.html',
                               bid=bitacora_id,
                               b=bitacora,
                               brigadas=brigadas,
                               historial=historial_visible,
                               stock_brigadas=stock_brigadas,
                               ya_sin_consumo=ya_sin_consumo,
                               id_sin_consumo=id_sin_consumo)
    except Exception as e:
        print(e)
        return f"Error servidor: {e}", 500


# =====================================================================
#  VISTA: SUPERVISOR / COORDINADOR OPERATIVO (REPORTES)
# =====================================================================
@app.route('/reportes')
@login_required
def reportes_view():
    return render_template('reportes.html',
                           user_name=session.get('user_name', ''),
                           role=session.get('role', ''))


@app.route('/kpi-global')
@login_required
def kpi_global_view():
    return render_template('kpi_global.html',
                           user_name=session.get('user_name', ''),
                           role=session.get('role', ''))


@app.route('/api/acumulados-data', methods=['GET'])
@login_required
def get_acumulados_data():
    try:
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        brigada_filter = request.args.get('brigada')
        zona_filter = request.args.get('zona')
        limit = int(request.args.get('limit', 500))

        query = supabase.table(Config.ACUMULADO_TABLE).select("*").order('id', desc=True).limit(limit)

        if start_date:
            query = query.gte('fecha_guardado', start_date)
        if end_date:
            # Agregar 1 día para incluir el día completo
            try:
                end_dt = datetime.datetime.strptime(end_date, '%Y-%m-%d') + datetime.timedelta(days=1)
                query = query.lt('fecha_guardado', end_dt.strftime('%Y-%m-%d'))
            except:
                query = query.lte('fecha_guardado', end_date)

        if brigada_filter:
            query = query.eq('brigada_responsable', brigada_filter)

        res = query.execute()
        data = res.data

        if not data: return jsonify([])

        # Curar datos: recalcular INC/TAS/SOT desde bitácoras originales
        bitacora_ids = list(set([str(r['bitacora_id']) for r in data if r.get('bitacora_id')]))
        if bitacora_ids:
            b_res = supabase.table('bitacoras') \
                .select('id, nroincidencia_bd, nrotas_bd, nrosot_bd, codigo_bd, zona_bd') \
                .in_('id', bitacora_ids) \
                .execute()
            b_map = {str(b['id']): b for b in b_res.data}

            for r in data:
                bid = str(r.get('bitacora_id'))
                if bid in b_map:
                    r['inc'] = get_identifier(b_map[bid])
                    if not r.get('region'):
                        r['region'] = b_map[bid].get('zona_bd', '')

        # Enriquecer con zona de brigada_tabla
        zone_map = get_brigada_zone_map()
        for r in data:
            bri = r.get('brigada_responsable', '')
            if bri and not r.get('zona_brigada'):
                r['zona_brigada'] = zone_of(zone_map, bri, r.get('region', 'SIN ZONA'))

        # Filtro por zona (post-enriquecimiento)
        if zona_filter:
            data = [r for r in data if str(r.get('zona_brigada', '')).upper() == zona_filter.upper()]

        # Ocultar datos financieros para contratas (role='user' o 'contrata')
        CAMPOS_FINANCIEROS = ('precio_unit', 'subtotal', 'total_soles', 'tc', 'moneda')
        if session.get('role') in ('user', 'contrata'):
            for r in data:
                for campo in CAMPOS_FINANCIEROS:
                    r.pop(campo, None)

        return jsonify(data)
    except Exception as e:
        print(f"Error Reporte: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/resumen-semanal', methods=['GET'])
@login_required
def resumen_semanal():
    """Retorna consumo agrupado por semana y brigada."""
    try:
        semanas = int(request.args.get('semanas', 8))
        fecha_corte = (datetime.datetime.now() - datetime.timedelta(weeks=semanas)).isoformat()

        res = supabase.table(Config.ACUMULADO_TABLE) \
            .select("brigada_responsable, cant_material, fecha_guardado, cod_material, nombre_material, precio_unit") \
            .gte('fecha_guardado', fecha_corte) \
            .execute()

        data = res.data or []
        zone_map = get_brigada_zone_map()

        # Agrupar por semana
        resumen = {}
        for r in data:
            fecha_str = r.get('fecha_guardado', '')
            try:
                dt = datetime.datetime.fromisoformat(str(fecha_str).replace('Z', ''))
                semana_key = f"{dt.year}-S{dt.strftime('%U').zfill(2)}"
            except:
                semana_key = "SIN FECHA"

            bri  = r.get('brigada_responsable', 'SIN BRIGADA')
            zona = zone_of(zone_map, bri, 'SIN ZONA')
            cant = float(r.get('cant_material', 0))
            costo = float(r.get('precio_unit', 0))

            key = f"{semana_key}|{bri}"
            if key not in resumen:
                resumen[key] = {
                    'semana': semana_key,
                    'brigada': bri,
                    'zona': zona,
                    'total_items': 0,
                    'total_unidades': 0,
                    'costo_total': 0
                }
            resumen[key]['total_items'] += 1
            resumen[key]['total_unidades'] += cant
            resumen[key]['costo_total'] = round(resumen[key]['costo_total'] + (cant * costo), 2)

        return jsonify(sorted(list(resumen.values()), key=lambda x: x['semana'], reverse=True))
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/brigadas-lista', methods=['GET'])
@login_required
def brigadas_lista():
    """Lista de brigadas con su zona para poblar filtros."""
    try:
        res = supabase.table('brigada_tabla').select('brigada_main, "ZONA"').execute()
        brigadas = sorted(set(r['brigada_main'] for r in (res.data or []) if r.get('brigada_main')))
        zonas = sorted(set(r.get('ZONA', '') for r in (res.data or []) if r.get('ZONA')))
        return jsonify({'brigadas': brigadas, 'zonas': zonas})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/bitacoras-pendientes', methods=['GET'])
@login_required
def bitacoras_pendientes():
    """
    Retorna bitácoras con su estado de registro de materiales.
    tiene_material = True si hay consumo real registrado.
    tiene_sin_consumo = True si la brigada marcó 'sin consumo' (no usó material).
    """
    try:
        zona = request.args.get('zona', '')
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        dias = request.args.get('dias')

        query = supabase.table('bitacoras') \
            .select('id, codigo_bd, nroincidencia_bd, nrotas_bd, nrosot_bd, zona_bd, bri1_oficial, contrata_cicsa, fecha_asignacion_bd, estado_textual_bd, titulo_bd')

        if start_date:
            query = query.gte('fecha_asignacion_bd', f"{start_date}T00:00:00")
            if end_date:
                query = query.lte('fecha_asignacion_bd', f"{end_date}T23:59:59")
        else:
            d = int(dias) if dias else 7
            fecha_corte = (datetime.datetime.now() - datetime.timedelta(days=d)).isoformat()
            query = query.gte('fecha_asignacion_bd', fecha_corte)

        if zona:
            query = query.eq('zona_bd', zona)

        res = query.limit(10000).execute()
        bitacoras = res.data or []

        if bitacoras:
            bids = [str(b['id']) for b in bitacoras]
            mat_res = supabase.table(Config.ACUMULADO_TABLE) \
                .select('bitacora_id, cod_material') \
                .in_('bitacora_id', bids).execute()

            # Separar ids con consumo real vs con marca sin_consumo
            ids_con_material  = set()
            ids_sin_consumo   = set()
            for r in (mat_res.data or []):
                bid = str(r['bitacora_id'])
                if r.get('cod_material') == 'SIN_CONSUMO':
                    ids_sin_consumo.add(bid)
                else:
                    ids_con_material.add(bid)

            resultado = []
            for b in bitacoras:
                bid = str(b['id'])
                b['tiene_material']    = bid in ids_con_material
                b['tiene_sin_consumo'] = bid in ids_sin_consumo
                b['identificador']     = get_identifier(b)
                resultado.append(b)

            return jsonify(resultado)

        return jsonify([])
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/buscar-bitacora-bd', methods=['GET'])
@login_required
def buscar_bitacora_bd():
    """Busca una bitácora en toda la base de datos sin límite de fechas."""
    try:
        q = request.args.get('q', '').upper().strip()
        if len(q) < 4:
            return jsonify([])

        or_cond = f"nroincidencia_bd.ilike.%{q}%,nrotas_bd.ilike.%{q}%,nrosot_bd.ilike.%{q}%,codigo_bd.ilike.%{q}%"
        if q.isdigit():
            or_cond += f",id.eq.{int(q)}"

        query = supabase.table('bitacoras') \
            .select('id, codigo_bd, nroincidencia_bd, nrotas_bd, nrosot_bd, zona_bd, bri1_oficial, contrata_cicsa, fecha_asignacion_bd, estado_textual_bd, titulo_bd') \
            .or_(or_cond) \
            .limit(50)

        res = query.execute()
        bitacoras = res.data or []

        if bitacoras:
            bids = [str(b['id']) for b in bitacoras]
            mat_res = supabase.table(Config.ACUMULADO_TABLE) \
                .select('bitacora_id, cod_material') \
                .in_('bitacora_id', bids).execute()

            ids_con_material = set()
            ids_sin_consumo = set()
            for r in (mat_res.data or []):
                bid = str(r['bitacora_id'])
                if r.get('cod_material') == 'SIN_CONSUMO':
                    ids_sin_consumo.add(bid)
                else:
                    ids_con_material.add(bid)

            resultado = []
            for b in bitacoras:
                bid = str(b['id'])
                b['tiene_material'] = bid in ids_con_material
                b['tiene_sin_consumo'] = bid in ids_sin_consumo
                b['identificador'] = get_identifier(b)
                resultado.append(b)

            return jsonify(resultado)

        return jsonify([])
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# =====================================================================
#  EXPORTACIÓN EXCEL CON FILTROS (Solo admin/supervisor)
# =====================================================================
@app.route('/api/exportar-excel')
@admin_required
def exportar_excel():
    try:
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        brigada_filter = request.args.get('brigada')
        zona_filter = request.args.get('zona')

        query = supabase.table(Config.ACUMULADO_TABLE).select("*")

        if start_date:
            query = query.gte('fecha_guardado', start_date)
        if end_date:
            try:
                end_dt = datetime.datetime.strptime(end_date, '%Y-%m-%d') + datetime.timedelta(days=1)
                query = query.lt('fecha_guardado', end_dt.strftime('%Y-%m-%d'))
            except:
                query = query.lte('fecha_guardado', end_date)
        if brigada_filter:
            query = query.eq('brigada_responsable', brigada_filter)

        res = query.execute()
        data = res.data
        if not data: return "No hay datos para el rango seleccionado", 404

        # Curar datos
        bitacora_ids = list(set([str(r['bitacora_id']) for r in data if r.get('bitacora_id')]))
        if bitacora_ids:
            b_res = supabase.table('bitacoras') \
                .select('id, nroincidencia_bd, nrotas_bd, nrosot_bd, codigo_bd, zona_bd') \
                .in_('id', bitacora_ids).execute()
            b_map = {str(b['id']): b for b in b_res.data}
            for r in data:
                bid = str(r.get('bitacora_id'))
                if bid in b_map:
                    r['inc'] = get_identifier(b_map[bid])
                    if not r.get('region'):
                        r['region'] = b_map[bid].get('zona_bd', '')

        # Enriquecer con zona desde brigada_tabla
        zone_map = get_brigada_zone_map()
        for r in data:
            bri = r.get('brigada_responsable', '')
            r['zona_brigada'] = zone_of(zone_map, bri, r.get('region', ''))

        # Filtro zona post-enriquecimiento
        if zona_filter:
            data = [r for r in data if r.get('zona_brigada', '').upper() == zona_filter.upper()]

        df = pd.DataFrame(data)
        if df.empty: return "No hay datos para los filtros seleccionados", 404

        if 'cod_material' in df.columns:
            df['cod_material'] = df['cod_material'].astype(str).str.lstrip('0')

        # ─── COLUMNAS EXACTAS DE materiales_acumulado ───────────────────
        # Orden: igual al flujo de trabajo (datos del incidente → material → validación)
        column_map = {
            # Identificadores del incidente
            'bitacora_id':          'ID BITÁCORA',
            'inc':                  'INC / TAS / SOT',
            'sot':                  'SOT REF',
            'fecha_asign_inc':      'FECHA ASIGN INC',
            # Datos geográficos / organizativos
            'zona_brigada':         'ZONA BRIGADA',
            'region':               'REGION',
            'subregion':            'SUBREGION',
            'red_afect':            'RED AFECT',
            'base_cuadrilla':       'BASE CUADRILLA',
            'brigada_responsable':  'BRIGADA',
            # Responsables
            'sup_claro':            'SUP. CLARO',
            'sup_contrata':         'SUP. CONTRATA',
            # Site info
            'id_site_inicio':       'ID SITE INICIO',
            'name_site_inicio':     'NAME SITE INICIO',
            'id_site_fin':          'ID SITE FIN',
            'name_site_fin':        'NAME SITE FIN',
            'otdr':                 'OTDR',
            # Incidente
            'causa_averia':         'CAUSA DE AVERÍA',
            'tipo_mmto':            'TIPO DE MMTO',
            # Material
            'categoria':            'CATEGORIA',
            'subcategoria':         'SUBCATEGORIA',
            'cod_material':         'COD MATERIAL',
            'nombre_material':      'NOMBRE MATERIAL',
            'origen_material':      'ORIGEN (CLARO/CICSA)',
            # Costos
            'precio_unit':          'PRECIO UNIT.',
            'cant_material':        'CANT. MATERIAL',
            'moneda':               'MONEDA',
            'tc':                   'TC',
            'subtotal':             'SUBTOTAL USD',
            'total_soles':          'TOTAL S/',
            # Estado trabajo
            'trabajo_concluido':    'TRABAJO CONCLUIDO?',
            'porcentaje_ejecucion': '% EJECUCION',
            'comentario':           'COMENTARIO',
            # Validaciones
            'validado_oym':         'VALIDADO OYM',
            'validado_jefatura':    'VALIDADO JEFATURA',
            # Liquidación
            'mes_liq':              'MES LIQ',
            'sem_uso':              'SEM USO',
            # Fechas de control
            'fecha_guardado':       'FECHA GUARDADO',
            'created_at':           'CREATED AT',
        }

        df.rename(columns=column_map, inplace=True)
        cols_finales = [val for val in column_map.values() if val in df.columns]
        df = df[cols_finales]

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Base_Acumulada')
        output.seek(0)

        suffix = ""
        if start_date or end_date:
            suffix = f"_{start_date or 'inicio'}_{end_date or 'hoy'}"
        filename = f"Reporte_Materiales{suffix}_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
        return send_file(output,
                         mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                         as_attachment=True,
                         download_name=filename)
    except Exception as e:
        print(f"Error exportando: {e}")
        return f"Error: {str(e)}", 500


@app.route('/api/exportar-semanal')
@admin_required
def exportar_semanal():
    """Exporta resumen agrupado por semana y brigada."""
    try:
        semanas = int(request.args.get('semanas', 4))
        fecha_corte = (datetime.datetime.now() - datetime.timedelta(weeks=semanas)).isoformat()

        res = supabase.table(Config.ACUMULADO_TABLE) \
            .select("brigada_responsable, cant_material, fecha_guardado, cod_material, nombre_material, precio_unit, subtotal, total_soles") \
            .gte('fecha_guardado', fecha_corte).execute()

        data = res.data or []
        zone_map = get_brigada_zone_map()

        rows = []
        for r in data:
            fecha_str = r.get('fecha_guardado', '')
            try:
                dt = datetime.datetime.fromisoformat(str(fecha_str).replace('Z', ''))
                semana_key = f"{dt.year}-S{dt.strftime('%U').zfill(2)}"
                mes = dt.strftime('%B').upper()
            except:
                semana_key = "SIN FECHA"
                mes = ""

            bri = r.get('brigada_responsable', '')
            rows.append({
                'SEMANA': semana_key,
                'MES': mes,
                'BRIGADA': bri,
                'ZONA': zone_of(zone_map, bri, ''),
                'COD MATERIAL': str(r.get('cod_material', '')).lstrip('0'),
                'MATERIAL': r.get('nombre_material', ''),
                'CANTIDAD': float(r.get('cant_material', 0)),
                'PRECIO UNIT': float(r.get('precio_unit', 0)),
                'SUBTOTAL USD': float(r.get('subtotal', 0)),
                'TOTAL SOLES': float(r.get('total_soles', 0)),
            })

        df = pd.DataFrame(rows)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Resumen_Semanal')
            # Hoja de pivote por brigada
            if not df.empty:
                pivot = df.groupby(['SEMANA', 'BRIGADA', 'ZONA']).agg(
                    TOTAL_ITEMS=('MATERIAL', 'count'),
                    UNIDADES=('CANTIDAD', 'sum'),
                    COSTO_USD=('SUBTOTAL USD', 'sum'),
                    COSTO_SOLES=('TOTAL SOLES', 'sum')
                ).reset_index()
                pivot.to_excel(writer, index=False, sheet_name='Pivote_Brigada')
        output.seek(0)

        filename = f"Reporte_Semanal_{semanas}sem_{datetime.datetime.now().strftime('%Y%m%d')}.xlsx"
        return send_file(output,
                         mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                         as_attachment=True,
                         download_name=filename)
    except Exception as e:
        return f"Error: {str(e)}", 500


@app.route('/api/exportar-liquidacion')
@admin_required
def exportar_liquidacion():
    """
    Exporta en formato Liquidación FTTH-HFC (plantilla BBDD CENTRO).
    Join entre materiales_acumulado y bitacoras para enriquecer los datos.
    """
    try:
        start_date   = request.args.get('start_date')
        end_date     = request.args.get('end_date')
        brigada_filter = request.args.get('brigada')
        zona_filter  = request.args.get('zona')

        # ── 1. Traer datos directo desde la tabla bitacoras ──────────────────
        query = supabase.table('bitacoras') \
            .select("id, nroincidencia_bd, nrotas_bd, nrosot_bd, "
                    "red1_bd, distrito_bd, nombresite_bd, zona_bd, "
                    "plano_ftth_bd, anillo_hfc_bd, responsable_claro_bd, "
                    "materiales_bd, bitacora_bd, fechainicial_bd, bri1_oficial, bri1_bd") \
            .ilike('tipoaveria_bd', '%CORRECTIVO%')

        if start_date:
            query = query.gte('fechainicial_bd', start_date)
        if end_date:
            query = query.lte('fechainicial_bd', end_date)
        if zona_filter:
            query = query.ilike('zona_bd', f"%{zona_filter}%")

        b_res = query.execute()
        bitacoras_data = b_res.data or []

        # ── 2. Filtrar brigada en memoria si aplica ──────────────────────────
        if brigada_filter:
            bf = brigada_filter.upper()
            bitacoras_data = [
                b for b in bitacoras_data
                if bf in str(b.get('bri1_oficial') or '').upper() or bf in str(b.get('bri1_bd') or '').upper()
            ]

        if not bitacoras_data:
            return "No hay registros MANTENIMIENTO CORRECTIVO en bitácoras para los filtros aplicados.", 404

        # ── 3. Construir filas en formato plantilla ──────────────────────────
        def inc_tas(b):
            invalidos = {'', 'NONE', 'NULL', 'NAN', 'NO TIENE', '0'}
            for campo in ('nroincidencia_bd', 'nrotas_bd', 'nrosot_bd'):
                val = str(b.get(campo) or '').strip()
                if val.upper() not in invalidos:
                    return val
            return ''

        def plano(b):
            p = str(b.get('plano_ftth_bd') or '').strip()
            if p and p.upper() not in ('', 'NONE', 'NULL'):
                return p
            return str(b.get('anillo_hfc_bd') or '').strip()

        rows = []
        for b in bitacoras_data:
            # Formatear Fecha (fechainicial_bd que es date/timestamp)
            fecha_raw = b.get('fechainicial_bd')
            if fecha_raw:
                try:
                    fecha_fmt = datetime.datetime.fromisoformat(str(fecha_raw).replace('Z','')).strftime('%d/%m/%Y %H:%M')
                except:
                    # Fallback si solo es 'YYYY-MM-DD'
                    try:
                        fecha_fmt = datetime.datetime.strptime(str(fecha_raw)[:10], '%Y-%m-%d').strftime('%d/%m/%Y %H:%M')
                    except:
                        fecha_fmt = str(fecha_raw)
            else:
                fecha_fmt = ''

            # Zona directo desde bitacora (prioridad principal)
            zona_final = str(b.get('zona_bd') or '')

            rows.append({
                'FECHA':                   fecha_fmt,
                'TIPO DE RED':             (b.get('red1_bd') or '').upper(),
                'INCITAS':                 inc_tas(b),
                'SOT DE ASIGNACION':       str(b.get('nrosot_bd') or ''),
                'Distrito':                str(b.get('distrito_bd') or ''),
                'POP/SITE':                str(b.get('nombresite_bd') or ''),
                'ANILLO / PLANO':          plano(b),
                'Responsable Claro':       str(b.get('responsable_claro_bd') or ''),
                'MATERIAL UTILIZADO':      str(b.get('materiales_bd') or ''),
                'BITACORA DE LA ATENCION': str(b.get('bitacora_bd') or ''),
                'ZONA':                    zona_final,
            })

        df = pd.DataFrame(rows)

        # ── 4. Construir Excel ─────────────────────────────────
        cols_bbdd = [
            'FECHA', 'TIPO DE RED', 'INCITAS', 'SOT DE ASIGNACION',
            'Distrito', 'POP/SITE', 'ANILLO / PLANO', 'Responsable Claro',
            'MATERIAL UTILIZADO', 'BITACORA DE LA ATENCION', 'ZONA'
        ]
        df_bbdd = df[cols_bbdd]

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df_bbdd.to_excel(writer, index=False, sheet_name='LIQUIDACION MAT')

        output.seek(0)
        suffix = f"_{start_date or 'inicio'}_{end_date or 'hoy'}" if (start_date or end_date) else ''
        filename = f"Liquidacion_FTTH_HFC{suffix}_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        print(f"Error exportar-liquidacion: {e}")
        return f"Error: {str(e)}", 500


# =====================================================================
#  BÚSQUEDA Y GUARDADO (TÉCNICO)
# =====================================================================
@app.route('/api/search', methods=['GET'])
def search():
    q = request.args.get('q', '').upper()
    if len(q) < 2: return jsonify([])
    try:
        res = supabase.table('catalogo_unificado') \
            .select("*") \
            .or_(f"descripcion.ilike.%{q}%,codigo.ilike.%{q}%,cod_sap.ilike.%{q}%,cod_ax.ilike.%{q}%") \
            .limit(20).execute()

        resultados = []
        for item in res.data:
            costo_str = str(item.get('costo', '0')).replace('$', '').replace(',', '').strip()
            try:
                costo_val = float(costo_str) if costo_str else 0.0
            except:
                costo_val = 0.0

            ax = str(item.get('cod_ax', '')).strip()
            if ax and ax not in ['NO TIENE']: ax = ax.lstrip('0')

            sap = str(item.get('cod_sap', '')).strip()
            internal = str(item.get('codigo', '')).strip()

            codigo_visual = 'S/C'
            if ax and ax not in ['NO TIENE', 'nan', 'None', '']:
                codigo_visual = ax
            elif sap and sap not in ['NO TIENE', 'nan', 'None', '']:
                codigo_visual = sap
            else:
                codigo_visual = internal

            resultados.append({
                'codigo': internal,
                'codigo_visual': codigo_visual,
                'descripcion': item.get('descripcion'),
                'costo': costo_val,
                'categoria': item.get('categoria', 'GENERAL'),
                'subcategoria': item.get('unidad', ''),
                'origen': item.get('empresa', 'CLARO'),
            })
        return jsonify(resultados)
    except Exception as e:
        return jsonify([])


@app.route('/api/save-single', methods=['POST'])
def save_single():
    d = request.json
    try:
        b = supabase.table('bitacoras').select("*").eq('id', int(d['bid'])).execute().data[0]
        now = datetime.datetime.now()
        identifier = get_identifier(b)
        origen = d['item'].get('origen', 'CLARO')
        cant = float(d['cant'])
        price = float(d['item']['costo'] or 0)

        nombre_mat = (d['item'].get('descripcion') or '').upper()
        cant_ingresada = float(d['cant'])
        
        factor_conversion = 1.0
        if ("CINTA BANDIT" in nombre_mat or "FLEJE" in nombre_mat) and "HEBILLA" not in nombre_mat and "PARA FLEJE" not in nombre_mat:
            factor_conversion = 30.0
            
        cant = cant_ingresada / factor_conversion

        row = {
            'bitacora_id': str(d['bid']),
            'brigada_responsable': d['bri'],
            'fecha_guardado': now.isoformat(),
            'inc': identifier,
            'fecha_asign_inc': b.get('fecha_asignacion_bd'),
            'sot': b.get('nrosot_bd'),
            'red_afect': b.get('red1_bd'),
            'region': b.get('zona_bd'),
            'subregion': b.get('departamento_bd'),
            'base_cuadrilla': b.get('base_bd'),
            'sup_claro': b.get('responsable_claro_bd'),
            'sup_contrata': b.get('responsable_cicsa_bd'),
            'id_site_inicio': b.get('nombresite_bd'),
            'name_site_inicio': b.get('nombresite_bd'),
            'otdr': b.get('otdr_bd'),
            'causa_averia': b.get('causa_bd'),
            'tipo_mmto': b.get('tipoaveria_bd'),
            'categoria': d['item'].get('categoria'),
            'subcategoria': d['item'].get('subcategoria'),
            'cod_material': d['item'].get('codigo'),
            'nombre_material': d['item'].get('descripcion'),
            'origen_material': origen,
            'precio_unit': price,
            'cant_material': cant,
            'moneda': 'D',
            'tc': 3.75,
            'subtotal': cant * price,
            'total_soles': (cant * price) * 3.75,
            'trabajo_concluido': b.get('estado_trabajo'),
            'porcentaje_ejecucion': 100 if b.get('is_cerrada') else 0,
            'validado_oym': 'PENDIENTE'
        }

        fa_str = b.get('fecha_asignacion_bd')
        if fa_str:
            try:
                dt = datetime.datetime.fromisoformat(str(fa_str).replace('Z', ''))
                row['mes_liq'] = dt.strftime('%B').upper()
                row['sem_uso'] = dt.strftime('%Y%U')
            except:
                pass

        final_res = supabase.table(Config.ACUMULADO_TABLE).insert([row]).execute()

        # Descontar stock de brigada
        try:
            bri = d['bri']
            cod = d['item'].get('codigo')
            stock_res = supabase.table('stock_brigadas').select('id, stock_actual').eq('brigada', bri).eq('cod_material', cod).execute()
            if stock_res.data:
                item_stock = stock_res.data[0]
                nuevo_stock = item_stock['stock_actual'] - cant
                supabase.table('stock_brigadas').update({
                    'stock_actual': nuevo_stock,
                    'updated_at': now.isoformat()
                }).eq('id', item_stock['id']).execute()
        except Exception as stock_e:
            print(f"Advertencia: No se pudo descontar stock: {stock_e}")

        # Supabase RLS puede devolver data=[] aun cuando el insert fue exitoso.
        # En ese caso recuperamos la fila recién insertada por bitacora_id + cod_material + fecha.
        saved_row = None
        if final_res.data:
            saved_row = final_res.data[0]
        else:
            try:
                fallback = supabase.table(Config.ACUMULADO_TABLE) \
                    .select("*") \
                    .eq('bitacora_id', str(d['bid'])) \
                    .eq('brigada_responsable', d['bri']) \
                    .eq('cod_material', d['item'].get('codigo')) \
                    .order('id', desc=True) \
                    .limit(1) \
                    .execute()
                saved_row = fallback.data[0] if fallback.data else row
            except:
                saved_row = row  # Devolver lo que enviamos como mínimo

        return jsonify({'ok': True, 'saved': saved_row})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/sin-consumo', methods=['POST'])
def marcar_sin_consumo():
    """
    Marca una bitácora como 'sin consumo de materiales'.
    Inserta un registro especial en materiales_acumulado con cod_material='SIN_CONSUMO'.
    Si ya existe uno, no duplica.
    """
    d = request.json or {}
    try:
        bid = str(d.get('bid', '')).strip()
        bri = str(d.get('bri', '')).strip().upper()
        if not bid or not bri:
            return jsonify({'error': 'bid y bri son requeridos'}), 400

        # Verificar que no exista ya un registro sin_consumo para esta bitacora+brigada
        existing = supabase.table(Config.ACUMULADO_TABLE) \
            .select('id') \
            .eq('bitacora_id', bid) \
            .eq('brigada_responsable', bri) \
            .eq('cod_material', 'SIN_CONSUMO') \
            .execute()
        if existing.data:
            return jsonify({'ok': True, 'msg': 'Ya estaba marcada como sin consumo.'})

        # Obtener datos de la bitacora para completar el registro
        b_res = supabase.table('bitacoras').select('*').eq('id', int(bid)).execute()
        b = b_res.data[0] if b_res.data else {}
        now = datetime.datetime.now()
        identifier = get_identifier(b)

        registro = {
            'bitacora_id':          bid,
            'brigada_responsable':  bri,
            'fecha_guardado':       now.isoformat(),
            'inc':                  identifier,
            'fecha_asign_inc':      b.get('fecha_asignacion_bd'),
            'sot':                  b.get('nrosot_bd'),
            'red_afect':            b.get('red1_bd'),
            'region':               b.get('zona_bd'),
            'subregion':            b.get('departamento_bd'),
            'base_cuadrilla':       b.get('base_bd'),
            'sup_claro':            b.get('responsable_claro_bd'),
            'sup_contrata':         b.get('responsable_cicsa_bd'),
            'id_site_inicio':       b.get('nombresite_bd'),
            'name_site_inicio':     b.get('nombresite_bd'),
            'causa_averia':         b.get('causa_bd'),
            'tipo_mmto':            b.get('tipoaveria_bd'),
            'cod_material':         'SIN_CONSUMO',
            'nombre_material':      'Sin consumo de materiales',
            'origen_material':      'CICSA',
            'cant_material':        0,
            'precio_unit':          0,
            'subtotal':             0,
            'total_soles':          0,
            'moneda':               'D',
            'tc':                   3.75,
            'trabajo_concluido':    b.get('estado_trabajo'),
            'porcentaje_ejecucion': 0,
            'validado_oym':         'SIN_CONSUMO',
        }
        supabase.table(Config.ACUMULADO_TABLE).insert([registro]).execute()
        return jsonify({'ok': True, 'msg': 'Bitácora marcada como sin consumo de materiales.'})
    except Exception as e:
        print(f"Error sin-consumo: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/deshacer-sin-consumo', methods=['POST'])
def deshacer_sin_consumo():
    """
    Deshace la marca 'sin consumo' eliminando el registro SIN_CONSUMO
    de materiales_acumulado para que la bitácora vuelva a aparecer
    como pendiente en el monitor.
    """
    d = request.json or {}
    try:
        record_id = d.get('id')
        bid       = str(d.get('bid', '')).strip()
        bri       = str(d.get('bri', '')).strip().upper()

        if record_id:
            # Borrar por id específico
            supabase.table(Config.ACUMULADO_TABLE).delete().eq('id', int(record_id)).execute()
        elif bid and bri:
            # Fallback: buscar por bitacora_id + brigada + cod_material
            supabase.table(Config.ACUMULADO_TABLE).delete() \
                .eq('bitacora_id', bid) \
                .eq('brigada_responsable', bri) \
                .eq('cod_material', 'SIN_CONSUMO') \
                .execute()
        else:
            return jsonify({'error': 'Se requiere id o (bid + bri)'}), 400

        return jsonify({'ok': True, 'msg': 'Marca sin consumo eliminada. La bitácora vuelve a estado pendiente.'})
    except Exception as e:
        print(f"Error deshacer-sin-consumo: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/delete-item', methods=['POST'])
def delete_item():
    d = request.json
    try:
        item_id = d.get('item_id') or d.get('id')
        subtotal_devuelto = 0
        # Devolver stock antes de borrar
        try:
            item_data = supabase.table(Config.ACUMULADO_TABLE).select('brigada_responsable, cod_material, cant_material, subtotal').eq('id', item_id).execute()
            if item_data.data:
                itm = item_data.data[0]
                bri = itm.get('brigada_responsable')
                cod = itm.get('cod_material')
                cant = float(itm.get('cant_material', 0))
                subtotal_devuelto = float(itm.get('subtotal', 0))
                stock_res = supabase.table('stock_brigadas').select('id, stock_actual').eq('brigada', bri).eq('cod_material', cod).execute()
                if stock_res.data:
                    old_stock = stock_res.data[0]
                    supabase.table('stock_brigadas').update({
                        'stock_actual': old_stock['stock_actual'] + cant,
                        'updated_at': datetime.datetime.now().isoformat()
                    }).eq('id', old_stock['id']).execute()
        except Exception as stock_e:
            print(f"Advertencia al devolver stock: {stock_e}")

        supabase.table(Config.ACUMULADO_TABLE).delete().eq('id', item_id).execute()
        return jsonify({'ok': True, 'subtotal_devuelto': subtotal_devuelto})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# =====================================================================
#  DASHBOARD DE STOCK (COORDINADOR DE MATERIALES)
# =====================================================================
@app.route('/dashboard-stock')
@login_required
def dashboard_stock():
    try:
        res_bri = supabase.table('brigada_tabla').select('brigada_main, "ZONA", contrata_bd').execute()
        brigadas_raw = {}
        zonas_set    = set()
        contratas_set = set()
        for item in (res_bri.data or []):
            bm   = item.get('brigada_main', '').strip()
            zona = item.get('ZONA', 'SIN ZONA')
            cont = item.get('contrata_bd', '') or ''
            if bm:
                brigadas_raw[bm] = zona
                if zona: zonas_set.add(zona)
                if cont: contratas_set.add(cont)
        brigadas_list  = sorted(brigadas_raw.keys())
        zonas_list     = sorted(zonas_set)
        contratas_list = sorted(contratas_set)
    except Exception as e:
        print("Error recuperando brigadas:", e)
        brigadas_list  = []
        zonas_list     = []
        contratas_list = []

    return render_template('dashboard_stock.html',
                           brigadas=brigadas_list,
                           zonas=zonas_list,
                           contratas=contratas_list,
                           user_name=session.get('user_name', ''),
                           role=session.get('role', ''))


@app.route('/api/dashboard-stock-data', methods=['GET'])
@login_required
def get_dashboard_stock_data():
    try:
        zona_filter     = request.args.get('zona', '')
        contrata_filter = request.args.get('contrata', '')
        res = supabase.table('stock_brigadas').select("*").execute()
        data = res.data or []

        # Enriquecer con zona y contrata desde brigada_tabla
        # La contrata guardada en stock_brigadas tiene prioridad sobre el join
        bri_map = get_brigada_zone_map()
        for r in data:
            bri  = r.get('brigada', '')
            info = bri_map.get(bri, {})
            r['zona'] = info.get('zona', 'SIN ZONA') if isinstance(info, dict) else str(info)
            # Prioridad: columna contrata de la fila → join brigada_tabla
            if not r.get('contrata'):
                r['contrata'] = info.get('contrata', '') if isinstance(info, dict) else ''

        if zona_filter:
            data = [r for r in data if r.get('zona', '').upper() == zona_filter.upper()]
        if contrata_filter:
            data = [r for r in data if (r.get('contrata') or '').upper() == contrata_filter.upper()]

        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/alertas-criticas', methods=['GET'])
@login_required
def alertas_criticas():
    """
    Retorna materiales cuyo stock_actual <= stock_minimo.
    Si stock_minimo = 0 y stock_inicial > 0, usa el 20% como fallback.
    """
    try:
        zona_filter = request.args.get('zona', '')
        res = supabase.table('stock_brigadas').select("*").execute()
        data = res.data or []
        zone_map = get_brigada_zone_map()

        alertas = []
        for r in data:
            actual   = float(r.get('stock_actual', 0))
            inicial  = float(r.get('stock_inicial', 0))
            minimo   = float(r.get('stock_minimo', 0))

            # Calcular porcentaje para la barra
            if inicial > 0:
                porcentaje = min((actual / inicial) * 100, 100)
            else:
                porcentaje = 0 if actual <= 0 else 100

            # Umbral de alerta: usa stock_minimo si está definido, si no 20% del inicial
            umbral = minimo if minimo > 0 else (inicial * 0.20)
            es_critico = actual <= umbral

            bri = r.get('brigada', '')
            r['zona']       = zone_map.get(bri, 'SIN ZONA')
            r['porcentaje'] = round(porcentaje, 1)
            r['umbral']     = round(umbral, 2)
            r['es_cero']    = actual <= 0

            if zona_filter and r['zona'].upper() != zona_filter.upper():
                continue

            if es_critico:
                alertas.append(r)

        # Ordenar: primero los en cero, luego por porcentaje ascendente
        alertas.sort(key=lambda x: (not x['es_cero'], x['porcentaje']))
        return jsonify(alertas)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/stats-por-zona', methods=['GET'])
@login_required
def stats_por_zona():
    """Estadísticas de salud de stock agrupadas por zona, usando stock_minimo como umbral."""
    try:
        res = supabase.table('stock_brigadas').select("*").execute()
        data = res.data or []
        zone_map = get_brigada_zone_map()

        zonas: dict = {}
        for r in data:
            bri     = r.get('brigada', '')
            zona    = zone_map.get(bri, 'SIN ZONA')
            actual  = float(r.get('stock_actual', 0))
            inicial = float(r.get('stock_inicial', 0))
            minimo  = float(r.get('stock_minimo', 0))

            ptc = min((actual / inicial * 100), 100) if inicial > 0 else (0 if actual <= 0 else 100)
            umbral = minimo if minimo > 0 else (inicial * 0.20)
            es_critico = actual <= umbral

            if zona not in zonas:
                zonas[zona] = {
                    'zona': zona,
                    'total_items': 0,
                    'items_criticos': 0,
                    'items_ok': 0,
                    'porcentaje_promedio': []
                }
            zonas[zona]['total_items'] += 1
            zonas[zona]['porcentaje_promedio'].append(ptc)
            if es_critico:
                zonas[zona]['items_criticos'] += 1
            else:
                zonas[zona]['items_ok'] += 1

        result = []
        for z, info in zonas.items():
            pts = info['porcentaje_promedio']
            info['porcentaje_promedio'] = round(sum(pts) / len(pts), 1) if pts else 0
            result.append(info)

        return jsonify(sorted(result, key=lambda x: x['porcentaje_promedio']))
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/despachar-stock', methods=['POST'])
@login_required
def despachar_stock():
    d = request.json
    try:
        bri      = d.get('brigada').strip().upper()
        cod      = d.get('cod_material').strip()
        nombre   = d.get('nombre_material', '').strip()
        cant     = float(d.get('cantidad', 0))
        minimo   = float(d.get('stock_minimo', 0))  # Umbral configurable
        contrata = str(d.get('contrata', '') or '').strip().upper()
        now      = datetime.datetime.now().isoformat()

        if cant <= 0:
            return jsonify({'error': 'La cantidad debe ser mayor a 0'}), 400

        exist_res = supabase.table('stock_brigadas') \
            .select('id, stock_actual, stock_inicial, stock_minimo, contrata') \
            .eq('brigada', bri).eq('cod_material', cod).execute()

        if exist_res.data:
            item = exist_res.data[0]
            nuevo_stock   = float(item['stock_actual']) + cant
            nuevo_inicial = float(item.get('stock_inicial') or 0) + cant
            # Actualizar stock_minimo solo si se proporcionó uno nuevo
            update_payload = {
                'stock_actual':  nuevo_stock,
                'stock_inicial': nuevo_inicial,
                'updated_at':    now
            }
            if minimo > 0:
                update_payload['stock_minimo'] = minimo
            if contrata:
                update_payload['contrata'] = contrata
            supabase.table('stock_brigadas').update(update_payload).eq('id', item['id']).execute()
        else:
            supabase.table('stock_brigadas').insert([{
                'brigada':         bri,
                'cod_material':    cod,
                'nombre_material': nombre,
                'stock_actual':    cant,
                'stock_inicial':   cant,
                'stock_minimo':    minimo,
                'contrata':        contrata,
                'updated_at':      now
            }]).execute()
            
        # ── REGISTRAR EN HISTORIAL DE DESPACHOS ──
        responsable = session.get('user', 'Sistema')
        supabase.table('historial_despachos').insert([{
            'brigada': bri,
            'cod_material': cod,
            'nombre_material': nombre,
            'cantidad': cant,
            'responsable': responsable,
            'tipo': 'MANUAL',
            'fecha': now
        }]).execute()

        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/ajustar-minimo', methods=['POST'])
@login_required
def ajustar_minimo():
    """Permite al coordinador de materiales definir/actualizar el stock mínimo de alerta."""
    d = request.json
    try:
        item_id = d.get('id')
        nuevo_minimo = float(d.get('stock_minimo', 0))
        if nuevo_minimo < 0:
            return jsonify({'error': 'El mínimo no puede ser negativo'}), 400
        supabase.table('stock_brigadas').update({
            'stock_minimo': nuevo_minimo,
            'updated_at':   datetime.datetime.now().isoformat()
        }).eq('id', item_id).execute()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/kpi-consumo', methods=['GET'])
@login_required
def kpi_consumo():
    try:
        bri = request.args.get('brigada')
        cod = request.args.get('cod_material')
        
        # 1. Traer consumos (salidas)
        q_cons = supabase.table(Config.ACUMULADO_TABLE).select('fecha_guardado, brigada_responsable, cod_material, nombre_material, cant_material, bitacora_id')
        if bri: q_cons = q_cons.eq('brigada_responsable', bri)
        if cod: q_cons = q_cons.eq('cod_material', cod)
        res_cons = q_cons.execute()
        
        # 2. Traer despachos (entradas)
        q_desp = supabase.table('historial_despachos').select('fecha, brigada, cod_material, nombre_material, cantidad, responsable, tipo')
        if bri: q_desp = q_desp.eq('brigada', bri)
        if cod: q_desp = q_desp.eq('cod_material', cod)
        res_desp = q_desp.execute()
        
        # Mapear a un formato unificado
        eventos = []
        for c in (res_cons.data or []):
            eventos.append({
                'fecha': c.get('fecha_guardado'),
                'brigada': c.get('brigada_responsable'),
                'cod_material': c.get('cod_material'),
                'nombre_material': c.get('nombre_material'),
                'cantidad': -abs(float(c.get('cant_material') or 0)),
                'tipo': 'CONSUMO',
                'responsable': f"Técnico (Bitácora {c.get('bitacora_id')})"
            })
            
        for d in (res_desp.data or []):
            eventos.append({
                'fecha': d.get('fecha'),
                'brigada': d.get('brigada'),
                'cod_material': d.get('cod_material'),
                'nombre_material': d.get('nombre_material'),
                'cantidad': abs(float(d.get('cantidad') or 0)),
                'tipo': f"DESPACHO {d.get('tipo', '')}",
                'responsable': d.get('responsable')
            })
            
        # Ordenar por fecha desc
        eventos.sort(key=lambda x: (x['fecha'] or ''), reverse=True)
        
        return jsonify(eventos)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/corregir-stock', methods=['POST'])
@login_required
def corregir_stock():
    d = request.json
    try:
        item_id = d.get('id')
        nuevo_valor = float(d.get('nuevo_stock', 0))
        if nuevo_valor < 0:
            return jsonify({'error': 'El stock no puede ser negativo'}), 400
        supabase.table('stock_brigadas').update({
            'stock_actual': nuevo_valor,
            'updated_at': datetime.datetime.now().isoformat()
        }).eq('id', item_id).execute()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/eliminar-stock', methods=['POST'])
@admin_required
def eliminar_stock():
    """Elimina permanentemente una fila de stock_brigadas. Solo admin."""
    d = request.json
    try:
        item_id = d.get('id')
        if not item_id:
            return jsonify({'error': 'ID requerido'}), 400
        supabase.table('stock_brigadas').delete().eq('id', item_id).execute()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/backfill-contratas', methods=['POST'])
@admin_required
def backfill_contratas():
    """
    Rellena la columna 'contrata' de stock_brigadas para todos los registros
    que la tengan vacía o nula, cruzando con brigada_tabla.
    Operación segura: solo actualiza, nunca elimina.
    """
    try:
        # Cargar mapa oficial brigada → contrata
        bri_res = supabase.table('brigada_tabla') \
            .select('brigada_main, contrata_bd') \
            .execute()
        bri_map = {}
        for b in (bri_res.data or []):
            bm = str(b.get('brigada_main') or '').strip().upper()
            ct = str(b.get('contrata_bd') or '').strip().upper()
            if bm and ct:
                bri_map[bm] = ct

        if not bri_map:
            return jsonify({'error': 'No se pudo cargar brigada_tabla'}), 500

        # Obtener registros de stock sin contrata
        stock_res = supabase.table('stock_brigadas') \
            .select('id, brigada, contrata') \
            .execute()

        now = datetime.datetime.now().isoformat()
        actualizados  = 0
        sin_brigada   = []   # brigadas en stock no registradas en brigada_tabla

        for r in (stock_res.data or []):
            # Solo procesar filas sin contrata
            if r.get('contrata'):
                continue

            bri = str(r.get('brigada') or '').strip().upper()
            contrata_oficial = bri_map.get(bri, '')

            if not contrata_oficial:
                sin_brigada.append(bri)
                continue

            supabase.table('stock_brigadas').update({
                'contrata':   contrata_oficial,
                'updated_at': now
            }).eq('id', r['id']).execute()
            actualizados += 1

        msg = f"✅ {actualizados} registro(s) actualizados con su contrata oficial."
        response = {'ok': True, 'actualizados': actualizados, 'msg': msg}

        if sin_brigada:
            uniq = list(dict.fromkeys(sin_brigada))
            response['warning'] = (
                f"⚠️ {len(uniq)} brigada(s) en stock sin coincidencia en brigada_tabla "
                f"(no se pudo rellenar su contrata): "
                f"{', '.join(uniq[:10])}{'...' if len(uniq) > 10 else ''}"
            )

        return jsonify(response)
    except Exception as e:
        print(f"Error backfill contratas: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/despacho-masivo', methods=['POST'])
@login_required
def despacho_masivo():
    """
    Carga masiva de stock desde Excel.
    Columnas requeridas: CODIGO AX | CONTRATA | BRIGADA | UNIDAD | CANTIDAD
    Validación previa contra brigada_tabla:
      - Brigadas no registradas son RECHAZADAS.
      - La contrata oficial de BD tiene prioridad; se reportan discrepancias.
    """
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No se encontró archivo en la petición'}), 400
        file = request.files['file']
        es_reemplazo = request.form.get('modo') == 'reemplazar'
        if file.filename == '':
            return jsonify({'error': 'Nombre de archivo vacío'}), 400

        file.seek(0)
        df_raw = pd.read_excel(file, header=None, dtype=str)
        
        header_idx = 0
        brigada_global = None
        contrata_global = None
        
        for i, row in df_raw.iterrows():
            row_vals = [str(v).upper().strip() for v in row if pd.notna(v)]
            row_str = " ".join(row_vals)
            
            # Detectar metadata global (Formato CARSO u otros)
            if "CONTRATISTA" in row_str or "CUADRILLA" in row_str:
                for idx, val in enumerate(row):
                    if pd.isna(val): continue
                    val_str = str(val).upper().strip()
                    if "CONTRATISTA" in val_str or "CUADRILLA" in val_str:
                        # El valor suele estar a la derecha
                        for j in range(idx+1, len(row)):
                            if pd.notna(row[j]):
                                next_val = str(row[j]).strip().upper()
                                if '/' in next_val:
                                    parts = next_val.split('/')
                                    if len(parts) >= 2:
                                        contrata_global = parts[0].strip()
                                        brigada_global = parts[1].strip()
                                break
                        break

            # Detectar fila de cabeceras (AX y CANTIDAD)
            has_ax = any("AX" in v or "CÓDIGO" in v or "CODIGO" in v for v in row_vals)
            has_cant = any("CANT" in v for v in row_vals)
            if has_ax and has_cant:
                header_idx = i
                break

        file.seek(0)
        df = pd.read_excel(file, header=header_idx, dtype=str)
        cols_upper = [str(c).upper().strip() for c in df.columns]
        df.columns = cols_upper
        
        # Inyectar metadata extraída si no existe la columna en el header
        if brigada_global and not any('BRIGADA' in c for c in cols_upper):
            df['BRIGADA'] = brigada_global
            cols_upper.append('BRIGADA')
        if contrata_global and not any('CONTRATA' in c or 'EMPRESA' in c for c in cols_upper):
            df['CONTRATA'] = contrata_global
            cols_upper.append('CONTRATA')

        # ── Mapeo flexible de columnas ──────────────────────────────────
        col_cod   = next((c for c in cols_upper if 'AX' in c or ('COD' in c and 'CONTRATA' not in c)), None)
        col_bri   = next((c for c in cols_upper if 'BRIGADA' in c), None)
        col_cant  = next((c for c in cols_upper if 'CANTIDAD' in c or 'CANT' in c), None)
        col_cont  = next((c for c in cols_upper if 'CONTRATA' in c or 'EMPRESA' in c), None)
        col_unid  = next((c for c in cols_upper if 'UNIDAD' in c or 'UNID' in c), None)
        col_comercial = next((c for c in cols_upper if 'COMERCIAL' in c or 'COMUN' in c), None)

        if not col_cod or not col_bri or not col_cant:
            return jsonify({'error':
                'El archivo debe tener columnas: CODIGO AX, BRIGADA, CANTIDAD. '
                f'Columnas detectadas: {", ".join(cols_upper)}'}), 400

        df = df[df[col_cant].notnull()]
        df[col_cant] = pd.to_numeric(df[col_cant], errors='coerce').fillna(0)
        df = df[df[col_cant] > 0]

        if df.empty:
            return jsonify({'error': 'No se encontraron filas con cantidad válida > 0'}), 400

        # ── Cargar brigada_tabla para validación ────────────────────────
        # { brigada_main_upper → { zona, contrata } }
        brigada_bd_map = {}
        try:
            bri_res = supabase.table('brigada_tabla') \
                .select('brigada_main, "ZONA", contrata_bd') \
                .execute()
            for b in (bri_res.data or []):
                bm = str(b.get('brigada_main') or '').strip().upper()
                if bm:
                    brigada_bd_map[bm] = {
                        'zona':     b.get('ZONA', ''),
                        'contrata': str(b.get('contrata_bd') or '').strip().upper()
                    }
        except Exception as bri_e:
            print(f"Advertencia brigada_tabla: {bri_e}")

        # ── Resolver nombres desde catalogo_unificado ───────────────────
        # Recolectar todos los códigos AX únicos del Excel
        codigos_ax = list(set(
            str(row[col_cod]).strip()[:-2].lstrip('0') if str(row[col_cod]).strip().endswith('.0') else str(row[col_cod]).strip().lstrip('0')
            for _, row in df.iterrows()
            if pd.notna(row[col_cod]) and str(row[col_cod]).strip() not in ('', 'nan', 'None')
        ))

        nombre_map = {}  # { cod_ax_limpio → descripcion }
        if codigos_ax:
            try:
                # Buscar en catálogo por lotes de 100
                for i in range(0, len(codigos_ax), 100):
                    lote = codigos_ax[i:i+100]
                    cat_res = supabase.table('catalogo_unificado') \
                        .select('cod_ax, descripcion, empresa') \
                        .in_('cod_ax', lote) \
                        .execute()
                    for c in (cat_res.data or []):
                        ax_clean = str(c.get('cod_ax', '')).strip().lstrip('0')
                        if ax_clean and ax_clean not in nombre_map:
                            nombre_map[ax_clean] = c.get('descripcion', '').strip()
            except Exception as cat_e:
                print(f"Advertencia catálogo: {cat_e}")

        # ── Procesar filas ───────────────────────────────────────────────
        now = datetime.datetime.now().isoformat()
        stock_res = supabase.table('stock_brigadas') \
            .select('id, brigada, cod_material, stock_actual, stock_inicial, stock_minimo, contrata') \
            .execute()
        stock_map = {(d['brigada'].upper(), d['cod_material']): d for d in (stock_res.data or [])}

        ops_insert         = []
        ops_historial      = []
        procesados         = 0
        sin_nombre         = []
        brigadas_invalidas = []   # brigadas no halladas en brigada_tabla
        discrepancias_cont = []   # contrata en Excel difiere de la oficial en BD
        
        responsable_masivo = session.get('user', 'Sistema')

        for _, row in df.iterrows():
            bri  = str(row[col_bri]).upper().strip()
            raw_cod = str(row[col_cod]).strip()
            if raw_cod.endswith('.0'): raw_cod = raw_cod[:-2]
            cod  = raw_cod.lstrip('0')
            cant = float(row[col_cant])

            if not bri or not cod or cant <= 0:
                continue

            # ── Validar brigada contra brigada_tabla ─────────────────────
            if brigada_bd_map and bri not in brigada_bd_map:
                brigadas_invalidas.append(bri)
                continue  # Rechazar fila con brigada desconocida

            # ── Resolver contrata oficial desde BD ──────────────────────
            contrata_oficial = brigada_bd_map.get(bri, {}).get('contrata', '')

            # Leer contrata del Excel solo para contrastar
            contrata_excel = ''
            if col_cont:
                raw_cont = row.get(col_cont, '')
                if pd.notna(raw_cont):
                    contrata_excel = str(raw_cont).strip().upper()

            # Detectar discrepancia (informativa, no bloquea la carga)
            if contrata_excel and contrata_oficial and contrata_excel != contrata_oficial:
                discrepancias_cont.append(
                    f"{bri}: Excel='{contrata_excel}' → BD='{contrata_oficial}'"
                )

            # Siempre se guarda la contrata oficial de brigada_tabla
            contrata_val = contrata_oficial or contrata_excel

            # Leer nombre comercial del Excel si existe
            nombre_comercial = ''
            if col_comercial:
                raw_com = row.get(col_comercial, '')
                if pd.notna(raw_com):
                    nombre_comercial = str(raw_com).strip()

            nombre = nombre_map.get(cod, '')
            if not nombre:
                nombre = f'AX-{cod}'
                sin_nombre.append(cod)

            key = (bri, cod)

            if key in stock_map:
                item = stock_map[key]
                if es_reemplazo:
                    nuevo_stock   = cant
                    nuevo_inicial = max(float(item.get('stock_inicial') or 0), cant)
                else:
                    nuevo_stock   = float(item['stock_actual']) + cant
                    nuevo_inicial = float(item.get('stock_inicial') or 0) + cant

                update_payload = {
                    'stock_actual':    nuevo_stock,
                    'stock_inicial':   nuevo_inicial,
                    'nombre_material': nombre,
                    'updated_at':      now
                }
                if nombre_comercial:
                    update_payload['nombre_comercial'] = nombre_comercial
                
                # Sólo actualizar contrata si viene en el Excel (no sobreescribir con vacío)
                if contrata_val:
                    update_payload['contrata'] = contrata_val

                supabase.table('stock_brigadas').update(update_payload).eq('id', item['id']).execute()
            else:
                insert_payload = {
                    'brigada':         bri,
                    'cod_material':    cod,
                    'nombre_material': nombre,
                    'stock_actual':    cant,
                    'stock_inicial':   cant,
                    'stock_minimo':    0,
                    'contrata':        contrata_val,
                    'updated_at':      now
                }
                if nombre_comercial:
                    insert_payload['nombre_comercial'] = nombre_comercial
                
                ops_insert.append(insert_payload)
                
            ops_historial.append({
                'brigada': bri,
                'cod_material': cod,
                'nombre_material': nombre,
                'cantidad': cant,
                'responsable': responsable_masivo,
                'tipo': 'MASIVO',
                'fecha': now
            })

            procesados += 1

        if ops_insert:
            for i in range(0, len(ops_insert), 500):
                supabase.table('stock_brigadas').insert(ops_insert[i:i+500]).execute()
                
        if ops_historial:
            for i in range(0, len(ops_historial), 500):
                try:
                    supabase.table('historial_despachos').insert(ops_historial[i:i+500]).execute()
                except Exception as hist_e:
                    print(f"Error guardando historial despachos masivo: {hist_e}")

        # ── Construir respuesta estructurada ────────────────────────────
        sin_nombre_uniq    = list(dict.fromkeys(sin_nombre))
        bri_inv_uniq       = list(dict.fromkeys(brigadas_invalidas))
        disc_cont_uniq     = list(dict.fromkeys(discrepancias_cont))

        # Calcular nuevos vs actualizados
        nuevos      = len(ops_insert)
        actualizados = procesados - nuevos

        return jsonify({
            'ok':          procesados > 0,
            'procesados':  procesados,
            'nuevos':      nuevos,
            'actualizados': actualizados,
            'rechazadas':  bri_inv_uniq,          # brigadas no halladas en BD
            'sin_catalogo': sin_nombre_uniq,      # códigos AX sin nombre en catálogo
            'discrepancias': disc_cont_uniq,      # contrata Excel ≠ BD
        })
    except Exception as e:
        print(f"Error Masivo: {e}")
        return jsonify({'error': str(e)}), 500


# =====================================================================
#  MONITOR DE COORDINADOR (VISTA DE PENDIENTES)
# =====================================================================
@app.route('/api/exportar-cumplimiento', methods=['POST'])
@login_required
def exportar_cumplimiento():
    try:
        req_data = request.json.get('data', [])
        if not req_data:
            return "No hay datos para exportar", 400
        
        rows = []
        for b in req_data:
            if b.get('tiene_material'):
                status_txt = "Registrado"
            elif b.get('tiene_sin_consumo'):
                status_txt = "Sin Consumo"
            else:
                status_txt = "Pendiente"
                
            fecha = b.get('fecha_asignacion_bd')
            if fecha:
                try:
                    fecha_fmt = datetime.datetime.fromisoformat(str(fecha).replace('Z', '')).strftime('%d/%m/%Y')
                except:
                    fecha_fmt = str(fecha)
            else:
                fecha_fmt = '—'
                
            rows.append({
                'ESTADO REGISTRO': status_txt,
                'INCIDENCIA / TICKET': b.get('identificador') or b.get('id'),
                'ID BITACORA': b.get('id'),
                'ZONA': b.get('zona_bd') or '—',
                'CONTRATA': b.get('contrata_cicsa') or '—',
                'BRIGADA': b.get('bri1_oficial') or '—',
                'ASIGNACION': fecha_fmt,
                'TIPO / ESTADO': b.get('estado_textual_bd') or '—'
            })
            
        df = pd.DataFrame(rows)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Status de Cumplimiento')
            
        output.seek(0)
        filename = f"Status_Cumplimiento_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
        return send_file(output,
                         mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                         as_attachment=True,
                         download_name=filename)
    except Exception as e:
        print(f"Error exportar-cumplimiento: {e}")
        return jsonify({'error': str(e)}), 500



@app.route('/monitor')
@login_required
def monitor_view():
    """Vista del coordinador de operaciones para monitorear el registro de materiales."""
    try:
        res_zonas = supabase.table('brigada_tabla').select('"ZONA"').execute()
        zonas = sorted(set(r.get('ZONA', '') for r in (res_zonas.data or []) if r.get('ZONA')))
    except:
        zonas = []
    return render_template('monitor.html',
                           zonas=zonas,
                           user_name=session.get('user_name', ''),
                           role=session.get('role', ''))


if __name__ == '__main__':
    app.run(debug=True, port=5000, host='0.0.0.0')