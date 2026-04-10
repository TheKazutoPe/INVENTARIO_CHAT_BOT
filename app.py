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

ROLES_PERMITIDOS = ('admin', 'user')   # Únicos roles que pueden entrar al sistema
ROLES_ADMIN      = ('admin',)           # Acceso exclusivo: exportación masiva de Excel
ROLES_ALL        = ('admin', 'user')   # Acceso completo sin exportar

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
    """Devuelve un mapa {brigada_main: ZONA} desde brigada_tabla."""
    try:
        res = supabase.table('brigada_tabla').select('brigada_main, "ZONA"').execute()
        return {r['brigada_main']: r.get('ZONA', 'SIN ZONA') for r in (res.data or []) if r.get('brigada_main')}
    except:
        return {}

# =====================================================================
#  AUTENTICACIÓN
# =====================================================================
@app.route('/')
def index():
    if 'user_email' in session:
        role = session.get('role', '')
        if role == 'admin':
            return redirect(url_for('reportes_view'))
        if role == 'user':
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
        raw_names = [bitacora.get(f'bri{i}_oficial') for i in range(1, 6) if bitacora.get(f'bri{i}_oficial')]

        if raw_names:
            try:
                map_res = supabase.table('brigada_tabla').select('name_brigada_bd, brigada_main').in_('name_brigada_bd', raw_names).execute()
                mapping = {x['name_brigada_bd']: x['brigada_main'] for x in map_res.data}
                for name in raw_names:
                    short = mapping.get(name, name)
                    brigadas.append({'val': short, 'lbl': short})
            except:
                brigadas = [{'val': n, 'lbl': n} for n in raw_names]

        # Obtener stock disponible por brigada para mostrar advertencias
        stock_brigadas = {}
        if brigadas:
            bri_names = [b['val'] for b in brigadas]
            try:
                stock_res = supabase.table('stock_brigadas').select('brigada, cod_material, nombre_material, stock_actual, stock_inicial').in_('brigada', bri_names).execute()
                for s in (stock_res.data or []):
                    key = s['brigada']
                    if key not in stock_brigadas:
                        stock_brigadas[key] = []
                    stock_brigadas[key].append(s)
            except:
                pass

        return render_template('materiales_form.html',
                               bid=bitacora_id,
                               b=bitacora,
                               brigadas=brigadas,
                               historial=historial,
                               stock_brigadas=stock_brigadas)
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
                r['zona_brigada'] = zone_map.get(bri, r.get('region', 'SIN ZONA'))

        # Filtro por zona (post-enriquecimiento)
        if zona_filter:
            data = [r for r in data if r.get('zona_brigada', '').upper() == zona_filter.upper()]

        # Ocultar datos financieros para contratas (role='user')
        CAMPOS_FINANCIEROS = ('precio_unit', 'subtotal', 'total_soles', 'tc', 'moneda')
        if session.get('role') == 'user':
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

            bri = r.get('brigada_responsable', 'SIN BRIGADA')
            zona = zone_map.get(bri, 'SIN ZONA')
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
    Retorna bitácoras que no tienen ningún material registrado.
    Útil para el coordinador de operaciones para monitorear cumplimiento.
    """
    try:
        zona = request.args.get('zona', '')
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        dias = request.args.get('dias')

        query = supabase.table('bitacoras') \
            .select('id, codigo_bd, nroincidencia_bd, nrotas_bd, nrosot_bd, zona_bd, bri1_oficial, contrata_cicsa, fecha_asignacion_bd, estado_textual_bd, titulo_bd') \
            .eq('is_cerrada', False)
            
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

        res = query.limit(800).execute()
        bitacoras = res.data or []

        # Obtener IDs con material registrado
        if bitacoras:
            bids = [str(b['id']) for b in bitacoras]
            mat_res = supabase.table(Config.ACUMULADO_TABLE).select('bitacora_id').in_('bitacora_id', bids).execute()
            ids_con_material = set(str(r['bitacora_id']) for r in (mat_res.data or []))

            # Filtrar las que no tienen material
            pendientes = []
            for b in bitacoras:
                b['tiene_material'] = str(b['id']) in ids_con_material
                b['identificador'] = get_identifier(b)
                pendientes.append(b)

            return jsonify(pendientes)

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
            r['zona_brigada'] = zone_map.get(bri, r.get('region', ''))

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
                'ZONA': zone_map.get(bri, ''),
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
        res_bri = supabase.table('brigada_tabla').select('brigada_main, "ZONA"').execute()
        brigadas_raw = {}
        zonas_set = set()
        for item in (res_bri.data or []):
            bm = item.get('brigada_main', '').strip()
            zona = item.get('ZONA', 'SIN ZONA')
            if bm:
                brigadas_raw[bm] = zona
                if zona:
                    zonas_set.add(zona)
        brigadas_list = sorted(brigadas_raw.keys())
        zonas_list = sorted(zonas_set)
    except Exception as e:
        print("Error recuperando brigadas:", e)
        brigadas_list = []
        zonas_list = []

    return render_template('dashboard_stock.html',
                           brigadas=brigadas_list,
                           zonas=zonas_list,
                           user_name=session.get('user_name', ''),
                           role=session.get('role', ''))


@app.route('/api/dashboard-stock-data', methods=['GET'])
@login_required
def get_dashboard_stock_data():
    try:
        zona_filter = request.args.get('zona', '')
        res = supabase.table('stock_brigadas').select("*").execute()
        data = res.data or []

        # Enriquecer con zona
        zone_map = get_brigada_zone_map()
        for r in data:
            bri = r.get('brigada', '')
            r['zona'] = zone_map.get(bri, 'SIN ZONA')

        if zona_filter:
            data = [r for r in data if r.get('zona', '').upper() == zona_filter.upper()]

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
        bri    = d.get('brigada').strip().upper()
        cod    = d.get('cod_material').strip()
        nombre = d.get('nombre_material', '').strip()
        cant   = float(d.get('cantidad', 0))
        minimo = float(d.get('stock_minimo', 0))  # Umbral configurable
        now    = datetime.datetime.now().isoformat()

        if cant <= 0:
            return jsonify({'error': 'La cantidad debe ser mayor a 0'}), 400

        exist_res = supabase.table('stock_brigadas') \
            .select('id, stock_actual, stock_inicial, stock_minimo') \
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
            supabase.table('stock_brigadas').update(update_payload).eq('id', item['id']).execute()
        else:
            supabase.table('stock_brigadas').insert([{
                'brigada':        bri,
                'cod_material':   cod,
                'nombre_material': nombre,
                'stock_actual':   cant,
                'stock_inicial':  cant,
                'stock_minimo':   minimo,
                'updated_at':     now
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


@app.route('/api/despacho-masivo', methods=['POST'])
@login_required
def despacho_masivo():
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No se encontró archivo en la petición'}), 400
        file = request.files['file']
        es_reemplazo = request.form.get('modo') == 'reemplazar'
        if file.filename == '':
            return jsonify({'error': 'Nombre de archivo vacío'}), 400

        df = pd.read_excel(file)
        cols_upper = [str(c).upper().strip() for c in df.columns]
        df.columns = cols_upper

        col_cod = next((c for c in cols_upper if 'COD' in c or 'AX' in c), None)
        col_bri = next((c for c in cols_upper if 'BRIGADA' in c), None)
        col_cant = next((c for c in cols_upper if 'CANTIDAD' in c or 'CANT' in c), None)
        col_desc = next((c for c in cols_upper if ('DESC' in c or 'MATERIAL' in c) and 'CANT' not in c), None)

        if not col_cod or not col_bri or not col_cant:
            return jsonify({'error': 'El archivo debe contener columnas para CÓDIGO/AX, BRIGADA y CANTIDAD.'}), 400

        df = df[df[col_cant].notnull()]
        df[col_cant] = pd.to_numeric(df[col_cant], errors='coerce').fillna(0)
        df = df[df[col_cant] > 0]

        now = datetime.datetime.now().isoformat()
        stock_res = supabase.table('stock_brigadas').select('id, brigada, cod_material, stock_actual, stock_inicial, stock_minimo').execute()
        stock_map = {(d['brigada'].upper(), d['cod_material']): d for d in (stock_res.data or [])}

        # Detectar columna stock_minimo en el excel (opcional)
        col_min = next((c for c in cols_upper if 'MINIMO' in c or 'MIN' in c), None)

        ops_insert = []
        for index, row in df.iterrows():
            bri    = str(row[col_bri]).upper().strip()
            cod    = str(row[col_cod]).strip().lstrip('0')
            desc   = str(row[col_desc]).strip() if col_desc else f"Material {cod}"
            cant   = float(row[col_cant])
            minimo = float(row[col_min]) if col_min and pd.notna(row[col_min]) else 0
            key    = (bri, cod)

            if key in stock_map:
                item = stock_map[key]
                if es_reemplazo:
                    nuevo_stock   = cant
                    nuevo_inicial = max(float(item.get('stock_inicial') or 0), cant)
                else:
                    nuevo_stock   = float(item['stock_actual']) + cant
                    nuevo_inicial = float(item.get('stock_inicial') or 0) + cant

                update_payload = {
                    'stock_actual':  nuevo_stock,
                    'stock_inicial': nuevo_inicial,
                    'nombre_material': desc,
                    'updated_at':    now
                }
                if minimo > 0:
                    update_payload['stock_minimo'] = minimo
                supabase.table('stock_brigadas').update(update_payload).eq('id', item['id']).execute()
            else:
                ops_insert.append({
                    'brigada':         bri,
                    'cod_material':    cod,
                    'nombre_material': desc,
                    'stock_actual':    cant,
                    'stock_inicial':   cant,
                    'stock_minimo':    minimo,
                    'updated_at':      now
                })

        if ops_insert:
            for i in range(0, len(ops_insert), 500):
                supabase.table('stock_brigadas').insert(ops_insert[i:i+500]).execute()

        return jsonify({'ok': True, 'msg': f"Se procesaron {len(df)} registros con éxito."})
    except Exception as e:
        print(f"Error Masivo: {e}")
        return jsonify({'error': str(e)}), 500


# =====================================================================
#  MONITOR DE COORDINADOR (VISTA DE PENDIENTES)
# =====================================================================
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