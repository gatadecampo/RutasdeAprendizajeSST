import streamlit as st
import pandas as pd
import math
import re
import unicodedata

# =============================================================================
# 0. CONFIGURACIÓN DEL MOTOR
# =============================================================================
CAMPOS_PESO = {
    "Nombre": 3.0,
    "Descripción": 2.0,
    "Normativa": 1.0,
    "Subcategoría": 0.5,
    "Público objetivo": 0.5,
}

FUERZA_FRASE_EXACTA = 1.00
FUERZA_TODAS_LAS_PALABRAS = 0.85
FUERZA_PARCIAL = 0.40
COBERTURA_MIN_PARCIAL = 0.60

UMBRAL_DEFECTO = 2.5
PUNTAJE_MIN_UNA_FRASE = 1.7

# -----------------------------------------------------------------------------
# NORMAS / PERFILES / INDUSTRIAS
#   tipo "norma"     -> las palabras clave son obligatorias; la norma solo da
#                       bonus de orden (o filtra si se activa en Ajustes avanzados).
#   tipo "perfil" /
#   tipo "industria" -> las palabras clave son OPCIONALES. Un curso entra si el
#                       texto de las columnas indicadas calza con el patrón.
# Los patrones se aplican sobre texto NORMALIZADO (minúsculas, sin tildes, sin
# signos), por eso "N°44" se busca como "n 44" y "Comités" como "comites".
# Para agregar un perfil/industria nuevo basta con sumar una línea aquí.
# -----------------------------------------------------------------------------
NORMAS = {
    "Decreto 44": {
        "tipo": "norma",
        "campos": {"Normativa": r"\bn\s*44\b"},
    },
    "Salud mental y Riesgo Psicosocial": {
        "tipo": "norma",
        "campos": {
            "Normativa": r"psicosocial|salud mental|karin|21\s?643|istas",
            "Subcategoría": r"psicosocial|salud mental",
        },
    },
    "Protocolos del MINSAL (Ministerio de Salud)": {
        "tipo": "norma",
        "campos": {"Normativa": r"protocolo"},
    },
    "Ley 16.744": {
        "tipo": "norma",
        "campos": {"Normativa": r"16\s?744"},
    },
    "Decreto 594": {
        "tipo": "norma",
        "campos": {"Normativa": r"\bn\s*594\b"},
    },
    "Perfil: supervisores": {
        "tipo": "perfil",
        "campos": {"Público objetivo": r"supervisor|capataz|jefatura|linea de mando"},
    },
    "Perfil: prevencionistas": {
        "tipo": "perfil",
        "campos": {"Público objetivo": r"prevencionista|encargad[oa]s? de (la )?prevencion"},
    },
    "Perfil: Comités Paritarios de Higiene y Seguridad o CPHS": {
        "tipo": "perfil",
        "campos": {"Público objetivo": r"cphs|comites? paritari|comites? de higiene"},
    },
    "Perfil: empleadores": {
        "tipo": "perfil",
        "campos": {"Público objetivo": r"empleador|directivo|gerenc|gerente|alta direccion"},
    },
    "Industria: Construcción": {
        "tipo": "industria",
        "campos": {"Industria": r"construcc", "Público objetivo": r"construcc"},
    },
    "Industria: Minería": {
        "tipo": "industria",
        "campos": {"Industria": r"miner", "Público objetivo": r"miner"},
    },
    "Industria: Inmobiliaria": {
        "tipo": "industria",
        "campos": {"Industria": r"inmobiliari", "Público objetivo": r"inmobiliari"},
    },
    "Industria: Agrícola": {
        "tipo": "industria",
        "campos": {"Industria": r"agricol", "Público objetivo": r"agricol|agro"},
    },
    "Industria: Manufacturera": {
        "tipo": "industria",
        "campos": {"Industria": r"manufactur", "Público objetivo": r"manufactur"},
    },
    "Industria: Logística": {
        "tipo": "industria",
        "campos": {"Industria": r"logistic", "Público objetivo": r"logistic"},
    },
    "Industria: Salud": {
        "tipo": "industria",
        "campos": {
            "Industria": r"\bsalud\b",
            "Público objetivo": r"personal de (la )?salud(?! ocupacional)|\btens\b|cuidadores|pacientes|enfermer|clinic|hospital",
        },
    },
}
# "Transversal a todas las industrias" NO cuenta como match de una industria específica:
# si contara, cada industria devolvería casi todo el catálogo.

