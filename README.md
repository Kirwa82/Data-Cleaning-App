# 🧼 Custom Data Cleaning Pipeline

A modular, interactive web application for cleaning and transforming data with a user-friendly interface. Built with Streamlit, this tool allows you to clean data from files or live KoboToolbox API feeds while keeping your data entirely in RAM — nothing is ever saved to disk.

## 📋 Table of Contents

- [Features](#features)
- [Installation](#installation)
- [Usage](#usage)
- [Data Sources](#data-sources)
- [Cleaning Pipeline Features](#cleaning-pipeline-features)
- [Power BI Integration](#power-bi-integration)
- [Security & Privacy](#security--privacy)
- [Dependencies](#dependencies)
- [Contributing](#contributing)

## ✨ Features

- **Dual Data Sources**: Upload files (CSV, Excel, Parquet) or connect to live KoboToolbox API feeds
- **Multi-Sheet Support**: Handle Excel workbooks with multiple sheets/tables
- **Modular Cleaning Pipeline**: Select and configure cleaning operations through an intuitive sidebar
- **Real-time Preview**: See changes instantly as you configure cleaning operations
- **Duplicate Detection**: Automatic removal of duplicate rows
- **Encoding Detection**: Automatic character encoding detection for CSV files
- **Power BI Integration**: Generate Python scripts for direct Power BI data import
- **Privacy-First**: All data processing happens in RAM, no data is written to disk
- **Download Options**: Export cleaned data as CSV files

## 🚀 Installation

### Prerequisites

- Python 3 or higher
- pip (Python package installer)
