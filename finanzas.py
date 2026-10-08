from __future__ import annotations

import calendar
import csv
import io
import sqlite3
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st


DB_PATH = Path(__file__).resolve().parent / "finanzas.db"

GASTOS = [
    "Movilidad/transporte", "Comida", "Vivienda", "Ocio", "Salud",
    "Educación", "Deudas", "Servicios", "Otros",
]
INGRESOS = ["Salario", "Ventas", "Inversiones", "Freelance", "Otros"]
METODOS = [
    "Efectivo", "Tarjeta Débito", "Tarjeta de Crédito", "Addi",
    "Transferencias",
]
MESES = [
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
]


def conectar() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def inicializar_base() -> None:
    with conectar() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS perfiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL COLLATE NOCASE UNIQUE
            );

            CREATE TABLE IF NOT EXISTS movimientos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                perfil_id INTEGER NOT NULL REFERENCES perfiles(id) ON DELETE CASCADE,
                tipo TEXT NOT NULL CHECK(tipo IN ('Ingreso', 'Gasto')),
                descripcion TEXT NOT NULL,
                monto REAL NOT NULL CHECK(monto > 0),
                categoria TEXT NOT NULL,
                metodo_pago TEXT NOT NULL,
                fecha_hora TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS indice_movimientos_perfil_fecha
                ON movimientos(perfil_id, fecha_hora);

            CREATE TABLE IF NOT EXISTS presupuestos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                perfil_id INTEGER NOT NULL REFERENCES perfiles(id) ON DELETE CASCADE,
                mes TEXT NOT NULL,
                ingresos_proyectados REAL NOT NULL DEFAULT 0
                    CHECK(ingresos_proyectados >= 0),
                UNIQUE(perfil_id, mes)
            );

            CREATE TABLE IF NOT EXISTS obligaciones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                perfil_id INTEGER NOT NULL REFERENCES perfiles(id) ON DELETE CASCADE,
                nombre TEXT NOT NULL,
                monto REAL NOT NULL CHECK(monto > 0),
                dia_limite INTEGER NOT NULL CHECK(dia_limite BETWEEN 1 AND 31)
            );

            CREATE TABLE IF NOT EXISTS deudas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                perfil_id INTEGER NOT NULL REFERENCES perfiles(id) ON DELETE CASCADE,
                acreedor TEXT NOT NULL,
                deuda_total REAL NOT NULL CHECK(deuda_total > 0),
                saldo_pendiente REAL NOT NULL CHECK(saldo_pendiente >= 0),
                tasa_interes REAL NOT NULL DEFAULT 0 CHECK(tasa_interes >= 0),
                cuotas_totales INTEGER NOT NULL CHECK(cuotas_totales > 0),
                cuotas_pendientes INTEGER NOT NULL CHECK(cuotas_pendientes >= 0),
                cuota_mensual REAL NOT NULL CHECK(cuota_mensual > 0),
                dia_limite INTEGER NOT NULL CHECK(dia_limite BETWEEN 1 AND 31)
            );

            CREATE TABLE IF NOT EXISTS metas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                perfil_id INTEGER NOT NULL REFERENCES perfiles(id) ON DELETE CASCADE,
                nombre TEXT NOT NULL,
                monto_objetivo REAL NOT NULL CHECK(monto_objetivo > 0),
                monto_ahorrado REAL NOT NULL DEFAULT 0 CHECK(monto_ahorrado >= 0),
                fecha_objetivo TEXT
            );
            """
        )
        cantidad = db.execute("SELECT COUNT(*) FROM perfiles").fetchone()[0]
        if cantidad == 0:
            db.execute("INSERT INTO perfiles (nombre) VALUES (?)", ("Personal",))


def consultar(sql: str, parametros: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    with conectar() as db:
        return list(db.execute(sql, parametros).fetchall())


def ejecutar(sql: str, parametros: tuple[Any, ...] = ()) -> int:
    with conectar() as db:
        cursor = db.execute(sql, parametros)
        return int(cursor.lastrowid or 0)


def pesos(monto: float | int) -> str:
    return "$" + f"{float(monto):,.0f}".replace(",", ".")


def limites_mes(anio: int, mes: int) -> tuple[str, str]:
    inicio = date(anio, mes, 1)
    anio_siguiente = anio + (mes == 12)
    mes_siguiente = 1 if mes == 12 else mes + 1
    siguiente = date(anio_siguiente, mes_siguiente, 1)
    return (
        datetime.combine(inicio, time.min).isoformat(timespec="minutes"),
        datetime.combine(siguiente, time.min).isoformat(timespec="minutes"),
    )


def movimientos_del_mes(
    perfil_id: int, inicio: str, fin: str
) -> list[sqlite3.Row]:
    return consultar(
        """
        SELECT * FROM movimientos
        WHERE perfil_id = ? AND fecha_hora >= ? AND fecha_hora < ?
        ORDER BY fecha_hora DESC
        """,
        (perfil_id, inicio, fin),
    )


def aplicar_tema(oscuro: bool) -> None:
    if oscuro:
        fondo, lateral, superficie = "#101820", "#17232e", "#1d2b37"
        texto, tenue, borde, acento = (
            "#e7edf2", "#aab8c4", "#30414f", "#58c4a7"
        )
    else:
        fondo, lateral, superficie = "#f5f8f7", "#eaf1ef", "#ffffff"
        texto, tenue, borde, acento = (
            "#18302b", "#60736d", "#d7e3df", "#16866b"
        )

    st.markdown(
        f"""
        <style>
        .stApp, [data-testid="stAppViewContainer"] {{
            background: {fondo};
            color: {texto};
        }}
        [data-testid="stSidebar"] > div:first-child {{
            background: {lateral};
            border-right: 1px solid {borde};
        }}
        [data-testid="stMetric"] {{
            background: {superficie};
            border: 1px solid {borde};
            padding: 1rem;
            border-radius: 0.8rem;
        }}
        [data-testid="stMetricValue"], [data-testid="stMarkdownContainer"],
        label, p, h1, h2, h3, h4 {{
            color: {texto};
        }}
        [data-testid="stCaptionContainer"] {{
            color: {tenue};
        }}
        div.stButton > button, div.stFormSubmitButton > button {{
            border-color: {acento};
        }}
        hr {{ border-color: {borde}; }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def elegir_perfil(perfiles: list[sqlite3.Row]) -> tuple[int, str]:
    with st.sidebar:
        st.title("Finanzas personales")
        nombres = [p["nombre"] for p in perfiles]
        ids = {p["nombre"]: p["id"] for p in perfiles}
        seleccionado = st.selectbox("Perfil", nombres)
        st.toggle("Modo oscuro", key="modo_oscuro", value=False)

        with st.expander("Crear perfil"):
            with st.form("formulario_perfil", clear_on_submit=True):
                nombre = st.text_input("Nombre del perfil", max_chars=50)
                guardar = st.form_submit_button("Crear perfil")
                if guardar:
                    nombre = nombre.strip()
                    if not nombre:
                        st.error("Escribe un nombre para el perfil.")
                    else:
                        try:
                            ejecutar(
                                "INSERT INTO perfiles (nombre) VALUES (?)",
                                (nombre,),
                            )
                            st.rerun()
                        except sqlite3.IntegrityError:
                            st.error("Ya existe un perfil con ese nombre.")

        st.divider()
        st.caption("Los datos se guardan en una base SQLite de este proyecto.")
        return int(ids[seleccionado]), seleccionado


def mostrar_inicio(perfil_id: int, perfil: str) -> None:
    hoy = date.today()
    inicio, fin = limites_mes(hoy.year, hoy.month)
    filas = movimientos_del_mes(perfil_id, inicio, fin)

    ingresos = sum(f["monto"] for f in filas if f["tipo"] == "Ingreso")
    gastos = sum(f["monto"] for f in filas if f["tipo"] == "Gasto")

    presupuesto = consultar(
        "SELECT ingresos_proyectados FROM presupuestos "
        "WHERE perfil_id = ? AND mes = ?",
        (perfil_id, hoy.strftime("%Y-%m")),
    )
    proyectado = presupuesto[0]["ingresos_proyectados"] if presupuesto else 0

    obligaciones = consultar(
        "SELECT COALESCE(SUM(monto), 0) AS total FROM obligaciones "
        "WHERE perfil_id = ?",
        (perfil_id,),
    )[0]["total"]

    st.title(f"Hola, {perfil}")
    st.caption(f"Resumen de {MESES[hoy.month - 1]} {hoy.year}")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Ingresos del mes", pesos(ingresos))
    c2.metric("Gastos del mes", pesos(gastos))
    c3.metric("Balance registrado", pesos(ingresos - gastos))
    c4.metric("Margen tras obligaciones", pesos(proyectado - obligaciones))

    st.subheader("Actividad reciente")
    recientes = consultar(
        "SELECT * FROM movimientos WHERE perfil_id = ? "
        "ORDER BY fecha_hora DESC LIMIT 6",
        (perfil_id,),
    )
    if recientes:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Fecha": datetime.fromisoformat(
                            r["fecha_hora"]
                        ).strftime("%d/%m/%Y %H:%M"),
                        "Descripción": r["descripcion"],
                        "Tipo": r["tipo"],
                        "Categoría": r["categoria"],
                        "Monto": pesos(r["monto"]),
                    }
                    for r in recientes
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("Registra tu primer ingreso o gasto para empezar.")

    st.caption(
        "El margen resta las obligaciones fijas del ingreso proyectado. "
        "El balance usa únicamente movimientos guardados."
    )


