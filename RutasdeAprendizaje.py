import streamlit as st
import pandas as pd
import math
import re
import unicodedata

# =============================================================================
# 0. CONFIGURACIÓN DEL MOTOR (aquí se afina el comportamiento sin tocar la lógica)
# =============================================================================

# Dónde se busca y cuánto vale un match según el campo del catálogo.
# Un match en el Nombre del curso pesa 6 veces más que uno en el Público objetivo.
CAMPOS_PESO = {
    "Nombre": 3.0,
    "Descripción": 2.0,
    "Normativa": 1.0,        # aquí viven los nombres de protocolos (PREXOR, TMERT, PLANESI...)
    "Subcategoría": 0.5,
    "Público objetivo": 0.5,
}

# Fuerza del match de una palabra clave contra un campo (se multiplica por el peso del campo)
FUERZA_FRASE_EXACTA = 1.00      # la frase aparece tal cual (ej: "partes móviles")
FUERZA_TODAS_LAS_PALABRAS = 0.85  # están todas las palabras, en cualquier orden o por sinónimo
FUERZA_PARCIAL = 0.40           # frases largas con cobertura parcial
COBERTURA_MIN_PARCIAL = 0.60

# Un curso entra a la ruta si:
#   (a) su puntaje total >= umbral (ajustable en pantalla), Y
#   (b) al menos UNA palabra clave pegó con fuerza razonable (evita que 5 matches
#       débiles en 'Normativa' sumados cuelen un curso irrelevante).
# Con estos valores entra un curso si una palabra clave aparece en su NOMBRE,
# o si al menos dos aparecen en su DESCRIPCIÓN.
UMBRAL_DEFECTO = 2.5
PUNTAJE_MIN_UNA_FRASE = 1.7

# Normas del selector -> (campo, patrón para FILTRAR, patrón para dar BONUS de orden)
PATRONES_NORMA = {
    "Ley 16.744": ("Normativa", r"16\.?744", r"16\.?744"),
    "D.S. N°44": ("Normativa", r"\bn\s*[°º]?\s*44\b", r"\bn\s*[°º]?\s*44\b"),
    "D.S. N°594": ("Normativa", r"\bn\s*[°º]?\s*594\b", r"\bn\s*[°º]?\s*594\b"),
    "Protocolos MINSAL": ("Normativa", r"protocolo", r"protocolo"),
    "Ley Karin": ("Normativa", r"21\.?643|karin", r"21\.?643|karin"),
    "Perfil: Comités Paritarios": ("Público objetivo", r"comit", r"comit"),
    "Industria: Minería": ("Industria", r"miner|transversal", r"miner"),
    "Industria: Construcción": ("Industria", r"construcc|transversal", r"construcc"),
}
BONUS_NORMA = 0.75  # solo ordena; nunca hace entrar a un curso por sí solo


