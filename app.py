import hashlib
import inspect
import io
import os
import re
from urllib.parse import urljoin, urlparse

import numpy as np
import pandas as pd
import plotly.express as px
import requests
import streamlit as st
from charset_normalizer import detect
from sqlalchemy import create_engine, inspect as sa_inspect
from sqlalchemy.engine import URL

try:
    from wordcloud import WordCloud
    WORDCLOUD_AVAILABLE = True
except ImportError:
    WORDCLOUD_AVAILABLE = False

# ==============================================================================
# CONFIG
# ==============================================================================
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "200"))
# Hosts the Kobo API token may be sent to (comma-separated, matches subdomains too)
ALLOWED_KOBO_HOSTS = tuple(
    h.strip().lower()
    for h in os.getenv("KOBO_ALLOWED_HOSTS", "kobotoolbox.org,humanitarianresponse.info").split(",")
    if h.strip()
)
# Set ALLOW_SQLITE=0 on shared/hosted deployments so users can't open server files
ALLOW_SQLITE = os.getenv("ALLOW_SQLITE", "1") == "1"
MAX_BARS = 30


# ==============================================================================
# CLEANING HELPERS (self-contained: also embedded into the Power BI script)
# ==============================================================================
def blank_to_na(s):
    """Strip strings and turn blank strings into real NaN. Non-string values are untouched."""
    if s.dtype == object or pd.api.types.is_string_dtype(s):
        return s.map(
            lambda x: (np.nan if x.strip() == "" else x.strip()) if isinstance(x, str) else x
        )
    return s


def cast_series(s, target):
    """Cast a Series to the chosen type without silently destroying data."""
    if target == "String":
        return s.astype("string")
    if target == "Integer":
        num = pd.to_numeric(s, errors="coerce")
        try:
            return num.astype("Int64")  # keeps missing values as <NA>, no fake zeros
        except (TypeError, ValueError):
            return num  # has decimals: keep as float instead of truncating
    if target == "Float":
        return pd.to_numeric(s, errors="coerce")
    if target == "DateTime":
        return pd.to_datetime(s, errors="coerce")
    if target == "Boolean":
        m = {"true": True, "false": False, "yes": True, "no": False, "y": True, "n": False,
             "1": True, "0": False, "t": True, "f": False}
        return s.astype(str).str.strip().str.lower().map(m).astype("boolean")
    return s


def find_duplicates(df, subset=None):
    """Boolean mask of duplicate rows (True = later copy). Rows with a null key are never
    treated as duplicates when a subset is given."""
    safe = df.copy()
    for c in safe.columns:
        if safe[c].dtype == object:
            safe[c] = safe[c].map(lambda x: str(x) if isinstance(x, (list, dict, set)) else x)
    if subset:
        mask = safe.duplicated(subset=subset)
        return mask & ~safe[subset].isna().any(axis=1)
    return safe.duplicated()


def handle_missing(df, cols, strategy):
    """Apply a missing-value strategy to the given columns only."""
    df = df.copy()
    for c in cols:
        df[c] = blank_to_na(df[c])
    if strategy == "Drop Rows with Any Missing Data":
        return df.dropna(subset=cols).reset_index(drop=True)
    if strategy == "Forward Fill":
        df[cols] = df[cols].ffill()
        return df
    if strategy == "Backward Fill":
        df[cols] = df[cols].bfill()
        return df
    for c in cols:
        if strategy == "Fill with Zero":
            if not pd.api.types.is_numeric_dtype(df[c]):
                df[c] = df[c].astype(object)
            df[c] = df[c].fillna(0)
        elif strategy in ("Fill with Mean", "Fill with Median"):
            num = pd.to_numeric(df[c], errors="coerce")
            if num.notna().sum() == 0 or num.notna().sum() / max(df[c].notna().sum(), 1) < 0.9:
                continue  # not a numeric column: leave untouched
            val = num.mean() if strategy == "Fill with Mean" else num.median()
            df[c] = df[c].fillna(val)
        elif strategy == "Fill with Mode":
            m = df[c].mode()
            if not m.empty:
                df[c] = df[c].fillna(m.iloc[0])
    return df