BONUS_NORMA = 0.75     # norma: solo ordena, nunca hace entrar un curso
PUNTAJE_PERFIL = 3.0   # perfil/industria: sube el curso en el orden de la lista

# =============================================================================
# 1. LIMPIEZA Y TRANSFORMACIÓN
# =============================================================================
def normalizar_texto(texto):
    if texto is None or (not isinstance(texto, str) and pd.isna(texto)):
        return ""
    texto = str(texto).lower().replace("\\n", " ")
    texto = "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")
    texto = re.sub(r"[^a-z0-9\s]", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def raiz(palabra):
    if len(palabra) > 3:
        if palabra.endswith("es") and palabra[-3] in "nlrdzsj":
            palabra = palabra[:-2]
        elif palabra.endswith("s"):
            palabra = palabra[:-1]
    return palabra[:6]


def _a_minutos(horas, minutos, pedagogica=False):
    return int(round(horas * (45 if pedagogica else 60) + minutos))


def extraer_minutos(texto):
    if texto is None or (not isinstance(texto, str) and pd.isna(texto)):
        return 0
    t = str(texto).lower().replace(",", ".").strip()
    if re.fullmatch(r"\d+(\.\d+)?", t):
        return int(float(t))
    h = re.search(r"(\d+(?:\.\d+)?)\s*(?:horas?|hrs?|h)\b", t)
    m = re.search(r"(\d+)\s*(?:minutos?|mins?|min)\b", t)
    return _a_minutos(float(h.group(1)) if h else 0, int(m.group(1)) if m else 0)


def extraer_minutos_exigidos(texto):
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
STOPWORDS = {
    "el","la","los","las","un","una","unos","unas","de","del","al","y","e","o","u","para","por","con","sin","sobre","entre","hacia","hasta","segun","tras","que","como","uso","frente","ante","donde",
    "manejo","operacion","manipulacion","realizar","aplicar","identificar","reconocer","comprender","entender","prevenir","evitar","conocer","proteger","mantener","asegurar","garantizar","promover","desarrollar","fomentar","implementar","utilizar",
    "procedimiento","procedimientos","prevencion","medida","medidas","sistema","sistemas","seguro","seguridad","riesgo","riesgos","salud","trabajo","empresa","empresas","programa","plan","norma","ley","decreto","capacitacion","curso","evaluacion","control","controles","identificacion","analisis","gestion","aplicacion","accion","acciones","condicion","condiciones","entorno","laboral","lesion","lesiones","accidente","accidentes","dano","danos","incidente","incidentes","trabajador","trabajadores","colaborador","colaboradores","empleador","empleadores","cumplimiento","legal","vigente","exigencia","requisito","requisitos","conocimiento","conocimientos","general","generales","especifico","especificos","tecnico","tecnicos",
    "suseso","dt","seremi","sgsst","iper","ist","achs","mutual","isl","minsal","protocolo","enfermedad","enfermedades","profesional","profesionales","higiene","industrial","matriz","ocupacional",
    "elemento","elementos","personal","equipo","equipos","individual","inspeccion","inspeccionar","mantenimiento","mantencion","cuidado","recambio","instruccion","teorico","practica","practico","basico","basica",
    "marco","normativo","competencias","descripcion","speaker","nombre","duracion",
}

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
    norm = normalizar_texto(frase)
    palabras = norm.split()
    if not palabras:
        return None
    significativas = [w for w in palabras if w not in STOPWORDS and len(w) >= 2]
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
    texto = re.sub(r"(?i)^\s*palabras\s+clave(\s+para\s+programa)?\s*:?\s*", "", texto.strip())
    frases = [f.strip(" .\t\"'•-") for f in re.split(r"[,;\n]+", texto)]
    return [f for f in frases if f]


def evaluar_norma(df, cfg):
    """Para cada curso, lista de columnas donde calzó el patrón de la norma/perfil/industria."""
    resultado = []
    for _, row in df.iterrows():
        aciertos = [
            campo for campo, patron in cfg["campos"].items()
            if campo in df.columns and re.search(patron, normalizar_texto(row[campo]))
        ]
        resultado.append(aciertos)
    return resultado


def es_perfil_o_industria(nombre):
    cfg = NORMAS.get(nombre)
    return bool(cfg) and cfg["tipo"] in ("perfil", "industria")


def calcular_ranking(df, frases, tema, norma, umbral, exigir_norma, modo="union"):
    cursos = indexar_catalogo(df)
    consultas = list(frases)
    if tema and tema.strip():
        consultas.append(tema.strip())
    frases_prep = [fp for fp in (preparar_frase(f, cursos) for f in consultas) if fp]

    res = [puntuar_curso(c, frases_prep) for c in cursos]
    out = df.copy()
    out["Score"] = [round(r[0], 2) for r in res]
    out["_mejor_frase"] = [r[1] for r in res]
    out["Coincidencias"] = [r[2] for r in res]

    cfg = NORMAS.get(norma)
    tipo = cfg["tipo"] if cfg else None
    aciertos = evaluar_norma(out, cfg) if cfg else [[] for _ in range(len(out))]
    out["Cita la norma"] = [bool(a) for a in aciertos]
    cita = out["Cita la norma"]

    entra_kw = (out["Score"] >= umbral) & (out["_mejor_frase"] >= PUNTAJE_MIN_UNA_FRASE)
    bonus = pd.Series(0.0, index=out.index)

    if tipo in ("perfil", "industria"):
        if not frases_prep:                     # palabras clave opcionales: manda el perfil
            entra = cita
        elif modo == "interseccion":
            entra = entra_kw & cita
        else:
            entra = entra_kw | cita
        bonus[cita] = PUNTAJE_PERFIL
        out["Coincidencias"] = [
            (f"{norma} [{', '.join(a)}]" + (f"; {k}" if k else "")) if a else k
            for a, k in zip(aciertos, out["Coincidencias"])
        ]
    elif tipo == "norma":
        entra = (entra_kw & cita) if exigir_norma else entra_kw
        bonus[cita & (out["Score"] > 0)] = BONUS_NORMA
    else:
        entra = entra_kw

    out["_entra"] = entra
    out["Score"] = out["Score"] + bonus
    return out.sort_values("Score", ascending=False).reset_index(drop=True)


def evaluar_cumplimiento(total_min, exigida_txt, solo_oal):
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
            opciones = list(NORMAS.keys()) + ["Otra (Manual)"]
            seleccion_norma = st.selectbox("Norma / Perfil / Industria:", opciones)
            nombre_norma = (
                st.text_input("Escribe el nombre de la Norma:")
                if seleccion_norma == "Otra (Manual)" else seleccion_norma
            )
        perfil_ind = es_perfil_o_industria(seleccion_norma)

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

        if perfil_ind:
            st.info(
                "👤 **Perfil / Industria:** el sistema busca en las columnas *Público objetivo* e "
                "*Industria* del catálogo. Las palabras clave son **opcionales**."
            )
        palabras_clave_input = st.text_area(
            "Palabras clave de NotebookLM (opcional en perfil / industria):" if perfil_ind
            else "Pega las 'PALABRAS CLAVE PARA PROGRAMA' de NotebookLM:",
            placeholder="Ej: cascos, calzado de seguridad, protección auditiva, guantes...",
            height=90,
        )

        exigir_norma = False
        modo_combinar = "union"
        with st.expander("⚙️ Ajustes avanzados"):
            umbral = st.slider(
                "Puntaje mínimo para entrar a la ruta (palabras clave)", 0.5, 6.0, UMBRAL_DEFECTO, 0.25,
                help="Más bajo = más cursos (más ruido). Más alto = solo coincidencias fuertes.",
            )
            if perfil_ind:
                modo_txt = st.selectbox(
                    "Si ingresas palabras clave junto con un perfil / industria:",
                    [
                        "Perfil/industria O palabras clave (unión)",
                        "Perfil/industria Y palabras clave (intersección)",
                    ],
                )
                modo_combinar = "interseccion" if "intersección" in modo_txt else "union"
            else:
                exigir_norma = st.checkbox(
                    "Mostrar solo cursos cuya columna 'Normativa' cite la norma seleccionada", value=False
                )

        if st.button("🚀 Generar Ruta de Aprendizaje"):
            frases = separar_palabras_clave(palabras_clave_input)
            if not frases and not perfil_ind:
                st.session_state.pop("resultado", None)
                st.session_state.pop("df_editor_input", None)
                st.warning("⚠️ Debes ingresar palabras clave.")
            else:
                ranking = calcular_ranking(
                    df_catalogo, frases, nombre_tema, nombre_norma, umbral, exigir_norma, modo_combinar
                )

                col_dur_rank = next((c for c in ranking.columns if c.lower().startswith("duraci")), None)
                dur_col_val = ranking[col_dur_rank] if col_dur_rank else ""

                # Tabla base para el editor interactivo
                df_editor_input = pd.DataFrame({
                    "Incluir": ranking["_entra"],
                    "Score": ranking["Score"],
                    "Nombre": ranking.get("Nombre", ""),
                    "Duración": dur_col_val,
                    "Normativa": ranking.get("Normativa", ""),
                    "Coincidencias": ranking["Coincidencias"],
                })

                st.session_state["df_editor_input"] = df_editor_input
                st.session_state["resultado"] = {
                    "ranking": ranking,
                    "norma": nombre_norma or "Norma General",
                    "tema": nombre_tema or "Tema General",
                    "exigida": duracion_exigida or "No especificada",
                    "oal": exclusividad.startswith("Exclusivo"),
                }

        # --- Renderizado con control manual ---
        if "resultado" in st.session_state and "df_editor_input" in st.session_state:
            res = st.session_state["resultado"]
            ranking = res["ranking"]

            st.subheader("🛠️ Selección y Ajuste de Cursos")
            st.caption(
                "Los cursos con ticket fueron seleccionados automáticamente por el algoritmo. "
                "Marca o desmarca casillas para agregar o quitar cursos de la ruta final:"
            )

            edited_df = st.data_editor(
                st.session_state["df_editor_input"],
                column_config={
                    "Incluir": st.column_config.CheckboxColumn("Incluir", help="Marca o desmarca para definir la ruta final"),
                    "Score": st.column_config.NumberColumn("Score", format="%.2f"),
                },
                disabled=["Score", "Nombre", "Duración", "Normativa", "Coincidencias"],
                use_container_width=True,
                hide_index=True,
                key="editor_interactivo_rutas",
            )

            # Extraer los cursos marcados actualmente
            indices_seleccionados = edited_df[edited_df["Incluir"]].index
            df_filtrado = ranking.loc[indices_seleccionados].copy()

            if "Nombre" in df_filtrado.columns:
                df_filtrado = df_filtrado.drop_duplicates(subset=["Nombre"])

            if df_filtrado.empty:
                st.warning("⚠️ No hay ningún curso seleccionado. Marca al menos una casilla en la tabla.")
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

                st.markdown("---")
                st.subheader(f"📋 Ruta Generada: {res['norma']} - {res['tema']}")
                c1, c2, c3 = st.columns(3)
                c1.metric("Cursos en la ruta", len(df_tabla))
                c2.metric("Duración Bukplay", formatear_duracion(total_minutos))
                c3.metric("Exigencia", res["exigida"])
                (st.success if estado.startswith("Cumple") else st.warning)(estado)

                st.dataframe(df_final, use_container_width=True)

                nombre_clean = re.sub(r"[^\w\-]+", "_", f"{res['norma']}_{res['tema']}").strip("_")
                st.download_button(
                    label=f"📥 Descargar CSV ({nombre_clean}.csv)",
                    data=df_final.to_csv(index=False).encode("utf-8-sig"),
                    file_name=f"{nombre_clean}.csv",
                    mime="text/csv",
                )

    except Exception as e:
        st.error(f"Error interno del sistema: {e}")