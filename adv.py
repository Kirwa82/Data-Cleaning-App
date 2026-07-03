import streamlit as st
import pandas as pd
import io
import requests
from charset_normalizer import detect
from urllib.parse import urljoin
from sqlalchemy import create_engine, inspect
import plotly.express as px
from wordcloud import WordCloud
WORDCLOUD_AVAILABLE = True
import matplotlib.pyplot as plt

# ==============================================================================
# WEB USER INTERFACE (STREAMLIT APP RENDER)
# ==============================================================================
st.set_page_config(page_title="Custom Data Cleaning Pipeline", layout="wide", page_icon="🧼")

st.title("🧼 Custom Data Cleaning Pipeline")
st.markdown("#### This is a modular data cleaning pipeline. **Your data remains completely in RAM and is never saved to a disk.**")

# Initialize session state tracking
if 'kobo_df' not in st.session_state:
    st.session_state['kobo_df'] = None
if 'db_df' not in st.session_state:
    st.session_state['db_df'] = None
if 'data_source' not in st.session_state:
    st.session_state['data_source'] = None
if 'all_uploaded_tables' not in st.session_state:
    st.session_state['all_uploaded_tables'] = None # Stores {sheet_name: df} for files
if 'selected_sheet' not in st.session_state:
    st.session_state['selected_sheet'] = None

# Helper function to make DataFrame hashable for duplicate detection
def make_hashable(df):
    df_safe = df.copy()
    for col in df_safe.columns:
        sample = df_safe[col].dropna()
        if len(sample) > 0:
            first_val = sample.iloc[0]
            if isinstance(first_val, (list, dict, set)):
                df_safe[col] = df_safe[col].apply(
                    lambda x: str(x) if isinstance(x, (list, dict, set)) else x
                )
    return df_safe


def fetch_all_kobo_submissions(kobo_server, form_uid, api_token, request_timeout=30, page_limit=1000):
    headers = {"Authorization": f"Token {api_token}"}
    results = []
    page_count = 0
    url = f"{kobo_server}/api/v2/assets/{form_uid}/data.json"
    params = {"limit": page_limit}

    while url:
        page_count += 1
        response = requests.get(url, headers=headers, params=params, timeout=request_timeout)
        if response.status_code != 200:
            raise ValueError(f"API Error {response.status_code}: {response.text}")

        payload = response.json()
        batch = payload.get("results", [])
        results.extend(batch)

        next_url = payload.get("next")
        if not next_url:
            break

        if next_url.startswith("/"):
            url = urljoin(kobo_server.rstrip("/") + "/", next_url.lstrip("/"))
        else:
            url = next_url
        params = None

    return results, page_count

# --- DATA SOURCE SELECTION ---
data_source = st.radio(
    "Choose your data source:", 
    ["File Upload", "KoboToolbox", "SQL Database"], 
    horizontal=True,
    key='data_source_radio'
)

df = None
file_extension = "csv"
output_filename = "cleaned_data.csv"
form_uid = ""
api_token = ""
kobo_server = "https://kf.kobotoolbox.org"

# Reset data when switching sources safely
if st.session_state['data_source'] != data_source:
    st.session_state['data_source'] = data_source
    st.session_state['all_uploaded_tables'] = None
    st.session_state['kobo_df'] = None
    st.session_state['db_df'] = None
    st.session_state['selected_sheet'] = None