def mostrar_movimientos(perfil_id: int) -> None:
    st.title("Movimientos")
    st.caption("Registra ingresos y gastos con fecha, hora y método de pago.")

    with st.form("formulario_movimiento", clear_on_submit=True):
        tipo = st.selectbox("Tipo de movimiento", ["Gasto", "Ingreso"])
        descripcion = st.text_input("Nombre o descripción", max_chars=120)
        c1, c2 = st.columns(2)
        monto = c1.number_input(
            "Monto (COP)", min_value=0.0, step=1000.0, format="%.0f"
        )
        fecha = c2.date_input("Fecha", value=date.today())
        c3, c4 = st.columns(2)
        hora = c3.time_input(
            "Hora",
            value=datetime.now().time().replace(second=0, microsecond=0),
        )
        metodo = c4.selectbox("Método de pago", METODOS)
        opciones = GASTOS if tipo == "Gasto" else INGRESOS
        categoria = st.selectbox("Categoría", opciones)
        guardar = st.form_submit_button("Guardar movimiento", type="primary")

        if guardar:
            if not descripcion.strip():
                st.error("Escribe una descripción.")
            elif monto <= 0:
                st.error("El monto debe ser mayor que cero.")
            else:
                ejecutar(
                    """
                    INSERT INTO movimientos
                        (perfil_id, tipo, descripcion, monto, categoria,
                         metodo_pago, fecha_hora)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        perfil_id,
                        tipo,
                        descripcion.strip(),
                        monto,
                        categoria,
                        metodo,
                        datetime.combine(fecha, hora).isoformat(
                            timespec="minutes"
                        ),
                    ),
                )
                st.success("Movimiento guardado.")
                st.rerun()

    st.divider()
    st.subheader("Historial")
    hoy = date.today()
    c1, c2 = st.columns(2)
    anio = c1.selectbox(
        "Año",
        list(range(hoy.year, hoy.year - 8, -1)),
        key="anio_movimientos",
    )
    mes = c2.selectbox(
        "Mes",
        list(range(1, 13)),
        index=hoy.month - 1,
        format_func=lambda m: MESES[m - 1],
        key="mes_movimientos",
    )

    inicio, fin = limites_mes(anio, mes)
    filas = movimientos_del_mes(perfil_id, inicio, fin)

    if not filas:
        st.info("No hay movimientos en ese periodo.")
        return

    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Fecha y hora": datetime.fromisoformat(
                        r["fecha_hora"]
                    ).strftime("%d/%m/%Y %H:%M"),
                    "Tipo": r["tipo"],
                    "Descripción": r["descripcion"],
                    "Categoría": r["categoria"],
                    "Método": r["metodo_pago"],
                    "Monto": pesos(r["monto"]),
                }
                for r in filas
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )

    opciones_borrar = {
        f'{r["descripcion"]} · {pesos(r["monto"])} · '
        f'{datetime.fromisoformat(r["fecha_hora"]).strftime("%d/%m/%Y")}': r["id"]
        for r in filas
    }
    with st.expander("Eliminar un movimiento"):
        elegido = st.selectbox("Movimiento", list(opciones_borrar))
        if st.button("Eliminar movimiento"):
            ejecutar(
                "DELETE FROM movimientos WHERE id = ? AND perfil_id = ?",
                (opciones_borrar[elegido], perfil_id),
            )
            st.rerun()


def mostrar_presupuesto(perfil_id: int) -> None:
    st.title("Presupuesto mensual")
    hoy = date.today()
    c1, c2 = st.columns(2)
    anio = c1.selectbox(
        "Año",
        list(range(hoy.year, hoy.year - 8, -1)),
        key="anio_presupuesto",
    )
    mes = c2.selectbox(
        "Mes",
        list(range(1, 13)),
        index=hoy.month - 1,
        format_func=lambda m: MESES[m - 1],
        key="mes_presupuesto",
    )
    mes_clave = f"{anio:04d}-{mes:02d}"

    fila = consultar(
        "SELECT ingresos_proyectados FROM presupuestos "
        "WHERE perfil_id = ? AND mes = ?",
        (perfil_id, mes_clave),
    )
    proyectado_actual = float(fila[0]["ingresos_proyectados"]) if fila else 0.0

    st.subheader("Ingresos proyectados")
    with st.form("formulario_presupuesto"):
        proyectado = st.number_input(
            "Ingreso esperado del mes (COP)",
            min_value=0.0,
            value=proyectado_actual,
            step=10000.0,
            format="%.0f",
        )
        guardar = st.form_submit_button("Guardar proyección")
        if guardar:
            ejecutar(
                """
                INSERT INTO presupuestos (perfil_id, mes, ingresos_proyectados)
                VALUES (?, ?, ?)
                ON CONFLICT(perfil_id, mes)
                DO UPDATE SET ingresos_proyectados = excluded.ingresos_proyectados
                """,
                (perfil_id, mes_clave, proyectado),
            )
            st.rerun()

    st.subheader("Obligaciones fijas")
    with st.form("formulario_obligacion", clear_on_submit=True):
        nombre = st.text_input("Obligación, por ejemplo arriendo o servicios")
        monto = st.number_input(
            "Valor mensual (COP)", min_value=0.0, step=10000.0, format="%.0f"
        )
        dia = st.number_input("Día límite de pago", min_value=1, max_value=31, value=1)
        guardar = st.form_submit_button("Agregar obligación")
        if guardar:
            if not nombre.strip() or monto <= 0:
                st.error("Escribe el nombre y un valor mayor que cero.")
            else:
                ejecutar(
                    "INSERT INTO obligaciones "
                    "(perfil_id, nombre, monto, dia_limite) VALUES (?, ?, ?, ?)",
                    (perfil_id, nombre.strip(), monto, int(dia)),
                )
                st.rerun()

    obligaciones = consultar(
        "SELECT * FROM obligaciones WHERE perfil_id = ? ORDER BY dia_limite",
        (perfil_id,),
    )
    total = sum(o["monto"] for o in obligaciones)

    c1, c2, c3 = st.columns(3)
    c1.metric("Ingreso proyectado", pesos(proyectado_actual))
    c2.metric("Obligaciones mensuales", pesos(total))
    c3.metric("Margen planificado", pesos(proyectado_actual - total))

    if obligaciones:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Obligación": o["nombre"],
                        "Valor": pesos(o["monto"]),
                        "Fecha límite": f"Día {o['dia_limite']}",
                    }
                    for o in obligaciones
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
        nombres = {
            f"{o['nombre']} · {pesos(o['monto'])}": o["id"]
            for o in obligaciones
        }
        elegido = st.selectbox("Eliminar obligación", list(nombres))
        if st.button("Eliminar obligación"):
            ejecutar(
                "DELETE FROM obligaciones WHERE id = ? AND perfil_id = ?",
                (nombres[elegido], perfil_id),
            )
            st.rerun()
    else:
        st.info("Agrega pagos fijos para calcular el margen planificado.")

    st.caption(
        "El margen es el ingreso proyectado menos las obligaciones fijas. "
        "Los gastos registrados se muestran por separado para evitar duplicarlos."
    )


def fecha_vencimiento(dia: int) -> date:
    hoy = date.today()
    ultimo = calendar.monthrange(hoy.year, hoy.month)[1]
    vencimiento = date(hoy.year, hoy.month, min(dia, ultimo))
    if vencimiento >= hoy:
        return vencimiento
    anio = hoy.year + (hoy.month == 12)
    mes = 1 if hoy.month == 12 else hoy.month + 1
    ultimo = calendar.monthrange(anio, mes)[1]
    return date(anio, mes, min(dia, ultimo))


def mostrar_deudas(perfil_id: int) -> None:
    st.title("Deudas")

    with st.form("formulario_deuda", clear_on_submit=True):
        acreedor = st.text_input("Acreedor o banco")
        deuda_total = st.number_input(
            "Deuda total (COP)", min_value=0.0, step=10000.0, format="%.0f"
        )
        saldo = st.number_input(
            "Saldo pendiente actual (COP)", min_value=0.0, step=10000.0,
            format="%.0f"
        )
        tasa = st.number_input(
            "Tasa de interés anual (%)", min_value=0.0, step=0.1, format="%.2f"
        )
        c1, c2 = st.columns(2)
        cuotas_totales = c1.number_input(
            "Número total de cuotas", min_value=1, value=1, step=1
        )
        cuotas_pendientes = c2.number_input(
            "Cuotas faltantes", min_value=0, value=1, step=1
        )
        c3, c4 = st.columns(2)
        cuota = c3.number_input(
            "Valor mensual (COP)", min_value=0.0, step=10000.0, format="%.0f"
        )
        dia = c4.number_input(
            "Día límite de pago", min_value=1, max_value=31, value=1
        )
        guardar = st.form_submit_button("Guardar deuda", type="primary")

        if guardar:
            if not acreedor.strip():
                st.error("Escribe el nombre del acreedor o banco.")
            elif deuda_total <= 0 or cuota <= 0 or saldo > deuda_total:
                st.error("Revisa los valores de la deuda y la cuota mensual.")
            elif cuotas_pendientes > cuotas_totales:
                st.error("Las cuotas faltantes no pueden superar el total.")
            else:
                ejecutar(
                    """
                    INSERT INTO deudas
                        (perfil_id, acreedor, deuda_total, saldo_pendiente,
                         tasa_interes, cuotas_totales, cuotas_pendientes,
                         cuota_mensual, dia_limite)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        perfil_id, acreedor.strip(), deuda_total, saldo, tasa,
                        int(cuotas_totales), int(cuotas_pendientes), cuota, int(dia),
                    ),
                )
                st.rerun()

    deudas = consultar(
        "SELECT * FROM deudas WHERE perfil_id = ? ORDER BY dia_limite",
        (perfil_id,),
    )
    if not deudas:
        st.info("Todavía no has agregado deudas.")
        return

    st.subheader("Resumen de deudas")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Acreedor": d["acreedor"],
                    "Saldo pendiente": pesos(d["saldo_pendiente"]),
                    "Tasa anual": f"{d['tasa_interes']:.2f}%",
                    "Cuotas": f"{d['cuotas_pendientes']} de {d['cuotas_totales']}",
                    "Cuota mensual": pesos(d["cuota_mensual"]),
                    "Próximo vencimiento": fecha_vencimiento(
                        d["dia_limite"]
                    ).strftime("%d/%m/%Y"),
                }
                for d in deudas
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )

    activas = [d for d in deudas if d["saldo_pendiente"] > 0]
    if activas:
        opciones = {
            f'{d["acreedor"]} · saldo {pesos(d["saldo_pendiente"])}': d
            for d in activas
        }
        with st.form("formulario_pago_deuda"):
            etiqueta = st.selectbox("Deuda", list(opciones))
            deuda = opciones[etiqueta]
            pago = st.number_input(
                "Monto pagado (COP)",
                min_value=0.0,
                max_value=float(deuda["saldo_pendiente"]),
                step=10000.0,
                format="%.0f",
            )
            metodo = st.selectbox("Método de pago", METODOS)
            guardar = st.form_submit_button("Registrar pago")

            if guardar:
                if pago <= 0:
                    st.error("El pago debe ser mayor que cero.")
                else:
                    nuevo_saldo = max(0.0, deuda["saldo_pendiente"] - pago)
                    cuotas_pagadas = int(pago // deuda["cuota_mensual"])
                    faltantes = (
                        0 if nuevo_saldo == 0
                        else max(0, deuda["cuotas_pendientes"] - cuotas_pagadas)
                    )
                    with conectar() as db:
                        db.execute(
                            "UPDATE deudas SET saldo_pendiente = ?, "
                            "cuotas_pendientes = ? WHERE id = ?",
                            (nuevo_saldo, faltantes, deuda["id"]),
                        )
                        db.execute(
                            """
                            INSERT INTO movimientos
                                (perfil_id, tipo, descripcion, monto, categoria,
                                 metodo_pago, fecha_hora)
                            VALUES (?, 'Gasto', ?, ?, 'Deudas', ?, ?)
                            """,
                            (
                                perfil_id,
                                f"Abono a deuda: {deuda['acreedor']}",
                                pago,
                                metodo,
                                datetime.now().isoformat(timespec="minutes"),
                            ),
                        )
                    st.rerun()


def mostrar_metas(perfil_id: int) -> None:
    st.title("Metas de ahorro")

    with st.form("formulario_meta", clear_on_submit=True):
        nombre = st.text_input("Nombre de la meta")
        objetivo = st.number_input(
            "Monto objetivo (COP)", min_value=0.0, step=10000.0, format="%.0f"
        )
        ahorrado = st.number_input(
            "Ahorro actual (COP)", min_value=0.0, step=10000.0, format="%.0f"
        )
        fecha = st.date_input("Fecha objetivo (opcional)", value=None)
        guardar = st.form_submit_button("Crear meta")

        if guardar:
            if not nombre.strip() or objetivo <= 0 or ahorrado > objetivo:
                st.error("Revisa el nombre y los montos de la meta.")
            else:
                ejecutar(
                    """
                    INSERT INTO metas
                        (perfil_id, nombre, monto_objetivo, monto_ahorrado,
                         fecha_objetivo)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        perfil_id, nombre.strip(), objetivo, ahorrado,
                        fecha.isoformat() if fecha else None,
                    ),
                )
                st.rerun()

    metas = consultar(
        "SELECT * FROM metas WHERE perfil_id = ? ORDER BY nombre",
        (perfil_id,),
    )
    if not metas:
        st.info("Crea una meta para empezar a seguir tu ahorro.")
        return

    for meta in metas:
        objetivo = float(meta["monto_objetivo"])
        ahorrado = float(meta["monto_ahorrado"])
        progreso = min(ahorrado / objetivo, 1.0)

        st.subheader(meta["nombre"])
        st.progress(
            progreso,
            text=f"{progreso:.0%} · {pesos(ahorrado)} de {pesos(objetivo)}",
        )
        if meta["fecha_objetivo"]:
            st.caption(
                "Fecha objetivo: "
                + date.fromisoformat(meta["fecha_objetivo"]).strftime("%d/%m/%Y")
            )

        with st.form(f"aporte_meta_{meta['id']}"):
            aporte = st.number_input(
                "Agregar ahorro (COP)",
                min_value=0.0,
                max_value=max(0.0, objetivo - ahorrado),
                step=10000.0,
                format="%.0f",
                key=f"aporte_{meta['id']}",
            )
            col_1, col_2 = st.columns(2)
            sumar = col_1.form_submit_button("Sumar ahorro")
            eliminar = col_2.form_submit_button("Eliminar meta")

            if sumar and aporte > 0:
                ejecutar(
                    "UPDATE metas SET monto_ahorrado = monto_ahorrado + ? "
                    "WHERE id = ? AND perfil_id = ?",
                    (aporte, meta["id"], perfil_id),
                )
                st.rerun()
            
            if eliminar:
                ejecutar(
                    "DELETE FROM metas WHERE id = ? AND perfil_id = ?",
                    (meta["id"], perfil_id),
                )
                st.rerun()


def exportar_csv() -> bytes:
    nombres = {
        p["id"]: p["nombre"]
        for p in consultar("SELECT id, nombre FROM perfiles")
    }

    tablas = {
        "movimiento": "SELECT * FROM movimientos",
        "presupuesto": "SELECT * FROM presupuestos",
        "obligacion": "SELECT * FROM obligaciones",
        "deuda": "SELECT * FROM deudas",
        "meta": "SELECT * FROM metas",
    }

    filas: list[dict[str, Any]] = []

    for tipo, sql in tablas.items():
        for row in consultar(sql):
            registro = dict(row)
            perfil_id = registro.get("perfil_id")
            filas.append(
                {
                    "tipo_registro": tipo,
                    "perfil": nombres.get(perfil_id, ""),
                    **registro,
                }
            )

    for perfil_id, nombre in nombres.items():
        filas.append(
            {
                "tipo_registro": "perfil",
                "perfil": nombre,
                "id": perfil_id,
                "nombre": nombre,
            }
        )

    if not filas:
        filas = [{"tipo_registro": "sin_datos", "perfil": ""}]

    traducciones = {
        "perfil_id": "id_perfil",
        "tipo": "tipo_movimiento",
        "descripcion": "descripcion",
        "monto": "monto",
        "categoria": "categoria",
        "metodo_pago": "metodo_pago",
        "fecha_hora": "fecha_hora",
        "mes": "mes",
        "ingresos_proyectados": "ingresos_proyectados",
        "dia_limite": "dia_limite",
        "acreedor": "acreedor",
        "deuda_total": "deuda_total",
        "saldo_pendiente": "saldo_pendiente",
        "tasa_interes": "tasa_interes",
        "cuotas_totales": "cuotas_totales",
        "cuotas_pendientes": "cuotas_pendientes",
        "cuota_mensual": "cuota_mensual",
        "monto_objetivo": "monto_objetivo",
        "monto_ahorrado": "monto_ahorrado",
        "fecha_objetivo": "fecha_objetivo",
    }

    tabla = pd.DataFrame(filas).fillna("")
    tabla = tabla.rename(columns=traducciones)
    buffer = io.StringIO()
    tabla.to_csv(buffer, index=False, quoting=csv.QUOTE_MINIMAL)

    return buffer.getvalue().encode("utf-8-sig")


def mostrar_reportes(perfil_id: int) -> None:
    st.title("Reportes")
    hoy = date.today()

    anio = st.selectbox(
        "Año de análisis",
        list(range(hoy.year, hoy.year - 8, -1)),
        key="anio_reportes",
    )
    mes = st.selectbox(
        "Mes para el desglose",
        list(range(1, 13)),
        index=hoy.month - 1,
        format_func=lambda m: MESES[m - 1],
        key="mes_reportes",
    )

    filas_anio = consultar(
        """
        SELECT * FROM movimientos
        WHERE perfil_id = ? AND fecha_hora >= ? AND fecha_hora < ?
        ORDER BY fecha_hora
        """,
        (perfil_id, f"{anio}-01-01T00:00", f"{anio + 1}-01-01T00:00"),
    )

    inicio, fin = limites_mes(anio, mes)
    filas_mes = movimientos_del_mes(perfil_id, inicio, fin)
    gastos = [r for r in filas_mes if r["tipo"] == "Gasto"]

    st.subheader(f"Desglose de {MESES[mes - 1]} {anio}")
    if gastos:
        datos = pd.DataFrame([dict(r) for r in gastos])
        categorias = (
            datos.groupby("categoria", as_index=False)["monto"]
            .sum()
            .rename(columns={"categoria": "Categoría", "monto": "Monto"})
        )
        metodos = (
            datos.groupby("metodo_pago", as_index=False)["monto"]
            .sum()
            .rename(columns={"metodo_pago": "Método de pago", "monto": "Monto"})
        )

        c1, c2 = st.columns(2)
        c1.plotly_chart(
            px.pie(
                categorias,
                names="Categoría",
                values="Monto",
                hole=0.42,
                title="Gastos por categoría",
            ),
            use_container_width=True,
        )
        c2.plotly_chart(
            px.bar(
                metodos,
                x="Método de pago",
                y="Monto",
                color="Método de pago",
                title="Gastos por método de pago",
            ),
            use_container_width=True,
        )
    else:
        st.info("No hay gastos registrados para ese mes.")

    st.subheader(f"Resumen mensual de {anio}")
    if filas_anio:
        datos = pd.DataFrame([dict(r) for r in filas_anio])
        datos["mes_numero"] = pd.to_datetime(datos["fecha_hora"]).dt.month
        resumen = datos.pivot_table(
            index="mes_numero",
            columns="tipo",
            values="monto",
            aggfunc="sum",
            fill_value=0,
        ).reindex(range(1, 13), fill_value=0)

        resumen = resumen.rename(
            columns={"Ingreso": "Ingresos", "Gasto": "Gastos"}
        )
        resumen.index = [MESES[i - 1] for i in resumen.index]
        datos_grafica = resumen.reset_index(names="Mes").melt(
            id_vars="Mes",
            var_name="Tipo",
            value_name="Monto",
        )

        st.plotly_chart(
            px.bar(
                datos_grafica,
                x="Mes",
                y="Monto",
                color="Tipo",
                barmode="group",
                title="Ingresos y gastos por mes",
            ),
            use_container_width=True,
        )
    else:
        st.info("No hay movimientos registrados para ese año.")

    st.divider()
    st.subheader("Exportar información")
    st.caption(
        "El archivo incluye todos los perfiles, movimientos, presupuestos, "
        "obligaciones, deudas y metas."
    )
    st.download_button(
        "Descargar todos los datos en CSV",
        data=exportar_csv(),
        file_name=f"finanzas_personales_{date.today().isoformat()}.csv",
        mime="text/csv",
        type="primary",
    )


def main() -> None:
    st.set_page_config(
        page_title="Finanzas personales",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    inicializar_base()
    perfiles = consultar("SELECT id, nombre FROM perfiles ORDER BY nombre")
    perfil_id, perfil = elegir_perfil(perfiles)
    aplicar_tema(bool(st.session_state.get("modo_oscuro", False)))

    pagina = st.sidebar.radio(
        "Secciones",
        [
            "Inicio",
            "Movimientos",
            "Presupuesto",
            "Deudas",
            "Metas de ahorro",
            "Reportes",
        ],
        label_visibility="collapsed",
    )

    if pagina == "Inicio":
        mostrar_inicio(perfil_id, perfil)
    elif pagina == "Movimientos":
        mostrar_movimientos(perfil_id)
    elif pagina == "Presupuesto":
        mostrar_presupuesto(perfil_id)
    elif pagina == "Deudas":
        mostrar_deudas(perfil_id)
    elif pagina == "Metas de ahorro":
        mostrar_metas(perfil_id)
    else:
        mostrar_reportes(perfil_id)


if __name__ == "__main__":
    main()
