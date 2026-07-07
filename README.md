# 🧼 Custom Data Cleaning Pipeline — User Guide

A Streamlit app for cleaning and visualizing data from **file uploads**, **KoboToolbox**, or a **SQL Database** — all in memory, with nothing written to disk.

---

## 1. Getting Started

### 1.1 Create a Virtual Environment

It's best practice to run this app in an isolated virtual environment so its dependencies don't conflict with other projects.

**Windows:**
```bash
python -m venv venv
venv\Scripts\activate
```

**macOS / Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

You'll know it worked if you see `(venv)` appear at the start of your terminal prompt.

### 1.2 Install Requirements

Create a file named `requirements.txt` in the same folder as the app with the following contents:

```
streamlit
pandas
requests
charset-normalizer
sqlalchemy
plotly
wordcloud
matplotlib
openpyxl
pyarrow
```

Then, depending on which SQL databases you plan to connect to, also add the relevant driver:

```
psycopg2-binary      # for PostgreSQL
pymysql               # for MySQL
pyodbc                # for SQL Server (MS SQL)
```

Install everything with:
```bash
pip install -r requirements.txt
```

> 💡 If you only plan to use File Upload or KoboToolbox (no SQL Server), you can skip `pyodbc` — but note the SQL Server driver software (**ODBC Driver 17 for SQL Server**) must also be installed separately on your machine; `pip install pyodbc` alone is not enough.

### 1.3 Run the App

```bash
streamlit run app.py
```

Your browser will open the app automatically (usually at `http://localhost:8501`).

> Replace `app.py` with the actual filename of the script if it's named differently.

### 1.4 Deactivating the Environment

When you're done, you can exit the virtual environment with:
```bash
deactivate
```

---

## 2. Step 1 — Choose Your Data Source

At the top of the app, pick one of three options:

| Option | Use When |
|---|---|
| **File Upload** | You have a CSV, Excel (.xlsx), or Parquet file on your computer |
| **KoboToolbox** | You want to pull live survey submissions from a KoboToolbox form |
| **SQL Database** | You want to connect directly to SQLite, PostgreSQL, MySQL, or SQL Server |

Switching sources will clear any previously loaded data.

---

## 3. Loading Your Data

### Option A: File Upload
1. Click **"Choose a file"** and select a `.csv`, `.xlsx`, or `.parquet` file.
2. The app auto-detects file encoding (for CSVs) and reads all sheets (for Excel).
3. If your Excel file has multiple sheets, use the **"Select which sheet/table to clean"** dropdown to pick one.

### Option B: KoboToolbox
1. Enter your **Form UID** (the unique ID of your Kobo form/asset).
2. Enter your **API Token** (kept hidden — treated as a password field).
3. Confirm or edit the **KoboToolbox Server URL** (default: `https://kf.kobotoolbox.org`).
4. Click **"🔄 Pull Live Data from KoboToolbox"**.
5. The app pages through all submissions automatically and loads them into memory.

> ⚠️ Your Form UID and API Token are never saved or displayed anywhere in the output — keep them private and don't share screenshots that include them.

### Option C: SQL Database
1. Select your **Database System Type**: SQLite, PostgreSQL, MySQL, or SQL Server.
2. Fill in the connection details:
   - **SQLite**: just a file path (or `:memory:` for a temporary test database).
   - **PostgreSQL / MySQL**: server, port (optional), database name, username, password.
   - **SQL Server**: choose **Windows Authentication** or **Database Authentication**.
     - Windows Authentication may not work if the app is hosted on Streamlit Cloud or Linux — use Database Authentication in those cases.
3. Click **"🔌 Connect"**.
4. Once connected, pick a table from the **Navigator** dropdown and click **"📥 Load Data"**.

---

## 4. Step 2 — Configure the Cleaning Pipeline (Sidebar)

Use the checkboxes in the left sidebar to turn cleaning steps on or off:

- **Show Data Dimensions (Shape)** — displays row/column counts, missing values, and duplicate row counts.
- **Drop Columns** — select and remove unwanted columns.
- **Rename Columns** — give columns new, cleaner names.
- **Deal with Data Types** — convert columns to String, Integer, Float, DateTime, or Boolean (the app suggests a likely type automatically).
- **Handle Missing Values** — choose a strategy (see below).
- **Show Summary Statistics (Describe)** — displays basic stats (mean, min, max, etc.) for numeric columns.

Steps run in this order: **Drop → Rename → Data Types → Duplicate Removal → Missing Values**.

---

## 5. Removing Duplicates

This step always runs (no checkbox needed):

1. Choose your strategy:
   - **Entire Row** — flags a row as duplicate only if every column matches another row.
   - **Primary Key / Distinguishing Column(s)** — pick one or more columns (e.g., a submission ID) to define uniqueness.
2. The app automatically ignores differences caused by extra spaces or inconsistent blank/null values (`""`, `"None"`, `"nan"`, `null`, etc.) when checking for duplicates.
3. A success message tells you how many duplicate rows were removed.

---

## 6. Handling Missing Values

If enabled, choose:
1. **Strategy**:
   - Drop Rows with Any Missing Data
   - Fill with Mean / Median / Mode
   - Fill with Zero
   - Forward Fill / Backward Fill
2. **Columns to apply it to** (defaults to all columns).

---

## 7. Previewing Your Data

- The cleaned data preview (first 15 rows) appears under **"Preview Of Your Data"** so you can check your work before downloading.

---

## 8. Visualizing Your Data

Under **"Interactive Data Visualization Suite"**, pick a chart type:

| Chart | What You Need |
|---|---|
| **Bar Chart** | One category column (X-axis), optional numeric column + aggregation (Average, Sum, etc.) |
| **Pie Chart** | One category column for slices, optional numeric column for proportions |
| **Line Chart** | A column for the X-axis (e.g., date/index) and a numeric Y-axis column |
| **Word Cloud** | A text/category column — generates a visual word frequency cloud (choose black or white background) |

Charts update live as you change the dropdowns.

---

## 9. Power BI Integration (KoboToolbox Only)

If your data source is **KoboToolbox**, the app generates a ready-to-use **Python script** under "Import Directly to Power BI." This script:
- Re-fetches your data live from the Kobo API,
- Applies the exact same cleaning steps you configured (drops, renames, type casting, deduplication, missing value handling),
- Is designed to be pasted directly into Power BI's **Python script data source**.

Simply copy the code block and paste it into Power BI.

---

## 10. Downloading Your Cleaned Data

At the bottom of the page, click **"📥 Download Cleaned Sheet"** to save your cleaned dataset as a CSV file to your computer.

---

## 🔒 Privacy Note

This app processes everything **in memory (RAM)** only. Your uploaded files, database credentials, and Kobo tokens are **never saved to disk** and disappear once you close or refresh the app.

---

## Troubleshooting Tips

- **"No valid tables loaded from file"** → Double-check the file format matches its extension (e.g., a renamed `.txt` file won't work as `.csv`).
- **KoboToolbox errors** → Verify your Form UID, API Token, and server URL are correct and that your account has access to the form.
- **SQL connection failed** → Check server name, port, database name, and credentials. For SQL Server, ensure the ODBC Driver 17 is installed on the machine running the app.
- **Word Cloud not showing** → Make sure the selected column actually contains text data (not empty).