# ==============================================================================
# OTHER HELPERS
# ==============================================================================
def arrow_safe(df):
    """Avoid Arrow serialization errors for mixed-type object columns (display only)."""
    out = df.copy()
    for c in out.columns[out.dtypes == object]:
        out[c] = out[c].map(lambda x: x if x is None or (isinstance(x, float) and np.isnan(x)) else str(x))
    return out


def sanitize_csv(df):
    """Neutralise spreadsheet formula injection (cells starting with = + - @)."""
    out = df.copy()
    for c in out.columns[(out.dtypes == object) | (out.dtypes == "string")]:
        out[c] = out[c].map(
            lambda x: "'" + x if isinstance(x, str) and x[:1] in ("=", "+", "-", "@", "\t", "\r") else x
        )
    return out


def validate_kobo_server(url):
    p = urlparse(url)
    host = (p.hostname or "").lower()
    if p.scheme != "https":
        raise ValueError("KoboToolbox server URL must use https.")
    if not any(host == h or host.endswith("." + h) for h in ALLOWED_KOBO_HOSTS):
        raise ValueError(
            f"Host '{host}' is not allowed. Allowed: {', '.join(ALLOWED_KOBO_HOSTS)} "
            "(configure with the KOBO_ALLOWED_HOSTS environment variable)."
        )
    return f"https://{host}" + (f":{p.port}" if p.port else "")


def fetch_all_kobo_submissions(kobo_server, form_uid, api_token, request_timeout=30, page_limit=1000):
    base = validate_kobo_server(kobo_server)
    if not re.fullmatch(r"[A-Za-z0-9]+", form_uid):
        raise ValueError("Form UID may only contain letters and digits.")

    headers = {"Authorization": f"Token {api_token}"}
    results, page_count = [], 0
    url = f"{base}/api/v2/assets/{form_uid}/data.json"
    params = {"limit": page_limit}

    while url:
        page_count += 1
        response = requests.get(url, headers=headers, params=params, timeout=request_timeout)
        if response.status_code != 200:
            raise ValueError(f"API Error {response.status_code}: {response.text[:200]}")
        payload = response.json()
        results.extend(payload.get("results", []))

        next_url = payload.get("next")
        if not next_url:
            break
        url = urljoin(base + "/", next_url)
        if urlparse(url).netloc != urlparse(base).netloc:
            raise ValueError("Refusing to follow a pagination link to a different host.")
        params = None
    return results, page_count


def build_db_url(db_type, server, port, database, username, password, auth_type, sqlite_path):
    """Return a properly escaped SQLAlchemy URL string, or None if fields are incomplete."""
    if db_type == "SQLite":
        return URL.create("sqlite", database=sqlite_path).render_as_string(hide_password=False) if sqlite_path else None
    if not (server and database):
        return None
    port_int = None
    if port:
        if not port.isdigit():
            raise ValueError("Port must be a number.")
        port_int = int(port)
    if db_type in ("PostgreSQL", "MySQL"):
        driver = "postgresql+psycopg2" if db_type == "PostgreSQL" else "mysql+pymysql"
        return URL.create(driver, username=username or None, password=password or None,
                          host=server, port=port_int, database=database).render_as_string(hide_password=False)
    # SQL Server
    from urllib import parse as urllib_parse
    srv = f"{server},{port_int}" if port_int else server
    odbc = f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={srv};DATABASE={database};"
    if auth_type == "Windows Authentication":
        odbc += "Trusted_Connection=yes;"
    else:
        safe_pwd = password.replace("}", "}}")
        odbc += f"UID={username};PWD={{{safe_pwd}}};"
    odbc += "TrustServerCertificate=yes;Connect Timeout=15;"  # enable a real cert in production
    return f"mssql+pyodbc:///?odbc_connect={urllib_parse.quote_plus(odbc)}"


def clear_state(*keys):
    for k in keys:
        st.session_state[k] = None