# =============================================================================
# 1. LIMPIEZA Y TRANSFORMACIÓN
# =============================================================================
def normalizar_texto(texto):
    """Minúsculas, sin tildes, solo letras/números. Elimina también '\\n' literales."""
    if texto is None or (not isinstance(texto, str) and pd.isna(texto)):
        return ""
    texto = str(texto).lower().replace("\\n", " ")
    texto = "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")
    texto = re.sub(r"[^a-z0-9\s]", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def raiz(palabra):
    """Raíz liviana para español: quita plural y corta a 6 letras.
    'herramientas'->'herram', 'psicosociales'->'psicos', 'cortes'->'corte'."""
    if len(palabra) > 3:
        if palabra.endswith("es") and palabra[-3] in "nlrdzsj":
            palabra = palabra[:-2]       # lesiones, manuales, motores
        elif palabra.endswith("s"):
            palabra = palabra[:-1]       # cortes, cascos, herramientas
    return palabra[:6]


def _a_minutos(horas, minutos, pedagogica=False):
    return int(round(horas * (45 if pedagogica else 60) + minutos))


def extraer_minutos(texto):
    """Convierte '1 hora 13 minutos', '2 horas', '45 minutos' o '73' a minutos."""
    if texto is None or (not isinstance(texto, str) and pd.isna(texto)):
        return 0
    t = str(texto).lower().replace(",", ".").strip()
    if re.fullmatch(r"\d+(\.\d+)?", t):          # ya viene como número de minutos
        return int(float(t))
    h = re.search(r"(\d+(?:\.\d+)?)\s*(?:horas?|hrs?|h)\b", t)
    m = re.search(r"(\d+)\s*(?:minutos?|mins?|min)\b", t)
    return _a_minutos(float(h.group(1)) if h else 0, int(m.group(1)) if m else 0)


def extraer_minutos_exigidos(texto):
    """Interpreta la exigencia legal. Devuelve minutos, o None si no es cuantificable
    (ej: 'Según matriz de riesgo')."""
    if not texto or not str(texto).strip():
        return None
    t = str(texto).lower().replace(",", ".")
    pedag = "pedag" in t
    h = re.search(r"(\d+(?:\.\d+)?)\s*(?:horas?|hrs?|h)\b", t)
    m = re.search(r"(\d+)\s*(?:minutos?|mins?|min)\b", t)
    if not h and not m:
        return None
    return _a_minutos(float(h.group(1)) if h else 0, int(m.group(1)) if m else 0, pedag)


def formatear_duracion(minutos_totales):
    minutos_totales = int(minutos_totales)
    if minutos_totales <= 0:
        return "0 min"
    horas, minutos = divmod(minutos_totales, 60)
    if horas > 0:
        return f"{horas} h {minutos} min" if minutos > 0 else f"{horas} hora(s)"
    return f"{minutos} min"


def limpiar_columnas(df):
    """Quita espacios en encabezados y aplana saltos de línea (incluidos '\\n' literales)
    usando '; ' como separador para que Normativa y Competencias sigan legibles."""
    df.columns = [str(c).strip() for c in df.columns]

    def aplanar(valor):
        if pd.isna(valor):
            return ""
        lineas = re.split(r"\r\n|\r|\n|\\n", str(valor))
        return "; ".join(l.strip() for l in lineas if l.strip())

    for col in df.columns:
        if df[col].dtype == "object":
            df[col] = df[col].apply(aplanar)
    return df


# =============================================================================
# 2. VOCABULARIO: STOPWORDS Y SINÓNIMOS
# =============================================================================
# Palabras que no aportan señal por sí solas (gramática + términos SST genéricos).
STOPWORDS = {
    "el","la","los","las","un","una","unos","unas","de","del","al","y","e","o","u","para","por","con","sin","sobre","entre","hacia","hasta","segun","tras","que","como","uso","frente","ante","donde",
    "manejo","operacion","manipulacion","realizar","aplicar","identificar","reconocer","comprender","entender","prevenir","evitar","conocer","proteger","mantener","asegurar","garantizar","promover","desarrollar","fomentar","implementar","utilizar",
    "procedimiento","procedimientos","prevencion","medida","medidas","sistema","sistemas","seguro","seguridad","riesgo","riesgos","salud","trabajo","empresa","empresas","programa","plan","norma","ley","decreto","capacitacion","curso","evaluacion","control","controles","identificacion","analisis","gestion","aplicacion","accion","acciones","condicion","condiciones","entorno","laboral","lesion","lesiones","accidente","accidentes","dano","danos","incidente","incidentes","trabajador","trabajadores","colaborador","colaboradores","empleador","empleadores","cumplimiento","legal","vigente","exigencia","requisito","requisitos","conocimiento","conocimientos","general","generales","especifico","especificos","tecnico","tecnicos",
    "suseso","dt","seremi","sgsst","iper","ist","achs","mutual","isl","minsal","protocolo","enfermedad","enfermedades","profesional","profesionales","higiene","industrial","matriz","ocupacional",
    "elemento","elementos","personal","equipo","equipos","individual","inspeccion","inspeccionar","mantenimiento","mantencion","cuidado","recambio","instruccion","teorico","practica","practico","basico","basica",
    "marco","normativo","competencias","descripcion","speaker","nombre","duracion",
}

# Grupos de términos equivalentes. Si la palabra clave dice "auditiva" y el curso dice
# "oídos", son lo mismo. AQUÍ ES DONDE MÁS CONVIENE AGREGAR VOCABULARIO con el tiempo.
# Una línea = un grupo, palabras sueltas separadas por espacio.
GRUPOS_SINONIMOS = [
    "auditivo auditiva oido oidos audicion hipoacusia ruido prexor sordera orejera orejeras tapon tapones",
    "ocular oculares ojo ojos vision vista visual lente lentes antiparra antiparras gafas anteojos",
    "respiratorio respiratoria respirador respiradores mascarilla mascarillas polvo aerosol gas gases vapor vapores",
    "silice planesi silicosis neumoconiosis polvo",
    "mano manos guante guantes dedo dedos",
    "pie pies calzado zapato zapatos bota botas botin",
    "cabeza casco cascos craneo",
    "altura arnes caida caidas",
    "mmc carga cargas levantamiento sobreesfuerzo",
    "tmert musculoesqueletico musculoesqueleticos ergonomia ergonomico postura postural biomecanico lumbalgia",
    "psicosocial psicosociales estres burnout acoso",
    "mental psicologico psicologica emocional",
    "evacuacion simulacro siniestro",
    "fuego incendio incendios extintor extintores combustion",
    "auxilio auxilios rcp reanimacion desmayo lipotimia",
    "conduccion conducir conductor conductores vehiculo vehiculos chofer defensivo",
    "radiacion ultravioleta uv solar fotoproteccion",
    "alcohol droga drogas",
    "comite comites cphs paritario paritarios",
    "atrapamiento atrapar aplastamiento cizallamiento movil moviles",
    "corte cortes cortante cortantes laceracion amputacion",
    "hipobaria hipoxia altitud soroche",
    "pausa pausas descanso micropausa estiramiento",
    "herramienta herramientas maquina maquinas", "fuego incendio incendios extintor extintores combustion",
    "auxilio auxilios rcp reanimacion desmayo lipotimia",
    "conduccion conducir conductor conductores vehiculo vehiculos chofer defensivo",
    "radiacion ultravioleta uv solar fotoproteccion",
    "alcohol droga drogas",
    "comite comites cphs paritario paritarios",
    "atrapamiento atrapar aplastamiento cizallamiento movil moviles",
    "corte cortes cortante cortantes laceracion amputacion",
    "hipobaria hipoxia altitud soroche",
    "pausa pausas descanso micropausa estiramiento",
    "herramienta herramientas maquina maquinas",
    "epp epi proteccion",
]

def _construir_sinonimos(grupos):
    mapa = {}
    for g in grupos:
        raices = {raiz(w) for w in normalizar_texto(g).split()}
        for r in raices:
            mapa.setdefault(r, set()).update(raices)
    return mapa


SINONIMOS = _construir_sinonimos(GRUPOS_SINONIMOS)


# =============================================================================
# 3. MOTOR DE SCORING
# =============================================================================
def indexar_catalogo(df):
    """Pre-calcula las raíces de cada campo de cada curso (se hace una sola vez)."""
    cursos = []
    for _, row in df.iterrows():
        campos, todo = {}, set()
        for campo in CAMPOS_PESO:
            if campo in df.columns:
                lista = [raiz(w) for w in normalizar_texto(row.get(campo, "")).split()]
                campos[campo] = (lista, set(lista))
                todo |= set(lista)
        cursos.append({"campos": campos, "todo": todo})
    return cursos


def preparar_frase(frase, cursos):
    """Prepara una palabra clave: raíces, tokens significativos con sinónimos y su IDF
    (una palabra que aparece en media el catálogo vale menos que una rara)."""
    norm = normalizar_texto(frase)
    palabras = norm.split()
    if not palabras:
        return None
    significativas = [w for w in palabras if w not in STOPWORDS and len(w) >= 2]
    # Una sola palabra genérica ("seguridad", "salud") no sirve como palabra clave.
    if len(palabras) == 1 and not significativas:
        return None

    alternativas = []
    for w in significativas:
        r = raiz(w)
        alternativas.append({r} | SINONIMOS.get(r, set()))

    n = len(cursos)
    idfs = []
    for alt in alternativas:
        df_ = sum(1 for c in cursos if alt & c["todo"])
        idfs.append(math.log((n + 1) / (df_ + 1)) + 1)

    return {
        "original": frase.strip(),
        "raices": [raiz(w) for w in palabras],
        "alternativas": alternativas,
        "idf": idfs,
    }


def _contiene_secuencia(lista, sub):
    n = len(sub)
    return n > 0 and any(lista[i:i + n] == sub for i in range(len(lista) - n + 1))


def fuerza_match(fp, lista, conjunto):
    if _contiene_secuencia(lista, fp["raices"]):
        return FUERZA_FRASE_EXACTA
    if not fp["alternativas"]:
        return 0.0
    presentes = [bool(alt & conjunto) for alt in fp["alternativas"]]
    if all(presentes):
        return FUERZA_TODAS_LAS_PALABRAS
    if len(fp["alternativas"]) >= 2:
        cobertura = sum(i for i, p in zip(fp["idf"], presentes) if p) / sum(fp["idf"])
        if cobertura >= COBERTURA_MIN_PARCIAL:
            return FUERZA_PARCIAL * cobertura
    return 0.0


def puntuar_curso(curso, frases_prep):
    """Devuelve (puntaje_total, mejor_puntaje_de_una_frase, detalle_de_coincidencias)."""
    total, mejor_frase, detalle = 0.0, 0.0, []
    for fp in frases_prep:
        mejor, campo_mejor = 0.0, ""
        for campo, (lista, conjunto) in curso["campos"].items():
            peso = CAMPOS_PESO[campo]
            if peso * FUERZA_FRASE_EXACTA <= mejor:
                continue
            p = peso * fuerza_match(fp, lista, conjunto)
            if p > mejor:
                mejor, campo_mejor = p, campo
        if mejor > 0:
            total += mejor
            mejor_frase = max(mejor_frase, mejor)
            detalle.append((mejor, f"{fp['original']} ({campo_mejor})"))
    detalle.sort(key=lambda x: -x[0])
    return total, mejor_frase, "; ".join(d for _, d in detalle)


def separar_palabras_clave(texto):
    """Acepta comas, punto y coma o saltos de línea; ignora la etiqueta 'PALABRAS CLAVE...:'."""
    texto = re.sub(r"(?i)^\s*palabras\s+clave(\s+para\s+programa)?\s*:?\s*", "", texto.strip())
    frases = [f.strip(" .\t\"'•-") for f in re.split(r"[,;\n]+", texto)]
    return [f for f in frases if f]


def calcular_ranking(df, frases, tema, norma, umbral, exigir_norma):
    """Puntúa todo el catálogo y marca qué cursos entran a la ruta."""
    cursos = indexar_catalogo(df)
    consultas = list(frases)
    if tema and tema.strip():
        consultas.append(tema.strip())          # el Tema/Módulo también cuenta como búsqueda
    frases_prep = [fp for fp in (preparar_frase(f, cursos) for f in consultas) if fp]

    res = [puntuar_curso(c, frases_prep) for c in cursos]
    out = df.copy()
    out["Score"] = [round(r[0], 2) for r in res]
    out["_mejor_frase"] = [r[1] for r in res]
    out["Coincidencias"] = [r[2] for r in res]

    # Bonus de orden y filtro opcional por la norma seleccionada
    out["Cita la norma"] = False
    out["_bonus"] = 0.0
    if norma in PATRONES_NORMA:
        campo, pat_filtro, pat_bonus = PATRONES_NORMA[norma]
        if campo in out.columns:
            cita = out[campo].astype(str).str.lower().str.contains(pat_filtro, regex=True)
            bonus = out[campo].astype(str).str.lower().str.contains(pat_bonus, regex=True)
            out["Cita la norma"] = cita
            out.loc[bonus & (out["Score"] > 0), "_bonus"] = BONUS_NORMA

    out["_entra"] = (out["Score"] >= umbral) & (out["_mejor_frase"] >= PUNTAJE_MIN_UNA_FRASE)
    if exigir_norma and norma in PATRONES_NORMA:
        out["_entra"] &= out["Cita la norma"]
    # El bonus se suma DESPUÉS de decidir quién entra: solo afecta el orden.
    out["Score"] = out["Score"] + out["_bonus"]
    return out.sort_values("Score", ascending=False)


def evaluar_cumplimiento(total_min, exigida_txt, solo_oal):
    """Texto de la fila resumen: compara de verdad lo ofrecido contra lo exigido."""
    exigidos = extraer_minutos_exigidos(exigida_txt)
    if exigidos is None:
        texto = "Duración exigida no cuantificable: validar manualmente"
    elif total_min >= exigidos:
        texto = f"Cumple duración exigida (+{formatear_duracion(total_min - exigidos)})"
    else:
        texto = f"No cumple: faltan {formatear_duracion(exigidos - total_min)}"
    if solo_oal:
        texto += " | Exige OAL: los cursos Bukplay son complementarios"
    return texto


# =============================================================================
# 4. INTERFAZ STREAMLIT
# =============================================================================
st.set_page_config(page_title="Generador de Rutas SST - Bukplay", layout="wide")

st.title("Generador Rutas de Aprendizaje SST Bukplay CL")
st.markdown("Motor de **scoring ponderado con sinónimos SST**")

archivo_subido = st.file_uploader(
    "1. Sube el catálogo consolidado de SST (CSV o Excel)", type=["csv", "xlsx", "xls"]
)


def leer_catalogo(archivo):
    if archivo.name.lower().endswith(".csv"):
        for enc in ("utf-8-sig", "latin-1"):
            try:
                archivo.seek(0)
                return pd.read_csv(archivo, encoding=enc)
            except UnicodeDecodeError:
                continue
    return pd.read_excel(archivo)


if archivo_subido is not None:
    try:
        df_catalogo = limpiar_columnas(leer_catalogo(archivo_subido))
        st.success(f"✅ Catálogo cargado: {len(df_catalogo)} cursos.")

        faltantes = [c for c in ("Nombre", "Descripción") if c not in df_catalogo.columns]
        if faltantes:
            st.error(f"Al catálogo le faltan columnas obligatorias: {', '.join(faltantes)}")
            st.stop()
        st.markdown("---")

        col_norma, col_tema, col_horas = st.columns([1, 1.5, 1])
        with col_norma:
            opciones = list(PATRONES_NORMA.keys()) + ["Otra (Manual)"]
            seleccion_norma = st.selectbox("Norma / Perfil / Industria:", opciones)
            nombre_norma = (
                st.text_input("Escribe el nombre de la Norma:")
                if seleccion_norma == "Otra (Manual)" else seleccion_norma
            )
        with col_tema:
            nombre_tema = st.text_input("Tema / Módulo:", placeholder="Ej: Uso y Mantención de EPP")
        with col_horas:
            duracion_exigida = st.text_input(
                "Duración Exigida por Norma:", placeholder="Ej: 1 hora cronológica, 8 horas..."
            )
            exclusividad = st.selectbox(
                "Quién puede impartirlo:",
                ["Interno / terceros", "Exclusivo OAL (ACHS, Mutual, IST, ISL)"],
            )

        palabras_clave_input = st.text_area(
            "Pega las 'PALABRAS CLAVE PARA PROGRAMA' de NotebookLM:",
            placeholder="Ej: cascos, calzado de seguridad, protección auditiva, guantes...",
            height=90,
        )

        with st.expander("⚙️ Ajustes avanzados"):
            umbral = st.slider(
                "Puntaje mínimo para entrar a la ruta", 0.5, 6.0, UMBRAL_DEFECTO, 0.25,
                help="Más bajo = más cursos (más ruido). Más alto = solo coincidencias fuertes.",
            )
            exigir_norma = st.checkbox(
                "Mostrar solo cursos cuya columna 'Normativa' cite la norma seleccionada", value=False
            )

        if st.button("🚀 Generar Ruta de Aprendizaje"):
            frases = separar_palabras_clave(palabras_clave_input)
            if not frases:
                st.session_state.pop("resultado", None)
                st.warning("⚠️ Debes ingresar palabras clave.")
            else:
                ranking = calcular_ranking(
                    df_catalogo, frases, nombre_tema, nombre_norma, umbral, exigir_norma
                )
                st.session_state["resultado"] = {
                    "ranking": ranking,
                    "norma": nombre_norma or "Norma General",
                    "tema": nombre_tema or "Tema General",
                    "exigida": duracion_exigida or "No especificada",
                    "oal": exclusividad.startswith("Exclusivo"),
                    "n_frases": len(frases),
                }

        # --- Se muestra desde session_state para que NO desaparezca al descargar ---
        res = st.session_state.get("resultado")
        if res:
            ranking = res["ranking"]
            df_filtrado = ranking[ranking["_entra"]].copy()
            if "Nombre" in df_filtrado.columns:
                df_filtrado = df_filtrado.drop_duplicates(subset=["Nombre"])

            if df_filtrado.empty:
                st.warning(
                    "⚠️ No se encontraron cursos con ese puntaje. Revisa los 'casi' más abajo "
                    "o baja el puntaje mínimo en Ajustes avanzados."
                )
            else:
                col_dur = next((c for c in df_filtrado.columns if c.lower().startswith("duraci")), None)
                dur_num = (
                    df_filtrado[col_dur].apply(extraer_minutos)
                    if col_dur else pd.Series([0] * len(df_filtrado), index=df_filtrado.index)
                )
                total_minutos = int(dur_num.sum())

                # TABLA FINAL (6 COLUMNAS ESTRICTAS)
                df_tabla = pd.DataFrame({
                    "Nombre del curso": df_filtrado.get("Nombre", ""),
                    "Speaker": df_filtrado.get("Speaker", ""),
                    "Descripción": df_filtrado.get("Descripción", ""),
                    "Duración": dur_num.apply(formatear_duracion),
                    "Marco normativo": df_filtrado.get("Normativa", ""),
                    "Competencias": df_filtrado.get("Competencias", ""),
                }).reset_index(drop=True)

                estado = evaluar_cumplimiento(total_minutos, res["exigida"], res["oal"])
                fila_vacia = pd.DataFrame([[None] * 6], columns=df_tabla.columns)
                fila_resumen = pd.DataFrame([[
                    f"RUTA: {res['norma']} - {res['tema']}",
                    f"EXIGENCIA: {res['exigida']}",
                    f"Cursos Bukplay: {len(df_tabla)}",
                    f"TOTAL: {formatear_duracion(total_minutos)}",
                    estado,
                    None,
                ]], columns=df_tabla.columns)
                df_final = pd.concat([df_tabla, fila_vacia, fila_resumen], ignore_index=True)

                st.subheader(f"📋 Ruta Generada: {res['norma']} - {res['tema']}")
                c1, c2, c3 = st.columns(3)
                c1.metric("Cursos en la ruta", len(df_tabla))
                c2.metric("Duración Bukplay", formatear_duracion(total_minutos))
                c3.metric("Exigencia", res["exigida"])
                (st.success if estado.startswith("Cumple") else st.warning)(estado)

                # Vista de auditoría en pantalla (NO se exporta)
                with st.expander("🔍 Por qué entró cada curso (auditoría)", expanded=True):
                    vista = df_filtrado[["Nombre", "Score", "Coincidencias"]].copy()
                    col_cuando = next((c for c in df_filtrado.columns if c.lower().startswith("cuándo")), None)
                    if col_cuando:
                        vista["Complemento presencial"] = (
                            df_filtrado[col_cuando].str.contains("presencial|advertencia", case=False, regex=True)
                            .map({True: "⚠️ Revisar ficha", False: ""})
                        )
                    st.dataframe(vista, use_container_width=True, hide_index=True)

                st.dataframe(df_final, use_container_width=True)

                nombre_clean = re.sub(r"[^\w\-]+", "_", f"{res['norma']}_{res['tema']}").strip("_")
                st.download_button(
                    label=f"📥 Descargar CSV ({nombre_clean}.csv)",
                    data=df_final.to_csv(index=False).encode("utf-8-sig"),  # BOM: Excel respeta tildes
                    file_name=f"{nombre_clean}.csv",
                    mime="text/csv",
                )

            # Cursos que NO entraron pero tuvieron alguna coincidencia
            casi = ranking[(~ranking["_entra"]) & (ranking["Score"] > 0)]
            if not casi.empty:
                with st.expander(f"🤏 Cursos descartados con alguna coincidencia ({len(casi)})"):
                    st.caption("Si alguno corresponde, agrega su palabra clave o baja el puntaje mínimo.")
                    st.dataframe(
                        casi[["Nombre", "Score", "Coincidencias"]],
                        use_container_width=True, hide_index=True,
                    )

    except Exception as e:
        st.error(f"Error interno del sistema: {e}")