# ==========================================
# SOURCE 1: FILE UPLOAD
# ==========================================
if data_source == "File Upload":
    uploaded_file = st.file_uploader(
        "Choose a file", 
        type=["csv", "xlsx", "parquet"], 
        help="Supported formats: CSV, Excel (.xlsx), Parquet"
    )
    
    if uploaded_file is not None:
        file_extension = uploaded_file.name.split(".")[-1].lower()
        
        try:
            with st.spinner("Reading file..."):
                if file_extension == "csv":
                    raw_preview = uploaded_file.read(20000)
                    detection = detect(raw_preview)
                    encoding_guess = detection["encoding"] or "utf-8"
                    uploaded_file.seek(0)
                    single_df = pd.read_csv(uploaded_file, encoding=encoding_guess)
                    st.session_state['all_uploaded_tables'] = {"CSV_Data": single_df}
                
                elif file_extension == "parquet":
                    single_df = pd.read_parquet(uploaded_file)
                    st.session_state['all_uploaded_tables'] = {"Parquet_Data": single_df}
                
                elif file_extension == "xlsx":
                    st.session_state['all_uploaded_tables'] = pd.read_excel(uploaded_file, sheet_name=None)
                
                if st.session_state['all_uploaded_tables']:
                    sheet_names = list(st.session_state['all_uploaded_tables'].keys())
                    st.success(f"✅ Successfully scanned file. Found {len(sheet_names)} table(s).")
                else:
                    st.error("❌ No valid tables loaded from file.")
        except Exception as e:
            st.error(f"❌ Error reading file: {e}")
            st.session_state['all_uploaded_tables'] = None

    if st.session_state['all_uploaded_tables'] is not None:
        available_sheets = list(st.session_state['all_uploaded_tables'].keys())
        selected_sheet = st.selectbox("🎯 Select which sheet/table to clean:", options=available_sheets, key="sheet_selector")
        
        df = st.session_state['all_uploaded_tables'][selected_sheet]
        output_filename = f"cleaned_{selected_sheet}_{uploaded_file.name if uploaded_file else 'data.csv'}"
        if not output_filename.endswith(".csv"):
            output_filename = output_filename.split(".")[0] + ".csv"

# ==========================================
# SOURCE 2: KOBOTOOLBOX
# ==========================================
elif data_source == "KoboToolbox":
    st.markdown("### 🔑 KoboToolbox Configuration")
    col1, col2 = st.columns(2)
    with col1:
        form_uid = st.text_input("Form UID (Asset UID)", placeholder="e.g., input your Form UID", key="form_uid").strip()
    with col2:
        api_token = st.text_input("API Token", type="password", placeholder="Paste your Kobo Secret Token", key="api_token").strip()
    
    col3, col4 = st.columns(2)
    with col3:
        kobo_server = st.text_input("KoboToolbox Server URL", value="https://kf.kobotoolbox.org", key="kobo_server").strip().rstrip('/')
    with col4:
        st.markdown("<br>", unsafe_allow_html=True)
        pull_data = st.button("🔄 Pull Live Data from KoboToolbox", type="primary", use_container_width=True)
    
    if pull_data:
        if not form_uid or not api_token:
            st.error("⚠️ Please enter both your Form UID and API Token.")
        else:
            try:
                with st.spinner("📡 Streaming Kobo pages into RAM..."):
                    results, page_count = fetch_all_kobo_submissions(kobo_server, form_uid, api_token)
                    if results:
                        st.session_state['kobo_df'] = pd.json_normalize(results)
                        st.success(f"✅ Loaded {len(results):,} submissions across {page_count} page(s)!")
                        st.rerun()
                    else:
                        st.warning("⚠️ Form contains 0 submissions.")
                        st.session_state['kobo_df'] = None
            except Exception as e:
                st.error(f"❌ Unexpected error: {str(e)}")
    
    if st.session_state['kobo_df'] is not None:
        df = st.session_state['kobo_df']
        output_filename = f"cleaned_kobo_{form_uid}.csv"