# ==============================================================================
# UI
# ==============================================================================
st.set_page_config(page_title="Custom Data Cleaning Pipeline", layout="wide", page_icon="🧼")
st.title("🧼 Custom Data Cleaning Pipeline")
st.markdown(
    "#### A modular data cleaning pipeline. **Data is processed in memory; "
    "uploads and Kobo pulls are not written to disk by this app.**"
)

for key in ("kobo_df", "db_df", "data_source", "all_uploaded_tables", "file_sig",
            "db_tables", "db_engine_uri", "db_fp", "active_db_table_name"):
    st.session_state.setdefault(key, None)

data_source = st.radio("Choose your data source:", ["File Upload", "KoboToolbox", "SQL Database"],
                       horizontal=True, key="data_source_radio")

df = None
output_filename = "cleaned_data.csv"
form_uid, api_token, kobo_server = "", "", "https://kf.kobotoolbox.org"
ns = data_source  # namespace for widget keys, refined per source below

if st.session_state["data_source"] != data_source:
    st.session_state["data_source"] = data_source
    clear_state("all_uploaded_tables", "file_sig", "kobo_df", "db_df", "db_tables", "db_fp")

# ---------------------------------------------------------------- FILE UPLOAD
if data_source == "File Upload":
    uploaded_file = st.file_uploader("Choose a file", type=["csv", "xlsx", "parquet"],
                                     help="Supported formats: CSV, Excel (.xlsx), Parquet")
    if uploaded_file is None:
        clear_state("all_uploaded_tables", "file_sig")  # don't keep stale data
    else:
        sig = (uploaded_file.name, uploaded_file.size)
        if uploaded_file.size > MAX_UPLOAD_MB * 1024 * 1024:
            st.error(f"❌ File is larger than {MAX_UPLOAD_MB} MB.")
            clear_state("all_uploaded_tables", "file_sig")
        elif st.session_state["file_sig"] != sig:
            ext = uploaded_file.name.rsplit(".", 1)[-1].lower()
            try:
                with st.spinner("Reading file..."):
                    if ext == "csv":
                        enc = detect(uploaded_file.read(20000))["encoding"] or "utf-8"
                        uploaded_file.seek(0)
                        tables = {"CSV_Data": pd.read_csv(uploaded_file, encoding=enc)}
                    elif ext == "parquet":
                        tables = {"Parquet_Data": pd.read_parquet(uploaded_file)}
                    else:
                        tables = pd.read_excel(uploaded_file, sheet_name=None)
                st.session_state["all_uploaded_tables"] = tables
                st.session_state["file_sig"] = sig
            except Exception as e:
                st.error(f"❌ Error reading file: {e}")
                clear_state("all_uploaded_tables", "file_sig")

        tables = st.session_state["all_uploaded_tables"]
        if tables:
            st.success(f"✅ File loaded. Found {len(tables)} table(s).")
            sheet = st.selectbox("🎯 Select which sheet/table to clean:", list(tables.keys()), key="sheet_selector")
            df = tables[sheet]
            ns = f"file_{sheet}"
            base = os.path.splitext(uploaded_file.name)[0]
            output_filename = re.sub(r"[^\w.\-]", "_", f"cleaned_{sheet}_{base}.csv")

