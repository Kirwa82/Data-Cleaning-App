import streamlit as st
import pandas as pd
import io
import requests
from charset_normalizer import detect

# ==============================================================================
# WEB USER INTERFACE (STREAMLIT APP RENDER)
# ==============================================================================
st.set_page_config(page_title="Custom Data Cleaning Pipeline", layout="wide", page_icon="🧼")

st.title("🧼 Custom Data Cleaning Pipeline")
st.markdown("#### Build a modular data cleaning pipeline. **Your data remains completely in RAM and is never saved to a disk.**")

# Initialize session state tracking
if 'kobo_df' not in st.session_state:
    st.session_state['kobo_df'] = None
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

# --- DATA SOURCE SELECTION ---
data_source = st.radio(
    "Choose your data source:", 
    ["File Upload", "KoboToolbox"], 
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
    st.session_state['selected_sheet'] = None

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

else:
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
            kobo_url = f"{kobo_server}/api/v2/assets/{form_uid}/data.json"
            headers = {"Authorization": f"Token {api_token}"}
            try:
                with st.spinner("📡 Streaming data straight to RAM..."):
                    response = requests.get(kobo_url, headers=headers, timeout=30)
                    if response.status_code == 200:
                        results = response.json().get("results", [])
                        if results:
                            st.session_state['kobo_df'] = pd.json_normalize(results)
                            st.success(f"✅ Loaded {len(results):,} records!")
                            st.rerun()
                        else:
                            st.warning("⚠️ Form contains 0 submissions.")
                            st.session_state['kobo_df'] = None
                    else:
                        st.error(f"❌ API Error {response.status_code}")
            except Exception as e:
                st.error(f"❌ Unexpected error: {str(e)}")
    
    if st.session_state['kobo_df'] is not None:
        df = st.session_state['kobo_df']
        output_filename = f"cleaned_kobo_{form_uid}.csv"

# --- CORE TRANSFORMATION & PIPELINE ENGINE ---
if df is not None:
    st.markdown("---")
    
    # -------------------------------------------------
    # SIDEBAR CONTROL PIPELINE
    # -------------------------------------------------
    st.sidebar.header("Pipeline Configuration")
    st.sidebar.markdown("Select processes to apply:")
    
    show_shape = st.sidebar.checkbox("Show Data Dimensions (Shape)", value=True)
    show_describe = st.sidebar.checkbox("Show Summary Statistics (Describe)", value=False)
    do_drop_cols = st.sidebar.checkbox("Drop Columns", value=False)
    do_rename_cols = st.sidebar.checkbox("Rename Columns", value=False)
    do_data_types = st.sidebar.checkbox("Deal with Data Types", value=False)
    do_missing_values = st.sidebar.checkbox("Handle Missing Values", value=False)

    # Base operational dataframe copies
    cleaned_df = df.copy()
    all_columns = df.columns.tolist()
    
    # 1. PROCESS: SHAPE
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

    # 2. PROCESS: DESCRIBE SUMMARY
    if show_describe:
        st.write("### 📊 Summary Statistics")
        st.dataframe(cleaned_df.describe(include='all').astype(str).fillna("-"), use_container_width=True)

    # 3. INTERACTIVE CONFIGURATIONS
    columns_to_drop = []
    if do_drop_cols:
        st.write("### ✂️ Select Columns to Remove")
        columns_to_drop = st.multiselect("Select columns to REMOVE:", options=all_columns, key=f"dropper_{st.session_state.get('sheet_selector', 'default')}")
        cleaned_df = cleaned_df.drop(columns=[c for c in columns_to_drop if c in cleaned_df.columns])

    remaining_cols_after_drop = [c for c in all_columns if c not in columns_to_drop]

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

    type_mapping_dict = {}
    if do_data_types and remaining_cols_after_drop:
        st.write("### 🔡 Deal with Data Types")
        with st.expander("Modify Column Field Class Types", expanded=False):
            t_cols = st.columns(3)
            for idx, col in enumerate(remaining_cols_after_drop):
                cur_name = columns_to_rename.get(col, col)
                if cur_name in cleaned_df.columns:
                    chosen_type = t_cols[idx % 3].selectbox(
                        f"Type for `{cur_name}`:", 
                        ["Keep Original", "String", "Integer", "Float", "DateTime", "Boolean"], 
                        key=f"type_{st.session_state.get('sheet_selector', 'default')}_{col}"
                    )
                    if chosen_type != "Keep Original":
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

    # Automatically remove duplicate rows for every pipeline run
    try:
        before_rows = cleaned_df.shape[0]
        cleaned_df = cleaned_df[~make_hashable(cleaned_df).duplicated()].reset_index(drop=True)
        removed_duplicates = before_rows - cleaned_df.shape[0]
        if removed_duplicates > 0:
            st.success(f"Removed {removed_duplicates:,} duplicate row(s) from your data.")
    except:
        pass

    na_strategy = "None"
    na_selected_cols = []
    if do_missing_values and remaining_cols_after_drop:
        st.write("### 🩹 Handle Missing Values")
        with st.expander("Configure Null/NA Imputation Strategies", expanded=False):
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

    # Output Data Preview Panels
    st.write("### Preview Of Your Data")
    st.dataframe(cleaned_df.head(15), use_container_width=True)
    st.markdown("---")

    # =====================================================
    # LIVE POWER BI DIRECT PYTHON INTEGRATION
    # =====================================================
    st.write("### 📊 Import Directly to Power BI")
    if data_source == "KoboToolbox" and form_uid and api_token:
        pbi_script = f"""import requests
import pandas as pd
import numpy as np

# 1. Fetch live data from KoboToolbox API
url = "{kobo_server}/api/v2/assets/{form_uid}/data.json"
headers = {{"Authorization": "Token {api_token}"}}

response = requests.get(url, headers=headers, timeout=45)
results = response.json().get("results", [])

# 2. Build and flatten the DataFrame
df = pd.json_normalize(results)
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
        st.warning("⚠️ Power BI live script configurations are only active during structural KoboToolbox data cloud feeds.")

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
    st.info("👆 Please upload a data asset source file or feed active KoboToolbox keys to compute operational fields.")