# ===========================================
# SOURCE 3: SQL DATABASE
# ===========================================
else:
    st.markdown("### 🗄️ SQL Database Connection")
    
    db_type = st.selectbox(
        "Database System Type", 
        ["SQLite", "PostgreSQL", "MySQL", "SQL Server (MS SQL)"]
    )
    db_uri = None
    
    if db_type == "SQLite":
        db_path = st.text_input("Database File Path", placeholder="example.db", help="Use ':memory:' for an in-memory testing DB.")
        if db_path:
            db_uri = f"sqlite:///{db_path}" if db_path != ":memory:" else "sqlite:///:memory:"
    else:
        col_srv, col_db = st.columns(2)
        with col_srv:
            server = st.text_input("Server", placeholder="Give Your Server Name or host", help="Use host or host\\instance format.")
        with col_db:
            port = st.text_input("Port (optional)", placeholder="1433", help="Leave blank to use the default SQL Server port.")

        database = st.text_input("Database", placeholder="my_database_name")
        auth_type = "Database Authentication"
        if db_type == "SQL Server (MS SQL)":
            auth_type = st.radio("Authentication Method", ["Windows Authentication", "Database Authentication"], horizontal=True)
            if auth_type == "Windows Authentication":
                st.info("Windows Authentication may not work from cloud hosts. Use Database Authentication when deploying on Streamlit Cloud or Linux.")

        username = ""
        password = ""
        if auth_type == "Database Authentication":
            col_usr, col_pwd = st.columns(2)
            with col_usr:
                username = st.text_input("Username")
            with col_pwd:
                password = st.text_input("Password", type="password")

        if server and database:
            from urllib import parse as urllib_parse
            connection_server = server
            if port:
                connection_server = f"{server},{port}"

            if db_type == "PostgreSQL":
                db_uri = f"postgresql://{username}:{password}@{server}/{database}"

            elif db_type == "MySQL":
                db_uri = f"mysql+pymysql://{username}:{password}@{server}/{database}"

            elif db_type == "SQL Server (MS SQL)":
                if auth_type == "Windows Authentication":
                    params = urllib_parse.quote_plus(
                        f"DRIVER={{ODBC Driver 17 for SQL Server}};"
                        f"SERVER={connection_server};"
                        f"DATABASE={database};"
                        f"Trusted_Connection=yes;"
                        f"TrustServerCertificate=yes;"
                        f"Connect Timeout=15;"
                    )
                else:
                    params = urllib_parse.quote_plus(
                        f"DRIVER={{ODBC Driver 17 for SQL Server}};"
                        f"SERVER={connection_server};"
                        f"DATABASE={database};"
                        f"UID={username};"
                        f"PWD={password};"
                        f"TrustServerCertificate=yes;"
                        f"Connect Timeout=15;"
                    )
                db_uri = f"mssql+pyodbc:///?odbc_connect={params}"

    # --- CONNECTION LOGIC ---
    col_btn = st.columns([3, 1])
    with col_btn[1]:
        st.markdown("<br>", unsafe_allow_html=True)
        connect_db = st.button("🔌 Connect", type="secondary", width="stretch")
        
    if db_uri and (connect_db or st.session_state.get('db_tables') is not None):
        try:
            engine = create_engine(db_uri)
            inspector = inspect(engine)
            tables = inspector.get_table_names()
            st.session_state['db_tables'] = tables
            st.session_state['db_engine_uri'] = db_uri
        except Exception as e:
            st.error(f"❌ Connection failed: Please check your credentials or server status.\n\nDetails: {e}")
            st.session_state['db_tables'] = None

    # --- NAVIGATOR / TABLE SELECTOR ---
    if st.session_state.get('db_tables'):
        st.success("✅ Connected successfully!")
        
        col3, col4 = st.columns([3, 1])
        with col3:
            selected_db_table = st.selectbox("🎯 Select Table (Navigator):", options=st.session_state['db_tables'])
        with col4:
            st.markdown("<br>", unsafe_allow_html=True)
            fetch_db_data = st.button("📥 Load Data", type="primary", width="stretch")
            
        if fetch_db_data:
            try:
                engine = create_engine(st.session_state['db_engine_uri'])
                with st.spinner(f"Loading `{selected_db_table}` into data model..."):
                    st.session_state['db_df'] = pd.read_sql_table(selected_db_table, con=engine)
                    st.session_state['active_db_table_name'] = selected_db_table
                    st.success(f"✅ Loaded {len(st.session_state['db_df']):,} rows!")
                    st.rerun()
            except Exception as e:
                st.error(f"❌ Failed to load table: {e}")

    if st.session_state.get('db_df') is not None:
        df = st.session_state['db_df']
        t_name = st.session_state.get('active_db_table_name', 'db_table')
        output_filename = f"cleaned_db_{t_name}.csv"