# ---------------------------------------------------------------- KOBOTOOLBOX
elif data_source == "KoboToolbox":
    st.markdown("### 🔑 KoboToolbox Configuration")
    c1, c2 = st.columns(2)
    form_uid = c1.text_input("Form UID (Asset UID)", placeholder="e.g., aBcDeF123", key="form_uid").strip()
    api_token = c2.text_input("API Token", type="password", placeholder="Paste your Kobo Secret Token",
                              key="api_token").strip()
    c3, c4 = st.columns(2)
    kobo_server = c3.text_input("KoboToolbox Server URL", value="https://kf.kobotoolbox.org",
                                key="kobo_server").strip().rstrip("/")
    c4.markdown("<br>", unsafe_allow_html=True)
    pull_data = c4.button("🔄 Pull Live Data from KoboToolbox", type="primary", use_container_width=True)

    if pull_data:
        if not form_uid or not api_token:
            st.error("⚠️ Please enter both your Form UID and API Token.")
        else:
            try:
                with st.spinner("📡 Streaming Kobo pages into RAM..."):
                    results, page_count = fetch_all_kobo_submissions(kobo_server, form_uid, api_token)
                if results:
                    st.session_state["kobo_df"] = pd.json_normalize(results)
                    st.success(f"✅ Loaded {len(results):,} submissions across {page_count} page(s)!")
                else:
                    st.warning("⚠️ Form contains 0 submissions.")
                    st.session_state["kobo_df"] = None
            except Exception as e:
                st.error(f"❌ {e}")

    if st.session_state["kobo_df"] is not None:
        df = st.session_state["kobo_df"]
        ns = f"kobo_{form_uid}"
        output_filename = re.sub(r"[^\w.\-]", "_", f"cleaned_kobo_{form_uid}.csv")

# ---------------------------------------------------------------- SQL DATABASE
else:
    st.markdown("### 🗄️ SQL Database Connection")
    db_types = (["SQLite"] if ALLOW_SQLITE else []) + ["PostgreSQL", "MySQL", "SQL Server (MS SQL)"]
    db_type = st.selectbox("Database System Type", db_types)
    server = port = database = username = password = sqlite_path = ""
    auth_type = "Database Authentication"

    if db_type == "SQLite":
        sqlite_path = st.text_input("Database File Path", placeholder="example.db").strip()
    else:
        cs, cp = st.columns(2)
        server = cs.text_input("Server", placeholder="Server name or host").strip()
        port = cp.text_input("Port (optional)", placeholder="leave blank for default").strip()
        database = st.text_input("Database", placeholder="my_database_name").strip()
        if db_type == "SQL Server (MS SQL)":
            auth_type = st.radio("Authentication Method", ["Windows Authentication", "Database Authentication"],
                                 horizontal=True)
            if auth_type == "Windows Authentication":
                st.info("Windows Authentication may not work from cloud hosts or Linux.")
        if auth_type == "Database Authentication":
            cu, cw = st.columns(2)
            username = cu.text_input("Username")
            password = cw.text_input("Password", type="password")

    try:
        db_uri = build_db_url(db_type, server, port, database, username, password, auth_type, sqlite_path)
    except ValueError as e:
        st.error(str(e))
        db_uri = None

    # Invalidate previous connection if any credential changed
    fp = hashlib.sha256((db_uri or "").encode()).hexdigest()
    if st.session_state["db_fp"] not in (None, fp):
        clear_state("db_tables", "db_df", "db_engine_uri")
    st.session_state["db_fp"] = fp

    _, cb = st.columns([3, 1])
    cb.markdown("<br>", unsafe_allow_html=True)
    if cb.button("🔌 Connect", type="secondary", use_container_width=True) and db_uri:
        engine = create_engine(db_uri)
        try:
            st.session_state["db_tables"] = sa_inspect(engine).get_table_names()
            st.session_state["db_engine_uri"] = db_uri
        except Exception as e:
            st.error(f"❌ Connection failed. Check credentials/server.\n\nDetails: {e}")
            clear_state("db_tables")
        finally:
            engine.dispose()

    if st.session_state["db_tables"]:
        st.success("✅ Connected successfully!")
        c3, c4 = st.columns([3, 1])
        selected_db_table = c3.selectbox("🎯 Select Table (Navigator):", st.session_state["db_tables"])
        c4.markdown("<br>", unsafe_allow_html=True)
        if c4.button("📥 Load Data", type="primary", use_container_width=True):
            engine = create_engine(st.session_state["db_engine_uri"])
            try:
                with st.spinner(f"Loading `{selected_db_table}`..."):
                    st.session_state["db_df"] = pd.read_sql_table(selected_db_table, con=engine)
                    st.session_state["active_db_table_name"] = selected_db_table
                st.success(f"✅ Loaded {len(st.session_state['db_df']):,} rows!")
            except Exception as e:
                st.error(f"❌ Failed to load table: {e}")
            finally:
                engine.dispose()

    if st.session_state["db_df"] is not None:
        df = st.session_state["db_df"]
        t_name = st.session_state.get("active_db_table_name") or "db_table"
        ns = f"db_{t_name}"
        output_filename = re.sub(r"[^\w.\-]", "_", f"cleaned_db_{t_name}.csv")

