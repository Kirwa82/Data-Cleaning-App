import streamlit as st
import pandas as pd
import numpy as np
import io
import plotly.express as px
try:
    from wordcloud import WordCloud
    WORDCLOUD_AVAILABLE = True
except ImportError:
    WORDCLOUD_AVAILABLE = False
import matplotlib.pyplot as plt

# ==============================================================================
# WEB USER INTERFACE (STREAMLIT APP RENDER)
# ==============================================================================
st.set_page_config(page_title="Custom Data Cleaning Pipeline", layout="wide", page_icon="🧼")

st.title("🧼 Custom Data Cleaning Pipeline")
st.markdown("#### This is a modular data cleaning pipeline. **Your data remains completely in RAM and is never saved to a disk.**")

# Initialize session state tracking
if 'all_uploaded_tables' not in st.session_state:
    st.session_state['all_uploaded_tables'] = None
if 'selected_sheet' not in st.session_state:
    st.session_state['selected_sheet'] = None

# Helper function to make DataFrame hashable for duplicate detection
def make_hashable(df):
    df_safe = df.copy()
    for col in df_safe.columns:
        if df_safe[col].apply(lambda x: isinstance(x, (list, dict, set))).any():
            df_safe[col] = df_safe[col].apply(
                lambda x: str(x) if isinstance(x, (list, dict, set)) else x
            )
    return df_safe

# ==========================================
# FILE UPLOAD
# ==========================================
st.write("### 📤 Upload Your Data File")
uploaded_file = st.file_uploader(
    "Choose a file", 
    type=["csv", "xlsx"], 
    help="Supported formats: CSV, Excel (.xlsx)"
)

df = None
output_filename = "cleaned_data.csv"

if uploaded_file is not None:
    file_extension = uploaded_file.name.split(".")[-1].lower()
    
    try:
        with st.spinner("Reading file..."):
            if file_extension == "csv":
                # Simple UTF-8 encoding - no charset detection to avoid timeouts
                single_df = pd.read_csv(uploaded_file, encoding="utf-8", on_bad_lines="skip")
                st.session_state['all_uploaded_tables'] = {"CSV_Data": single_df}
            
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
    
    # Pre-calculate shape duplicate tracking metrics
    try:
        metric_hash_df = make_hashable(cleaned_df)
        for c in metric_hash_df.columns:
            if metric_hash_df[c].dtype == 'object':
                metric_hash_df[c] = metric_hash_df[c].astype(str).str.strip()
            metric_hash_df[c] = metric_hash_df[c].replace(['', 'None', 'nan', 'NaN', 'null', 'NULL'], np.nan).fillna("NA_VAL")
        duplicate_rows = metric_hash_df.duplicated().sum()
    except Exception:
        duplicate_rows = "N/A"

    # 1. Describing the data
    if show_shape:
        st.write("### Data Dimensions (Shape)")
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Current Rows", f"{cleaned_df.shape[0]:,}")
        col2.metric("Current Columns", cleaned_df.shape[1])
        total_missing = cleaned_df.isnull().sum().sum()
        col3.metric("Total Missing Values", f"{total_missing:,}")
        col4.metric("Duplicate Rows", f"{duplicate_rows}")

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
        st.write("### 🔢 Deal with Data Types")
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

    # 5. REMOVAL OF DUPLICATES
    st.write("### 👥 Duplicate Removal Configuration")
    dedup_cols_option = st.radio(
        "Define duplicate evaluation strategy:",
        ["Entire Row (Match across all columns)", "Primary Key / Distinguishing Column(s)"],
        horizontal=True,
        key=f"dedup_choice_{st.session_state.get('sheet_selector', 'default')}"
    )

    dedup_subset = None
    if dedup_cols_option == "Primary Key / Distinguishing Column(s)":
        dedup_subset = st.multiselect(
            "Choose column(s) to isolate unique records (e.g., unique IDs, submission UUIDs):",
            options=cleaned_df.columns.tolist(),
            key=f"dedup_subset_{st.session_state.get('sheet_selector', 'default')}"
        )
        if not dedup_subset:
            st.info("💡 Select one or more columns above. Currently evaluating across the entire row until a selection is made.")

    try:
        before_rows = cleaned_df.shape[0]
        
        hashable_df = make_hashable(cleaned_df)
        
        for col in hashable_df.columns:
            if hashable_df[col].dtype == 'object':
                hashable_df[col] = hashable_df[col].astype(str).str.strip()
            
            hashable_df[col] = hashable_df[col].replace(['', 'None', 'nan', 'NaN', 'null', 'NULL'], np.nan)
            hashable_df[col] = hashable_df[col].fillna("CLEAN_PIPELINE_MARKER_NULL")

        if dedup_subset:
            duplicate_mask = hashable_df.duplicated(subset=dedup_subset)
        else:
            duplicate_mask = hashable_df.duplicated()
            
        cleaned_df = cleaned_df[~duplicate_mask].reset_index(drop=True)
        
        removed_duplicates = before_rows - cleaned_df.shape[0]
        if removed_duplicates > 0:
            st.success(f"Removed {removed_duplicates:,} duplicate row(s) from your data model space.")
    except Exception as e:
        st.sidebar.error(f"Duplicate check failed: {e}")

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
                        cleaned_df[col] = cleaned_df[col].astype(str).str.strip().replace({'': np.nan, 'None': np.nan, 'nan': np.nan, 'NaN': np.nan})

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
        st.dataframe(cleaned_df.describe(), use_container_width=True)

    # Output Data Preview Panels
    st.write("### Preview Of Your Data")
    st.dataframe(cleaned_df.head(15), use_container_width=True)

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
        
        # --- Word Cloud ---
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
        st.plotly_chart(fig, use_container_width=True)

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
    st.info("👆 Please upload a data file to begin cleaning.")