# --- CORE TRANSFORMATION & PIPELINE ENGINE ---
if df is not None:
    st.markdown("---")
    
    # -------------------------------------------------
    # SIDEBAR CONTROL PIPELINE
    # -------------------------------------------------
    st.sidebar.header("Pipeline Configuration")
    st.sidebar.markdown("Select processes to apply:")
    
    show_shape = st.sidebar.checkbox("Show Data Dimensions (Shape)", value=True)
    do_drop_cols = st.sidebar.checkbox("Drop Columns", value=False)
    do_rename_cols = st.sidebar.checkbox("Rename Columns", value=False)
    do_data_types = st.sidebar.checkbox("Deal with Data Types", value=False)
    do_missing_values = st.sidebar.checkbox("Handle Missing Values", value=False)
    show_describe = st.sidebar.checkbox("Show Summary Statistics (Describe)", value=False)

    # Base operational dataframe copies
    cleaned_df = df.copy()
    all_columns = df.columns.tolist()
    
    # 1. Describing the data
    if show_shape:
        st.write("### Data Dimensions (Shape)")
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Current Rows", f"{cleaned_df.shape[0]:,}")
        col2.metric("Current Columns", cleaned_df.shape[1])
        total_missing = cleaned_df.isnull().sum().sum()
        col3.metric("Total Missing Values", f"{total_missing:,}")
        try:
            duplicate_rows = make_hashable(cleaned_df).duplicated().sum()
        except Exception:
            duplicate_rows = "N/A"
        col4.metric("Duplicate Rows", f"{duplicate_rows:,}")

    # 2. Dropping Columns
    columns_to_drop = []
    if do_drop_cols:
        st.write("### ✂️ Select Columns to Remove")
        columns_to_drop = st.multiselect("Select columns to REMOVE:", options=all_columns, key=f"dropper_{st.session_state.get('sheet_selector', 'default')}")
        cleaned_df = cleaned_df.drop(columns=[c for c in columns_to_drop if c in cleaned_df.columns])

    remaining_cols_after_drop = [c for c in all_columns if c not in columns_to_drop]

    # 3. Renaming Columns
    columns_to_rename = {}
    if do_rename_cols and remaining_cols_after_drop:
        st.write("### ✏️ Rename Columns")
        with st.expander("Configure Column Map Re-labeling", expanded=False):
            r_cols = st.columns(2)
            for idx, col in enumerate(remaining_cols_after_drop):
                new_name = r_cols[idx % 2].text_input(f"Original: `{col}`", value="", key=f"ren_{st.session_state.get('sheet_selector', 'default')}_{col}").strip()
                if new_name:
                    columns_to_rename[col] = new_name
        cleaned_df = cleaned_df.rename(columns=columns_to_rename)

    # 4. Dealing with Data types
    type_mapping_dict = {}
    if do_data_types and remaining_cols_after_drop:
        st.write("###  Deal with Data Types")
        with st.expander("Deal with Data Types", expanded=False):
            t_cols = st.columns(3)
            for idx, col in enumerate(remaining_cols_after_drop):
                cur_name = columns_to_rename.get(col, col)
                if cur_name in cleaned_df.columns:
                    original_dtype = str(cleaned_df[cur_name].dtype)
                    type_options = [f"({original_dtype})", "String", "Integer", "Float", "DateTime", "Boolean"]
                    
                    # --- AUTOMATIC NUMBER DETECTION LOGIC ---
                    is_numeric = False
                    if "int" in original_dtype or "float" in original_dtype:
                        is_numeric = True
                    else:
                        non_null_samples = cleaned_df[cur_name].dropna().head(100)
                        if not non_null_samples.empty:
                            converted = pd.to_numeric(non_null_samples, errors='coerce')
                            if converted.notnull().sum() / len(non_null_samples) > 0.5:
                                is_numeric = True
                    
                    default_idx = 2 if is_numeric else 0
                    # ----------------------------------------

                    chosen_type = t_cols[idx % 3].selectbox(
                        f"Type for `{cur_name}` (original: {original_dtype}):", 
                        type_options,
                        index=default_idx,
                        key=f"type_{st.session_state.get('sheet_selector', 'default')}_{col}"
                    )
                    
                    if chosen_type != type_options[0]:
                        type_mapping_dict[cur_name] = chosen_type
                        try:
                            if chosen_type == "String":
                                cleaned_df[cur_name] = cleaned_df[cur_name].astype(str)
                            elif chosen_type == "Integer":
                                cleaned_df[cur_name] = pd.to_numeric(cleaned_df[cur_name], errors='coerce').fillna(0).astype(int)
                            elif chosen_type == "Float":
                                cleaned_df[cur_name] = pd.to_numeric(cleaned_df[cur_name], errors='coerce')
                            elif chosen_type == "DateTime":
                                cleaned_df[cur_name] = pd.to_datetime(cleaned_df[cur_name], errors='coerce')
                            elif chosen_type == "Boolean":
                                cleaned_df[cur_name] = cleaned_df[cur_name].astype(bool)
                        except Exception as e:
                            st.warning(f"Failed casting `{cur_name}` to {chosen_type}: {e}")

    # 5. Automatic Removal of Duplicates
    try:
        before_rows = cleaned_df.shape[0]
        cleaned_df = cleaned_df[~make_hashable(cleaned_df).duplicated()].reset_index(drop=True)
        removed_duplicates = before_rows - cleaned_df.shape[0]
        if removed_duplicates > 0:
            st.success(f"Removed {removed_duplicates:,} duplicate row(s) from your data.")
    except:
        pass

    # 6. Handling Missing Values
    na_strategy = "None"
    na_selected_cols = []
    if do_missing_values and remaining_cols_after_drop:
        st.write("### 🩹 Handling Missing Values")
        with st.expander("Dealing With Null Values", expanded=False):
            m_col1, m_col2 = st.columns(2)
            na_strategy = m_col1.selectbox(
                "Choose Strategy:", 
                ["Drop Rows with Any Missing Data", "Fill with Mean", "Fill with Median", "Fill with Mode", "Fill with Zero", "Forward Fill", "Backward Fill"],
                key=f"na_strat_{st.session_state.get('sheet_selector', 'default')}"
            )
            current_working_cols = [columns_to_rename.get(c, c) for c in remaining_cols_after_drop]
            current_working_cols = [c for c in current_working_cols if c in cleaned_df.columns]
            na_selected_cols = m_col2.multiselect("Apply to Specific Columns:", options=current_working_cols, default=current_working_cols, key=f"na_sel_{st.session_state.get('sheet_selector', 'default')}")
            
            if na_selected_cols:
                for col in na_selected_cols:
                    if cleaned_df[col].dtype == 'object':
                        cleaned_df[col] = cleaned_df[col].astype(str).str.strip().replace({'': None, 'None': None, 'nan': None, 'NaN': None})

                if na_strategy == "Drop Rows with Any Missing Data":
                    cleaned_df = cleaned_df.dropna(subset=na_selected_cols)
                elif na_strategy == "Fill with Zero":
                    cleaned_df[na_selected_cols] = cleaned_df[na_selected_cols].fillna(0)
                elif na_strategy == "Forward Fill":
                    cleaned_df[na_selected_cols] = cleaned_df[na_selected_cols].ffill()
                elif na_strategy == "Backward Fill":
                    cleaned_df[na_selected_cols] = cleaned_df[na_selected_cols].bfill()
                else:
                    for col in na_selected_cols:
                        numeric_series = pd.to_numeric(cleaned_df[col], errors='coerce')
                        if na_strategy == "Fill with Mean":
                            val = numeric_series.mean()
                            cleaned_df[col] = cleaned_df[col].fillna(val if not pd.isna(val) else 0)
                        elif na_strategy == "Fill with Median":
                            val = numeric_series.median()
                            cleaned_df[col] = cleaned_df[col].fillna(val if not pd.isna(val) else 0)
                        elif na_strategy == "Fill with Mode":
                            mode_res = cleaned_df[col].mode()
                            cleaned_df[col] = cleaned_df[col].fillna(mode_res[0] if not mode_res.empty else "")

    # 7. Summary Statistics
    if show_describe:
        st.write("### 📊 Summary Statistics")
        st.dataframe(cleaned_df.describe(), width='stretch')

    # Output Data Preview Panels
    st.write("### Preview Of Your Data")
    st.dataframe(cleaned_df.head(15), width='stretch')

    # =====================================================
    # INTERACTIVE VISUALIZATION WITH PLOTLY
    # =====================================================
    st.markdown("---")
    st.write("### 📊 Interactive Data Visualization Suite")

    numeric_cols = cleaned_df.select_dtypes(include=['number']).columns.tolist()
    categorical_cols = cleaned_df.select_dtypes(include=['object', 'category', 'string']).columns.tolist()
    all_viz_cols = cleaned_df.columns.tolist()

    agg_map = {"Average": "mean", "Sum": "sum", "Median": "median", "Max": "max", "Min": "min", "Count": "count"}

    viz_type = st.selectbox(
        "Choose Chart Type:",
        ["Bar Chart", "Pie Chart", "Line Chart", "Word Cloud"]
    )

    with st.expander(f"Configure {viz_type} Parameters", expanded=True):
        fig = None

        # --- BAR CHART ---
        if viz_type == "Bar Chart":
            if not categorical_cols:
                st.info("💡 Need at least one categorical column to anchor bars.")
            else:
                v_col1, v_col2 = st.columns(2)
                x_axis = v_col1.selectbox("X Axis (Category):", options=categorical_cols, key="bar_x")
                y_axis = v_col2.selectbox("Y Axis (Value):", options=["Row Count"] + numeric_cols, key="bar_y")

                if y_axis == "Row Count":
                    bar_data = cleaned_df[x_axis].value_counts().reset_index()
                    bar_data.columns = [x_axis, "Row Count"]
                    fig = px.bar(bar_data, x=x_axis, y="Row Count", title=f"Distribution of {x_axis}", template="plotly_white")
                else:
                    agg_func = st.radio(
                        "Aggregation:",
                        ["Average", "Sum", "Median", "Max", "Min"],
                        horizontal=True,
                        key="bar_agg"
                    )
                    bar_data = cleaned_df.groupby([x_axis], as_index=False)[y_axis].agg(agg_map[agg_func])
                    fig = px.bar(
                        bar_data, x=x_axis, y=y_axis,
                        title=f"{agg_func} {y_axis} by {x_axis}"
                    )

        # --- PIE CHART ---
        elif viz_type == "Pie Chart":
            if not categorical_cols:
                st.info("💡 Need at least one categorical column to construct segments.")
            else:
                v_col1, v_col2 = st.columns(2)
                names_col = v_col1.selectbox("Slices (Category Column):", options=categorical_cols, key="pie_names")
                values_col = v_col2.selectbox("Slice Proportions (Numeric Column):", options=["Row Count Summary"] + numeric_cols, key="pie_values")

                if values_col == "Row Count Summary":
                    pie_data = cleaned_df[names_col].value_counts().reset_index()
                    pie_data.columns = [names_col, "Count"]
                    fig = px.pie(pie_data, names=names_col, values="Count", title=f"Proportional Breakdown of {names_col}")
                else:
                    agg_func = st.radio(
                        "Aggregation:",
                        ["Sum", "Average", "Median", "Max", "Min"],
                        horizontal=True,
                        key="pie_agg"
                    )
                    pie_data = cleaned_df.groupby(names_col, as_index=False)[values_col].agg(agg_map[agg_func])
                    fig = px.pie(
                        pie_data, names=names_col, values=values_col,
                        title=f"{agg_func} {values_col} across {names_col}"
                    )

        # --- LINE CHART ---
        elif viz_type == "Line Chart":
            if not all_viz_cols or not numeric_cols:
                st.info(" Need at least one numeric column to chart.")
            else:
                v_col1, v_col2 = st.columns(2)
                x_axis = v_col1.selectbox("X Axis (Timeline/Index):", options=all_viz_cols, key="line_x")
                y_axis = v_col2.selectbox("Y Axis (Numeric Value):", options=numeric_cols, key="line_y")

                if y_axis:
                    agg_func = st.radio(
                        "Aggregation:",
                        ["Average", "Sum", "Median", "Max", "Min"],
                        horizontal=True,
                        key="line_agg"
                    )
                    line_data = (
                        cleaned_df.groupby([x_axis], as_index=False)[y_axis]
                        .agg(agg_map[agg_func])
                        .sort_values(x_axis)
                    )
                    fig = px.line(
                        line_data, x=x_axis, y=y_axis,
                        title=f"{agg_func} {y_axis} Trend over {x_axis}", template="plotly_white"
                    )
        ##--Word Cloud--
        elif viz_type == "Word Cloud":
            if not WORDCLOUD_AVAILABLE:
                st.warning("The optional `wordcloud` package is not installed. Install it to enable Word Cloud visualization.")
            elif not categorical_cols:
                st.info("We need at least one text/categorical values to generate a Wordcloud")
            else:
                v_col1, v_col2 = st.columns(2)
                text_col = v_col1.selectbox("Select the text column", options=categorical_cols, key="wc_text")
                bg_color = v_col2.selectbox("Background colors", options=['Black', 'White'], key="wc_bg")
                text_data = " ".join(cleaned_df[text_col].dropna().astype(str))
                if not text_data.strip():
                    st.warning("Selected column has no text data to generate a cloud")
                else:
                    with st.spinner("Generating wordcloud..."):
                        wordcloud = WordCloud(
                            width=800,
                            height=400,
                            background_color=bg_color.lower(),
                            collocations=False
                        ).generate(text_data)

                        fig, ax = plt.subplots(figsize=(10, 5))
                        ax.imshow(wordcloud, interpolation='bilinear')
                        ax.axis("off")
                        plt.tight_layout(pad=0)
                        st.pyplot(fig)

    if fig is not None and viz_type != "Word Cloud":
        st.plotly_chart(fig, width="stretch")

    # =============================
    # LIVE POWER BI PYTHON SCRIPT
    # =============================
    if data_source == "KoboToolbox" and form_uid and api_token:
        st.write("### 📊 Import Directly to Power BI")
        pbi_script = f"""import requests
import pandas as pd
import numpy as np
from urllib.parse import urljoin

# 1. Fetch live data from KoboToolbox API with pagination
headers = {{"Authorization": "Token {api_token}"}}
url = "{kobo_server}/api/v2/assets/{form_uid}/data.json"
params = {{"limit": 1000}}
all_results = []

while url:
    response = requests.get(url, headers=headers, params=params, timeout=45)
    response.raise_for_status()
    payload = response.json()
    all_results.extend(payload.get("results", []))
    next_url = payload.get("next")
    if not next_url:
        break
    if next_url.startswith("/"):
        url = urljoin("{kobo_server.rstrip('/')}/", next_url.lstrip("/"))
    else:
        url = next_url
    params = None

# 2. Build and flatten the DataFrame
df = pd.json_normalize(all_results)
"""
        step = 3
        if do_drop_cols and columns_to_drop:
            pbi_script += f"\n# {step}. Drop columns\ncolumns_to_drop = {str(list(columns_to_drop))}\ndf = df.drop(columns=[col for col in columns_to_drop if col in df.columns])\n"
            step += 1

        if do_rename_cols and columns_to_rename:
            pbi_script += f"\n# {step}. Rename columns\ncolumns_to_rename = {str(columns_to_rename)}\ndf = df.rename(columns=columns_to_rename)\n"
            step += 1

        if type_mapping_dict:
            pbi_script += f"\n# {step}. Cast Data Types\ntype_rules = {str(type_mapping_dict)}\nfor col, target_type in type_rules.items():\n    if col in df.columns:\n        if target_type == 'String': df[col] = df[col].astype(str)\n        elif target_type == 'Integer': df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0).astype(int)\n        elif target_type == 'Float': df[col] = pd.to_numeric(df[col], errors='coerce')\n        elif target_type == 'DateTime': df[col] = pd.to_datetime(df[col], errors='coerce')\n        elif target_type == 'Boolean': df[col] = df[col].astype(bool)\n"
            step += 1

        pbi_script += f"\n# {step}. Remove duplicate rows safely\ndf_safe = df.copy()\nfor col in df_safe.columns:\n    if len(df_safe[col].dropna()) > 0 and isinstance(df_safe[col].dropna().iloc[0], (list, dict, set)):\n        df_safe[col] = df_safe[col].astype(str)\ndf = df[~df_safe.duplicated()].reset_index(drop=True)\ndel df_safe\n"
        step += 1

        if do_missing_values and na_selected_cols:
            pbi_script += f"\n# {step}. Handle Missing values ({na_strategy})\ntarget_cols = {str(na_selected_cols)}\nfor col in target_cols:\n    if df[col].dtype == 'object':\n        df[col] = df[col].astype(str).str.strip().replace({{ '':'None', 'None':None, 'nan':None, 'NaN':None }})\n"
            
            if na_strategy == "Drop Rows with Any Missing Data":
                pbi_script += "df = df.dropna(subset=target_cols)\n"
            elif na_strategy == "Fill with Zero":
                pbi_script += "df[target_cols] = df[target_cols].fillna(0)\n"
            elif na_strategy == "Forward Fill":
                pbi_script += "df[target_cols] = df[target_cols].ffill()\n"
            elif na_strategy == "Backward Fill":
                pbi_script += "df[target_cols] = df[target_cols].bfill()\n"
            else:
                pbi_script += "for col in target_cols:\n"
                if na_strategy == "Fill with Mean":
                    pbi_script += "    df[col] = df[col].fillna(pd.to_numeric(df[col], errors='coerce').mean())\n"
                elif na_strategy == "Fill with Median":
                    pbi_script += "    df[col] = df[col].fillna(pd.to_numeric(df[col], errors='coerce').median())\n"
                elif na_strategy == "Fill with Mode":
                    pbi_script += "    m = df[col].mode(); df[col] = df[col].fillna(m[0] if not m.empty else '')\n"
            step += 1

        pbi_script += f"\n# {step}. Stringify structural JSON arrays for Power BI compatibility\nfor col in df.columns:\n    if len(df[col].dropna()) > 0 and isinstance(df[col].dropna().iloc[0], (list, dict, set)):\n        df[col] = df[col].astype(str)\n"

        st.markdown("> Copy the Python script block below. Power BI will execute this cleanly without showing `df_safe` artifacts.")
        st.code(pbi_script, language="python")
    else:
        pass

    st.markdown("---")

    # Download Output Package
    st.write("### 📥 Download Cleaned Output Package")
    try:
        buffer = io.BytesIO()
        cleaned_df.to_csv(buffer, index=False)
        buffer.seek(0)
        st.download_button(
            label=f"📥 Download Cleaned Sheet ({st.session_state.get('sheet_selector', 'Data')})",
            data=buffer,
            file_name=output_filename,
            mime="text/csv",
            type="primary"
        )
    except Exception as e:
        st.error(f"❌ Error generating payload binary package: {str(e)}")
else:
    st.info("👆 Please upload a data asset source file, pass active KoboToolbox keys, or stream from a SQL Database engine to compute operational fields.")