# ==============================================================================
# PIPELINE
# ==============================================================================
if df is None:
    st.info("👆 Upload a file, pull from KoboToolbox, or connect to a SQL database to begin.")
    st.stop()

st.markdown("---")
st.sidebar.header("Pipeline Configuration")
st.sidebar.markdown("Select processes to apply:")
show_shape = st.sidebar.checkbox("Show Data Dimensions (Shape)", value=True)
do_drop_cols = st.sidebar.checkbox("Drop Columns", value=False)
do_rename_cols = st.sidebar.checkbox("Rename Columns", value=False)
do_data_types = st.sidebar.checkbox("Deal with Data Types", value=False)
do_dedup = st.sidebar.checkbox("Remove Duplicates", value=False)
do_missing_values = st.sidebar.checkbox("Handle Missing Values", value=False)
show_describe = st.sidebar.checkbox("Show Summary Statistics (Describe)", value=False)
safe_export = st.sidebar.checkbox("Neutralise spreadsheet formulas in CSV export", value=True,
                                  help="Prefixes cells starting with = + - @ with an apostrophe.")

cleaned_df = df.copy()
all_columns = df.columns.tolist()

# 1. Drop columns
columns_to_drop = []
if do_drop_cols:
    st.write("### ✂️ Select Columns to Remove")
    columns_to_drop = st.multiselect("Select columns to REMOVE:", options=all_columns, key=f"drop_{ns}")
    cleaned_df = cleaned_df.drop(columns=columns_to_drop)
remaining_cols = [c for c in all_columns if c not in columns_to_drop]

# 2. Rename columns
columns_to_rename = {}
if do_rename_cols and remaining_cols:
    st.write("### ✏️ Rename Columns")
    with st.expander("Configure Column Map Re-labeling", expanded=False):
        r_cols = st.columns(2)
        for idx, col in enumerate(remaining_cols):
            new_name = r_cols[idx % 2].text_input(f"Original: `{col}`", value="", key=f"ren_{ns}_{col}").strip()
            if new_name:
                columns_to_rename[col] = new_name
        cleaned_df = cleaned_df.rename(columns=columns_to_rename)

# 3. Data types (defaults to the ORIGINAL type; numeric-looking columns just get a hint)
type_mapping_dict = {}
if do_data_types and remaining_cols:
    st.write("### 🔢 Deal with Data Types")
    with st.expander("Deal with Data Types", expanded=False):
        t_cols = st.columns(3)
        for idx, col in enumerate(remaining_cols):
            cur = columns_to_rename.get(col, col)
            if cur not in cleaned_df.columns:
                continue
            orig = str(cleaned_df[cur].dtype)
            options = [f"({orig})", "String", "Integer", "Float", "DateTime", "Boolean"]
            hint = ""
            if orig == "object":
                sample = cleaned_df[cur].dropna().head(100)
                if not sample.empty and pd.to_numeric(sample, errors="coerce").notna().mean() >= 0.95:
                    hint = " 💡 looks numeric"
            choice = t_cols[idx % 3].selectbox(f"Type for `{cur}` (original: {orig}){hint}:", options,
                                               index=0, key=f"type_{ns}_{col}")
            if choice != options[0]:
                type_mapping_dict[cur] = choice
                try:
                    cleaned_df[cur] = cast_series(cleaned_df[cur], choice)
                    if choice == "Integer" and str(cleaned_df[cur].dtype) != "Int64":
                        st.warning(f"`{cur}` has decimals, so it was kept as Float instead of truncating.")
                except Exception as e:
                    st.warning(f"Failed casting `{cur}` to {choice}: {e}")

