from __future__ import annotations

import calendar
import csv
import hashlib
import hmac
import io
import re
import secrets
import sqlite3
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st


APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "finanzas.db"
PASSWORD_HASH_ITERATIONS = 600_000
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,30}$")

EXPENSE_CATEGORIES = [
    "Movilidad/transporte",
    "Comida",
    "Vivienda",
    "Ocio",
    "Salud",
    "Educación",
    "Deudas",
    "Servicios",
    "Otros",
]

INCOME_CATEGORIES = ["Salario", "Ventas", "Inversiones", "Freelance", "Otros"]

PAYMENT_METHODS = [
    "Efectivo",
    "Tarjeta Débito",
    "Tarjeta de Crédito",
    "Addi",
    "Transferencias",
]

MONTHS = [
    "Enero",
    "Febrero",
    "Marzo",
    "Abril",
    "Mayo",
    "Junio",
    "Julio",
    "Agosto",
    "Septiembre",
    "Octubre",
    "Noviembre",
    "Diciembre",
]


def connect_db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_db() -> None:
    connection = connect_db()
    try:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                password_salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                password_iterations INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

        profiles_exist = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'profiles'"
        ).fetchone()

        if profiles_exist:
            profile_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(profiles)"
                ).fetchall()
            }

            if "user_id" not in profile_columns:
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.executescript(
                    """
                    BEGIN IMMEDIATE;
                    CREATE TABLE profiles_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                        name TEXT NOT NULL COLLATE NOCASE,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    INSERT INTO profiles_new (id, user_id, name, created_at)
                        SELECT id, NULL, name, created_at FROM profiles;
                    DROP TABLE profiles;
                    ALTER TABLE profiles_new RENAME TO profiles;
                    COMMIT;
                    """
                )
                connection.execute("PRAGMA foreign_keys = ON")
        else:
            connection.execute(
                """
                CREATE TABLE profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    name TEXT NOT NULL COLLATE NOCASE,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_profiles_user_name
            ON profiles(user_id, name COLLATE NOCASE)
            """
        )

        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                kind TEXT NOT NULL CHECK(kind IN ('Ingreso', 'Gasto')),
                description TEXT NOT NULL,
                amount REAL NOT NULL CHECK(amount > 0),
                category TEXT NOT NULL,
                payment_method TEXT NOT NULL,
                occurred_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_transactions_profile_date
                ON transactions(profile_id, occurred_at);

            CREATE TABLE IF NOT EXISTS budgets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                month TEXT NOT NULL,
                projected_income REAL NOT NULL DEFAULT 0
                    CHECK(projected_income >= 0),
                UNIQUE(profile_id, month)
            );

            CREATE TABLE IF NOT EXISTS obligations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                amount REAL NOT NULL CHECK(amount > 0),
                due_day INTEGER NOT NULL CHECK(due_day BETWEEN 1 AND 31)
            );

            CREATE TABLE IF NOT EXISTS debts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                creditor TEXT NOT NULL,
                total_amount REAL NOT NULL CHECK(total_amount > 0),
                remaining_balance REAL NOT NULL CHECK(remaining_balance >= 0),
                interest_rate REAL NOT NULL DEFAULT 0 CHECK(interest_rate >= 0),
                total_installments INTEGER NOT NULL CHECK(total_installments > 0),
                installments_left INTEGER NOT NULL CHECK(installments_left >= 0),
                monthly_payment REAL NOT NULL CHECK(monthly_payment > 0),
                due_day INTEGER NOT NULL CHECK(due_day BETWEEN 1 AND 31),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                target_amount REAL NOT NULL CHECK(target_amount > 0),
                saved_amount REAL NOT NULL DEFAULT 0 CHECK(saved_amount >= 0),
                deadline TEXT
            );
            """
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def fetch_all(
    sql: str, params: tuple[Any, ...] = ()
) -> list[sqlite3.Row]:
    with connect_db() as connection:
        return list(connection.execute(sql, params).fetchall())


def execute(sql: str, params: tuple[Any, ...] = ()) -> int:
    with connect_db() as connection:
        cursor = connection.execute(sql, params)
        return int(cursor.lastrowid or 0)


def hash_password(
    password: str, salt: bytes | None = None
) -> tuple[str, str]:
    salt = salt or secrets.token_bytes(16)
    password_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_HASH_ITERATIONS,
    )
    return salt.hex(), password_hash.hex()


def register_account(
    username: str, password: str
) -> tuple[int | None, str | None]:
    username = username.strip()

    if not USERNAME_PATTERN.fullmatch(username):
        return (
            None,
            "El usuario debe tener entre 3 y 30 caracteres: letras sin tilde, "
            "números, punto, guion o guion bajo.",
        )

    if len(password) < 10:
        return None, "La contraseña debe tener al menos 10 caracteres."

    salt, password_hash = hash_password(password)

    try:
        with connect_db() as connection:
            connection.execute("BEGIN IMMEDIATE")

            account_count = connection.execute(
                "SELECT COUNT(*) FROM users"
            ).fetchone()[0]

            cursor = connection.execute(
                """
                INSERT INTO users
                    (username, password_salt, password_hash, password_iterations)
                VALUES (?, ?, ?, ?)
                """,
                (
                    username,
                    salt,
                    password_hash,
                    PASSWORD_HASH_ITERATIONS,
                ),
            )

            user_id = int(cursor.lastrowid)

            if account_count == 0:
                # La primera cuenta recibe los perfiles de la versión anterior.
                connection.execute(
                    "UPDATE profiles SET user_id = ? WHERE user_id IS NULL",
                    (user_id,),
                )

            profile_count = connection.execute(
                "SELECT COUNT(*) FROM profiles WHERE user_id = ?",
                (user_id,),
            ).fetchone()[0]

            if profile_count == 0:
                connection.execute(
                    "INSERT INTO profiles (user_id, name) VALUES (?, ?)",
                    (user_id, "Personal"),
                )

    except sqlite3.IntegrityError:
        return None, "Ese nombre de usuario ya está ocupado. Elige otro."

    return user_id, None


def authenticate_user(
    username: str, password: str
) -> sqlite3.Row | None:
    rows = fetch_all(
        """
        SELECT id, username, password_salt, password_hash, password_iterations
        FROM users
        WHERE username = ? COLLATE NOCASE
        LIMIT 1
        """,
        (username.strip(),),
    )

    if not rows:
        # Mantiene un costo similar aunque el usuario no exista.
        hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes(16),
            PASSWORD_HASH_ITERATIONS,
        )
        return None

    account = rows[0]
    candidate_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(account["password_salt"]),
        int(account["password_iterations"]),
    )

    if hmac.compare_digest(candidate_hash.hex(), account["password_hash"]):
        return account

    return None


def show_auth_screen() -> None:
    st.title("Finanzas personales")
    st.write(
        "Inicia sesión o crea una cuenta. Cada cuenta tiene sus propios perfiles "
        "y su información financiera."
    )

    login_tab, register_tab = st.tabs(
        ["Iniciar sesión", "Crear cuenta"]
    )

    with login_tab:
        with st.form("login_form"):
            username = st.text_input("Usuario", max_chars=30)
            password = st.text_input("Contraseña", type="password")
            submitted = st.form_submit_button(
                "Iniciar sesión", type="primary"
            )

            if submitted:
                account = authenticate_user(username, password)

                if account:
                    st.session_state["user_id"] = int(account["id"])
                    st.session_state["username"] = account["username"]
                    st.rerun()
                else:
                    st.error("Usuario o contraseña incorrectos.")

    with register_tab:
        st.caption(
            "El usuario admite de 3 a 30 letras sin tilde, números, punto, "
            "guion o guion bajo. La contraseña debe tener al menos 10 caracteres."
        )

        st.info(
            "Si ya hay datos guardados de antes, la primera cuenta que se registre "
            "los recibirá. Crea tu cuenta antes de compartir el enlace."
        )

        with st.form("register_form"):
            new_username = st.text_input("Elige un usuario", max_chars=30)
            new_password = st.text_input(
                "Elige una contraseña", type="password"
            )
            password_confirmation = st.text_input(
                "Repite la contraseña", type="password"
            )
            submitted = st.form_submit_button(
                "Crear cuenta", type="primary"
            )

            if submitted:
                if new_password != password_confirmation:
                    st.error("Las contraseñas no coinciden.")
                else:
                    user_id, error = register_account(
                        new_username, new_password
                    )

                    if error:
                        st.error(error)
                    else:
                        st.session_state["user_id"] = user_id
                        st.session_state["username"] = new_username.strip()
                        st.rerun()

        st.caption(
            "Guarda tu contraseña en un lugar seguro; "
            "no hay recuperación por correo."
        )


def money(amount: float | int) -> str:
    return "$" + f"{float(amount):,.0f}".replace(",", ".")


def next_due_date(day_of_month: int) -> date:
    today = date.today()
    days_this_month = calendar.monthrange(today.year, today.month)[1]
    due_this_month = date(
        today.year,
        today.month,
        min(day_of_month, days_this_month),
    )

    if due_this_month >= today:
        return due_this_month

    year = today.year + (today.month == 12)
    month = 1 if today.month == 12 else today.month + 1
    days_next_month = calendar.monthrange(year, month)[1]

    return date(
        year,
        month,
        min(day_of_month, days_next_month),
    )


def apply_theme(dark: bool) -> None:
    if dark:
        background, sidebar, surface = "#101820", "#17232e", "#1d2b37"
        text, muted, border, accent = (
            "#e7edf2",
            "#aab8c4",
            "#30414f",
            "#58c4a7",
        )
    else:
        background, sidebar, surface = "#f5f8f7", "#eaf1ef", "#ffffff"
        text, muted, border, accent = (
            "#18302b",
            "#60736d",
            "#d7e3df",
            "#16866b",
        )

    st.markdown(
        f"""
        <style>
        .stApp, [data-testid="stAppViewContainer"] {{
            background: {background};
            color: {text};
        }}
        [data-testid="stSidebar"] > div:first-child {{
            background: {sidebar};
            border-right: 1px solid {border};
        }}
        [data-testid="stMetric"] {{
            background: {surface};
            border: 1px solid {border};
            padding: 1rem;
            border-radius: 0.8rem;
        }}
        [data-testid="stMetricValue"], [data-testid="stHeader"],
        [data-testid="stMarkdownContainer"], label, p, h1, h2, h3, h4 {{
            color: {text};
        }}
        [data-testid="stCaptionContainer"] {{
            color: {muted};
        }}
        div.stButton > button, div.stFormSubmitButton > button {{
            border-color: {accent};
        }}
        hr {{ border-color: {border}; }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def get_profile_rows(user_id: int) -> list[sqlite3.Row]:
    return fetch_all(
        """
        SELECT id, name FROM profiles
        WHERE user_id = ?
        ORDER BY name COLLATE NOCASE
        """,
        (user_id,),
    )


def add_transaction(
    profile_id: int,
    kind: str,
    description: str,
    amount: float,
    category: str,
    payment_method: str,
    occurred_at: datetime,
) -> None:
    execute(
        """
        INSERT INTO transactions
            (profile_id, kind, description, amount, category,
             payment_method, occurred_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            profile_id,
            kind,
            description.strip(),
            amount,
            category,
            payment_method,
            occurred_at.isoformat(timespec="minutes"),
        ),
    )


def transactions_for_period(
    profile_id: int, start: str, end: str
) -> list[sqlite3.Row]:
    return fetch_all(
        """
        SELECT * FROM transactions
        WHERE profile_id = ? AND occurred_at >= ? AND occurred_at < ?
        ORDER BY occurred_at DESC
        """,
        (profile_id, start, end),
    )


def budget_for_month(
    profile_id: int, month_key: str
) -> sqlite3.Row | None:
    rows = fetch_all(
        "SELECT * FROM budgets WHERE profile_id = ? AND month = ?",
        (profile_id, month_key),
    )
    return rows[0] if rows else None


def month_bounds(year: int, month: int) -> tuple[str, str]:
    first = date(year, month, 1)
    next_year = year + (month == 12)
    next_month = 1 if month == 12 else month + 1
    next_first = date(next_year, next_month, 1)

    return (
        datetime.combine(first, time.min).isoformat(timespec="minutes"),
        datetime.combine(next_first, time.min).isoformat(timespec="minutes"),
    )


def show_transactions(
    profile_id: int, rows: list[sqlite3.Row]
) -> None:
    if not rows:
        st.info("Todavía no hay movimientos para este periodo.")
        return

    table = pd.DataFrame(
        [
            {
                "Fecha y hora": datetime.fromisoformat(
                    row["occurred_at"]
                ).strftime("%d/%m/%Y %H:%M"),
                "Tipo": row["kind"],
                "Descripción": row["description"],
                "Categoría": row["category"],
                "Método": row["payment_method"],
                "Monto": money(row["amount"]),
                "id": row["id"],
            }
            for row in rows
        ]
    )

    st.dataframe(
        table.drop(columns=["id"]),
        use_container_width=True,
        hide_index=True,
    )

    delete_options = {
        f'{datetime.fromisoformat(row["occurred_at"]).strftime("%d/%m/%Y %H:%M")} · '
        f'{row["description"]} · {money(row["amount"])}': row["id"]
        for row in rows
    }

    with st.expander("Eliminar un movimiento"):
        selected_label = st.selectbox(
            "Movimiento",
            list(delete_options),
            key=f"delete_transaction_{profile_id}",
        )

        if st.button(
            "Eliminar movimiento",
            key=f"delete_transaction_button_{profile_id}",
            type="secondary",
        ):
            execute(
                "DELETE FROM transactions WHERE id = ? AND profile_id = ?",
                (delete_options[selected_label], profile_id),
            )
            st.success("Movimiento eliminado.")
            st.rerun()


def export_everything(user_id: int) -> bytes:
    profiles = {
        row["id"]: row["name"]
        for row in get_profile_rows(user_id)
    }
    profile_ids = list(profiles)

    if not profile_ids:
        records = [{"tipo_registro": "sin_datos", "perfil": ""}]
        frame = pd.DataFrame(records)
        buffer = io.StringIO()
        frame.to_csv(buffer, index=False, quoting=csv.QUOTE_MINIMAL)
        return buffer.getvalue().encode("utf-8-sig")

    placeholders = ", ".join("?" for _ in profile_ids)

    tables = {
        "movimientos": (
            f"SELECT * FROM transactions "
            f"WHERE profile_id IN ({placeholders}) ORDER BY occurred_at"
        ),
        "presupuestos": (
            f"SELECT * FROM budgets "
            f"WHERE profile_id IN ({placeholders}) ORDER BY month"
        ),
        "obligaciones": (
            f"SELECT * FROM obligations "
            f"WHERE profile_id IN ({placeholders}) ORDER BY due_day"
        ),
        "deudas": (
            f"SELECT * FROM debts "
            f"WHERE profile_id IN ({placeholders}) ORDER BY creditor"
        ),
        "metas": (
            f"SELECT * FROM goals "
            f"WHERE profile_id IN ({placeholders}) ORDER BY name"
        ),
    }

    records: list[dict[str, Any]] = []

    for table_name, sql in tables.items():
        for row in fetch_all(sql, tuple(profile_ids)):
            record = dict(row)
            profile_id = record.get("profile_id")
            records.append(
                {
                    "tipo_registro": table_name,
                    "perfil": profiles.get(profile_id, ""),
                    **record,
                }
            )

    for profile_id, profile_name in profiles.items():
        records.append(
            {
                "tipo_registro": "perfil",
                "perfil": profile_name,
                "id": profile_id,
                "name": profile_name,
            }
        )

    if not records:
        records = [{"tipo_registro": "sin_datos", "perfil": ""}]

    frame = pd.DataFrame(records).fillna("")
    buffer = io.StringIO()
    frame.to_csv(buffer, index=False, quoting=csv.QUOTE_MINIMAL)
    return buffer.getvalue().encode("utf-8-sig")


def sidebar(
    profile_rows: list[sqlite3.Row],
    user_id: int,
    username: str,
) -> tuple[int, str]:
    with st.sidebar:
        st.title("Finanzas")
        st.caption("Tu espacio para organizar el dinero.")
        st.caption(f"Sesión: {username}")

        if st.button("Cerrar sesión", use_container_width=True):
            st.session_state.pop("user_id", None)
            st.session_state.pop("username", None)
            st.rerun()

        profile_names = [row["name"] for row in profile_rows]
        profile_ids = {row["name"]: row["id"] for row in profile_rows}

        selected_name = st.selectbox(
            "Perfil",
            profile_names,
            key=f"profile_select_{user_id}",
        )

        st.session_state.setdefault("dark_mode", False)
        st.toggle("Modo oscuro", key="dark_mode")

        with st.expander("Crear perfil"):
            with st.form("create_profile_form", clear_on_submit=True):
                new_profile = st.text_input(
                    "Nombre del perfil",
                    max_chars=50,
                )
                submitted = st.form_submit_button("Crear perfil")

                if submitted:
                    cleaned = new_profile.strip()

                    if not cleaned:
                        st.error("Escribe un nombre para el perfil.")
                    else:
                        try:
                            execute(
                                "INSERT INTO profiles (user_id, name) "
                                "VALUES (?, ?)",
                                (user_id, cleaned),
                            )
                            st.success("Perfil creado.")
                            st.rerun()
                        except sqlite3.IntegrityError:
                            st.error("Ya tienes un perfil con ese nombre.")

        st.divider()
        st.caption("Tus perfiles y datos son privados de esta cuenta.")

        return int(profile_ids[selected_name]), selected_name


def show_overview(profile_id: int, profile_name: str) -> None:
    today = date.today()
    start, end = month_bounds(today.year, today.month)
    rows = transactions_for_period(profile_id, start, end)

    income = sum(
        row["amount"] for row in rows if row["kind"] == "Ingreso"
    )
    expenses = sum(
        row["amount"] for row in rows if row["kind"] == "Gasto"
    )

    month_key = today.strftime("%Y-%m")
    budget = budget_for_month(profile_id, month_key)

    obligations = fetch_all(
        "SELECT COALESCE(SUM(amount), 0) AS total "
        "FROM obligations WHERE profile_id = ?",
        (profile_id,),
    )[0]["total"]

    projected_income = budget["projected_income"] if budget else 0
    planned_margin = projected_income - obligations

    st.title(f"Hola, {profile_name}")
    st.caption(f"Resumen de {MONTHS[today.month - 1]} {today.year}")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Ingresos del mes", money(income))
    c2.metric("Gastos del mes", money(expenses))
    c3.metric("Balance registrado", money(income - expenses))
    c4.metric("Margen tras obligaciones", money(planned_margin))

    st.subheader("Actividad reciente")
    recent = fetch_all(
        """
        SELECT * FROM transactions
        WHERE profile_id = ?
        ORDER BY occurred_at DESC LIMIT 6
        """,
        (profile_id,),
    )

    if recent:
        frame = pd.DataFrame(
            [
                {
                    "Fecha": datetime.fromisoformat(
                        row["occurred_at"]
                    ).strftime("%d/%m/%Y"),
                    "Descripción": row["description"],
                    "Tipo": row["kind"],
                    "Categoría": row["category"],
                    "Monto": money(row["amount"]),
                }
                for row in recent
            ]
        )
        st.dataframe(frame, use_container_width=True, hide_index=True)
    else:
        st.info(
            "Registra tu primer ingreso o gasto para empezar a ver "
            "tu actividad aquí."
        )

    st.caption(
        "El margen resta las obligaciones fijas del ingreso proyectado. "
        "El balance registrado usa únicamente movimientos guardados."
    )


def show_movements(profile_id: int) -> None:
    st.title("Movimientos")
    st.caption(
        "Registra ingresos y gastos con su fecha, hora y método de pago."
    )

    with st.form(
        f"movement_form_{profile_id}",
        clear_on_submit=True,
    ):
        kind = st.selectbox(
            "Tipo de movimiento",
            ["Gasto", "Ingreso"],
        )
        description = st.text_input(
            "Nombre o descripción",
            max_chars=120,
        )

        first, second = st.columns(2)

        amount = first.number_input(
            "Monto (COP)",
            min_value=0.0,
            step=1000.0,
            format="%.0f",
        )
        occurred_date = second.date_input(
            "Fecha",
            value=date.today(),
        )

        third, fourth = st.columns(2)

        occurred_time = third.time_input(
            "Hora",
            value=datetime.now().time().replace(
                second=0,
                microsecond=0,
            ),
        )
        payment_method = fourth.selectbox(
            "Método de pago",
            PAYMENT_METHODS,
        )

        category_options = (
            EXPENSE_CATEGORIES
            if kind == "Gasto"
            else INCOME_CATEGORIES
        )
        category = st.selectbox(
            "Categoría",
            category_options,
        )

        submitted = st.form_submit_button(
            "Guardar movimiento",
            type="primary",
        )

        if submitted:
            if not description.strip():
                st.error("Escribe una descripción.")
            elif amount <= 0:
                st.error("El monto debe ser mayor que cero.")
            else:
                add_transaction(
                    profile_id,
                    kind,
                    description,
                    amount,
                    category,
                    payment_method,
                    datetime.combine(occurred_date, occurred_time),
                )
                st.success("Movimiento guardado.")
                st.rerun()

    st.divider()
    st.subheader("Historial")

    current_year = date.today().year
    years = list(range(current_year, current_year - 8, -1))

    f1, f2 = st.columns(2)

    selected_year = f1.selectbox(
        "Año",
        years,
        key=f"movement_year_{profile_id}",
    )
    selected_month = f2.selectbox(
        "Mes",
        list(range(1, 13)),
        index=date.today().month - 1,
        format_func=lambda month: MONTHS[month - 1],
        key=f"movement_month_{profile_id}",
    )

    start, end = month_bounds(selected_year, selected_month)
    show_transactions(
        profile_id,
        transactions_for_period(profile_id, start, end),
    )


def show_budget(profile_id: int) -> None:
    st.title("Presupuesto mensual")
    today = date.today()

    year = st.selectbox(
        "Año",
        list(range(today.year, today.year - 8, -1)),
        key=f"budget_year_{profile_id}",
    )
    month = st.selectbox(
        "Mes",
        list(range(1, 13)),
        index=today.month - 1,
        format_func=lambda value: MONTHS[value - 1],
        key=f"budget_month_{profile_id}",
    )

    month_key = f"{year:04d}-{month:02d}"
    current_budget = budget_for_month(profile_id, month_key)
    projected_default = (
        float(current_budget["projected_income"])
        if current_budget
        else 0.0
    )

    st.subheader("Ingresos proyectados")

    with st.form(f"budget_form_{profile_id}_{month_key}"):
        projected = st.number_input(
            "Ingreso esperado del mes (COP)",
            min_value=0.0,
            value=projected_default,
            step=10000.0,
            format="%.0f",
        )
        submitted = st.form_submit_button("Guardar proyección")

        if submitted:
            execute(
                """
                INSERT INTO budgets (profile_id, month, projected_income)
                VALUES (?, ?, ?)
                ON CONFLICT(profile_id, month)
                DO UPDATE SET projected_income = excluded.projected_income
                """,
                (profile_id, month_key, projected),
            )
            st.success("Proyección guardada.")
            st.rerun()

    st.subheader("Obligaciones fijas")

    with st.form(
        f"obligation_form_{profile_id}",
        clear_on_submit=True,
    ):
        name = st.text_input(
            "Obligación (por ejemplo, arriendo o servicios)"
        )
        amount = st.number_input(
            "Valor mensual (COP)",
            min_value=0.0,
            step=10000.0,
            format="%.0f",
        )
        due_day = st.number_input(
            "Día límite de pago",
            min_value=1,
            max_value=31,
            value=1,
        )
        submitted = st.form_submit_button("Agregar obligación")

        if submitted:
            if not name.strip():
                st.error("Escribe el nombre de la obligación.")
            elif amount <= 0:
                st.error("El valor debe ser mayor que cero.")
            else:
                execute(
                    "INSERT INTO obligations "
                    "(profile_id, name, amount, due_day) "
                    "VALUES (?, ?, ?, ?)",
                    (profile_id, name.strip(), amount, int(due_day)),
                )
                st.success("Obligación agregada.")
                st.rerun()

    obligations = fetch_all(
        "SELECT * FROM obligations "
        "WHERE profile_id = ? ORDER BY due_day, name",
        (profile_id,),
    )

    total_obligations = sum(row["amount"] for row in obligations)
    planned_margin = projected_default - total_obligations

    c1, c2, c3 = st.columns(3)
    c1.metric("Ingreso proyectado", money(projected_default))
    c2.metric("Obligaciones mensuales", money(total_obligations))
    c3.metric("Margen planificado", money(planned_margin))

    if obligations:
        frame = pd.DataFrame(
            [
                {
                    "Obligación": row["name"],
                    "Valor": money(row["amount"]),
                    "Fecha límite": f"Día {row['due_day']}",
                }
                for row in obligations
            ]
        )
        st.dataframe(frame, use_container_width=True, hide_index=True)

        with st.expander("Eliminar una obligación"):
            choice = st.selectbox(
                "Obligación",
                {
                    f"{row['name']} · {money(row['amount'])}": row["id"]
                    for row in obligations
                },
                key=f"delete_obligation_{profile_id}",
            )

            if st.button(
                "Eliminar obligación",
                key=f"delete_obligation_btn_{profile_id}",
            ):
                execute(
                    "DELETE FROM obligations "
                    "WHERE id = ? AND profile_id = ?",
                    (choice, profile_id),
                )
                st.success("Obligación eliminada.")
                st.rerun()
    else:
        st.info(
            "Agrega tus pagos fijos para calcular el margen planificado."
        )

    st.caption(
        "El margen planificado es ingreso proyectado menos obligaciones fijas. "
        "Los gastos registrados se muestran por separado para evitar contar "
        "dos veces un pago que también figure como obligación."
    )


def show_debts(profile_id: int) -> None:
    st.title("Deudas")

    with st.form(
        f"debt_form_{profile_id}",
        clear_on_submit=True,
    ):
        creditor = st.text_input("Acreedor o banco")

        total_amount = st.number_input(
            "Deuda total (COP)",
            min_value=0.0,
            step=10000.0,
            format="%.0f",
        )
        remaining = st.number_input(
            "Saldo pendiente actual (COP)",
            min_value=0.0,
            step=10000.0,
            format="%.0f",
        )
        interest = st.number_input(
            "Tasa de interés anual (%)",
            min_value=0.0,
            step=0.1,
            format="%.2f",
        )

        c1, c2 = st.columns(2)
        total_installments = c1.number_input(
            "Número total de cuotas",
            min_value=1,
            value=1,
            step=1,
        )
        installments_left = c2.number_input(
            "Cuotas faltantes",
            min_value=0,
            value=1,
            step=1,
        )

        c3, c4 = st.columns(2)
        monthly_payment = c3.number_input(
            "Valor mensual (COP)",
            min_value=0.0,
            step=10000.0,
            format="%.0f",
        )
        due_day = c4.number_input(
            "Día límite de pago",
            min_value=1,
            max_value=31,
            value=1,
        )

        submitted = st.form_submit_button(
            "Guardar deuda",
            type="primary",
        )

        if submitted:
            if not creditor.strip():
                st.error("Escribe el nombre del acreedor o banco.")
            elif total_amount <= 0 or monthly_payment <= 0:
                st.error(
                    "La deuda total y el valor mensual deben ser mayores que cero."
                )
            elif remaining > total_amount:
                st.error(
                    "El saldo pendiente no puede superar la deuda total."
                )
            elif installments_left > total_installments:
                st.error(
                    "Las cuotas faltantes no pueden superar el total de cuotas."
                )
            else:
                execute(
                    """
                    INSERT INTO debts
                        (profile_id, creditor, total_amount, remaining_balance,
                         interest_rate, total_installments, installments_left,
                         monthly_payment, due_day)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        profile_id,
                        creditor.strip(),
                        total_amount,
                        remaining,
                        interest,
                        int(total_installments),
                        int(installments_left),
                        monthly_payment,
                        int(due_day),
                    ),
                )
                st.success("Deuda guardada.")
                st.rerun()

    debts = fetch_all(
        "SELECT * FROM debts "
        "WHERE profile_id = ? ORDER BY due_day, creditor",
        (profile_id,),
    )

    if not debts:
        st.info("Todavía no has agregado deudas.")
        return

    st.subheader("Resumen de deudas")

    debt_frame = pd.DataFrame(
        [
            {
                "Acreedor": row["creditor"],
                "Saldo pendiente": money(row["remaining_balance"]),
                "Tasa anual": f"{row['interest_rate']:.2f}%",
                "Cuotas": (
                    f"{row['installments_left']} "
                    f"de {row['total_installments']}"
                ),
                "Cuota mensual": money(row["monthly_payment"]),
                "Próximo vencimiento": next_due_date(
                    row["due_day"]
                ).strftime("%d/%m/%Y"),
            }
            for row in debts
        ]
    )

    st.dataframe(debt_frame, use_container_width=True, hide_index=True) **…**

_This response is too long to display in full._