# 4. Duplicates (opt-in)
dedup_subset = None
if do_dedup:
    st.write("### 👥 Duplicate Removal Configuration")
    strategy = st.radio("Duplicate evaluation strategy:",
                        ["Entire Row (Match across all columns)", "Primary Key / Distinguishing Column(s)"],
                        horizontal=True, key=f"dedup_choice_{ns}")
    if strategy.startswith("Primary"):
        dedup_subset = st.multiselect("Choose key column(s) (rows with a blank key are kept):",
                                      options=cleaned_df.columns.tolist(), key=f"dedup_subset_{ns}") or None
        if not dedup_subset:
            st.info("💡 Select one or more columns. Until then the whole row is compared.")
    try:
        mask = find_duplicates(cleaned_df, dedup_subset)
        n_dup = int(mask.sum())
        if n_dup:
            cleaned_df = cleaned_df[~mask].reset_index(drop=True)
            st.success(f"Removed {n_dup:,} duplicate row(s).")
        else:
            st.caption("No duplicates found.")
    except Exception as e:
        st.error(f"Duplicate check failed: {e}")

# 5. Missing values
na_strategy, na_selected_cols = "None", []
if do_missing_values and remaining_cols:
    st.write("### 🩹 Handling Missing Values")
    with st.expander("Dealing With Null Values", expanded=False):
        m1, m2 = st.columns(2)
        na_strategy = m1.selectbox(
            "Choose Strategy:",
            ["Drop Rows with Any Missing Data", "Fill with Mean", "Fill with Median", "Fill with Mode",
             "Fill with Zero", "Forward Fill", "Backward Fill"],
            key=f"na_strat_{ns}")
        working = [columns_to_rename.get(c, c) for c in remaining_cols]
        working = [c for c in working if c in cleaned_df.columns]
        na_selected_cols = m2.multiselect("Apply to Specific Columns:", options=working, default=working,
                                          key=f"na_sel_{ns}")
        st.caption("Mean/Median are only applied to columns that are numeric; others are left unchanged.")
    if na_selected_cols:
        cleaned_df = handle_missing(cleaned_df, na_selected_cols, na_strategy)

# ---- Metrics on the FINAL cleaned data
if show_shape:
    st.write("### Data Dimensions (Shape)")
    try:
        dup_now = int(find_duplicates(cleaned_df).sum())
    except Exception:
        dup_now = "N/A"
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Current Rows", f"{cleaned_df.shape[0]:,}", delta=f"{cleaned_df.shape[0] - df.shape[0]:,} vs original")
    k2.metric("Current Columns", cleaned_df.shape[1])
    k3.metric("Total Missing Values", f"{int(cleaned_df.isna().sum().sum()):,}")
    k4.metric("Duplicate Rows", dup_now)

if show_describe:
    st.write("### 📊 Summary Statistics")
    st.dataframe(arrow_safe(cleaned_df.describe()), use_container_width=True)

st.write("### Preview Of Your Data")
st.dataframe(arrow_safe(cleaned_df.head(15)), use_container_width=True)

# ==============================================================================
# VISUALIZATION
# ==============================================================================
st.markdown("---")
st.write("### 📊 Interactive Data Visualization Suite")

numeric_cols = cleaned_df.select_dtypes(include=["number"]).columns.tolist()
categorical_cols = cleaned_df.select_dtypes(include=["object", "category", "string"]).columns.tolist()
all_viz_cols = cleaned_df.columns.tolist()
agg_map = {"Average": "mean", "Sum": "sum", "Median": "median", "Max": "max", "Min": "min"}

viz_type = st.selectbox("Choose Chart Type:", ["Bar Chart", "Pie Chart", "Line Chart", "Word Cloud"],
                        key=f"viz_{ns}")
fig = None

with st.expander(f"Configure {viz_type} Parameters", expanded=True):
    if viz_type == "Bar Chart":
        if not categorical_cols:
            st.info("💡 Need at least one categorical column to anchor bars.")
        else:
            a, b = st.columns(2)
            x_axis = a.selectbox("X Axis (Category):", categorical_cols, key=f"bar_x_{ns}")
            y_axis = b.selectbox("Y Axis (Value):", ["Row Count"] + numeric_cols, key=f"bar_y_{ns}")
            if y_axis == "Row Count":
                data = cleaned_df[x_axis].astype(str).value_counts().reset_index()
                data.columns = [x_axis, "Row Count"]
                y_plot, title = "Row Count", f"Distribution of {x_axis}"
            else:
                agg = st.radio("Aggregation:", list(agg_map), horizontal=True, key=f"bar_agg_{ns}")
                data = (cleaned_df.groupby(x_axis, as_index=False)[y_axis].agg(agg_map[agg])
                        .sort_values(y_axis, ascending=False))
                y_plot, title = y_axis, f"{agg} {y_axis} by {x_axis}"
            if len(data) > MAX_BARS:
                st.caption(f"Showing top {MAX_BARS} of {len(data):,} categories.")
                data = data.head(MAX_BARS)
            fig = px.bar(data, x=x_axis, y=y_plot, title=title, template="plotly_white")

    elif viz_type == "Pie Chart":
        if not categorical_cols:
            st.info("💡 Need at least one categorical column to construct segments.")
        else:
            a, b = st.columns(2)
            names_col = a.selectbox("Slices (Category Column):", categorical_cols, key=f"pie_names_{ns}")
            values_col = b.selectbox("Slice Proportions:", ["Row Count Summary"] + numeric_cols,
                                     key=f"pie_values_{ns}")
            if values_col == "Row Count Summary":
                data = cleaned_df[names_col].astype(str).value_counts().reset_index()
                data.columns = [names_col, "Count"]
                data = data.head(MAX_BARS)
                fig = px.pie(data, names=names_col, values="Count", title=f"Breakdown of {names_col}")
            else:
                agg = st.radio("Aggregation:", list(agg_map), index=1, horizontal=True, key=f"pie_agg_{ns}")
                data = (cleaned_df.groupby(names_col, as_index=False)[values_col].agg(agg_map[agg])
                        .sort_values(values_col, ascending=False).head(MAX_BARS))
                fig = px.pie(data, names=names_col, values=values_col,
                             title=f"{agg} {values_col} across {names_col}")

    elif viz_type == "Line Chart":
        if not numeric_cols:
            st.info("💡 Need at least one numeric column to chart.")
        else:
            a, b = st.columns(2)
            x_axis = a.selectbox("X Axis (Timeline/Index):", all_viz_cols, key=f"line_x_{ns}")
            y_axis = b.selectbox("Y Axis (Numeric Value):", numeric_cols, key=f"line_y_{ns}")
            if x_axis == y_axis:
                st.warning("Choose different columns for the X and Y axes.")
            else:
                agg = st.radio("Aggregation:", list(agg_map), horizontal=True, key=f"line_agg_{ns}")
                data = cleaned_df.groupby(x_axis, as_index=False)[y_axis].agg(agg_map[agg]).sort_values(x_axis)
                fig = px.line(data, x=x_axis, y=y_axis, title=f"{agg} {y_axis} Trend over {x_axis}",
                              template="plotly_white")

    elif viz_type == "Word Cloud":
        if not WORDCLOUD_AVAILABLE:
            st.warning("The optional `wordcloud` package is not installed (`pip install wordcloud`).")
        elif not categorical_cols:
            st.info("We need at least one text column to generate a word cloud.")
        else:
            a, b = st.columns(2)
            text_col = a.selectbox("Select the text column", categorical_cols, key=f"wc_text_{ns}")
            bg_color = b.selectbox("Background color", ["Black", "White"], key=f"wc_bg_{ns}")
            text_data = " ".join(cleaned_df[text_col].dropna().astype(str))
            if not text_data.strip():
                st.warning("Selected column has no text data.")
            else:
                with st.spinner("Generating word cloud..."):
                    wc = WordCloud(width=800, height=400, background_color=bg_color.lower(),
                                   collocations=False).generate(text_data)
                    st.image(wc.to_array(), use_container_width=True)

if fig is not None:
    st.plotly_chart(fig, use_container_width=True)

# ==============================================================================
# POWER BI SCRIPT (token is NEVER embedded)
# ==============================================================================
if data_source == "KoboToolbox" and form_uid:
    st.write("### 📊 Import Directly to Power BI")
    try:
        helper_src = "\n\n".join(inspect.getsource(f) for f in (blank_to_na, cast_series, find_duplicates, handle_missing))
    except OSError:
        helper_src = None

    if helper_src is None:
        st.info("Power BI script generation needs the app source file on disk.")
    else:
        pbi = '''import numpy as np
import pandas as pd
import requests
from urllib.parse import urljoin, urlparse

# Paste your token here (or read it from a Power BI parameter). Never share it.
TOKEN = "PASTE_YOUR_KOBO_TOKEN_HERE"
BASE = "__BASE__"
UID = "__UID__"

# --- helpers (same logic as the app) ---
__HELPERS__

# 1. Fetch live data with pagination
headers = {"Authorization": "Token " + TOKEN}
url, params, all_results = BASE + "/api/v2/assets/" + UID + "/data.json", {"limit": 1000}, []
while url:
    r = requests.get(url, headers=headers, params=params, timeout=45)
    r.raise_for_status()
    payload = r.json()
    all_results.extend(payload.get("results", []))
    nxt = payload.get("next")
    if not nxt:
        break
    url = urljoin(BASE + "/", nxt)
    if urlparse(url).netloc != urlparse(BASE).netloc:
        raise ValueError("Unexpected pagination host")
    params = None

# 2. Flatten
df = pd.json_normalize(all_results)
'''
        try:
            base_for_script = validate_kobo_server(kobo_server)
        except ValueError:
            base_for_script = kobo_server
        pbi = pbi.replace("__BASE__", base_for_script).replace("__UID__", form_uid).replace("__HELPERS__", helper_src)

        if do_drop_cols and columns_to_drop:
            pbi += f"\n# Drop columns\ndf = df.drop(columns={list(columns_to_drop)!r}, errors='ignore')\n"
        if do_rename_cols and columns_to_rename:
            pbi += f"\n# Rename columns\ndf = df.rename(columns={columns_to_rename!r})\n"
        if type_mapping_dict:
            pbi += (f"\n# Cast data types\nfor _c, _t in {type_mapping_dict!r}.items():\n"
                    "    if _c in df.columns:\n        df[_c] = cast_series(df[_c], _t)\n")
        if do_dedup:
            pbi += (f"\n# Remove duplicates\ndf = df[~find_duplicates(df, {dedup_subset!r})].reset_index(drop=True)\n")
        if do_missing_values and na_selected_cols:
            pbi += (f"\n# Missing values ({na_strategy})\n"
                    f"df = handle_missing(df, [c for c in {na_selected_cols!r} if c in df.columns], {na_strategy!r})\n")
        pbi += ("\n# Stringify list/dict columns for Power BI\nfor _c in df.columns:\n"
                "    if df[_c].dtype == object:\n"
                "        df[_c] = df[_c].map(lambda x: str(x) if isinstance(x, (list, dict, set)) else x)\n")

        st.markdown("> Copy the script into Power BI's Python source. Replace `PASTE_YOUR_KOBO_TOKEN_HERE` with your token.")
        st.code(pbi, language="python")

# ==============================================================================
# DOWNLOAD
# ==============================================================================
st.markdown("---")
st.write("### 📥 Download Cleaned Output Package")
try:
    export_df = sanitize_csv(cleaned_df) if safe_export else cleaned_df
    st.download_button(
        label=f"📥 Download Cleaned Data ({output_filename})",
        data=export_df.to_csv(index=False).encode("utf-8"),
        file_name=output_filename,
        mime="text/csv",
        type="primary",
    )
except Exception as e:
    st.error(f"❌ Error generating download: {e}")