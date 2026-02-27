🔵 Redfin Property ETL Pipeline
Python • Transactional Data Processing • Spatial Data Engineering

📌 Overview

This project is a production-oriented ETL pipeline built in Python to automate ingestion, transformation, spatial enrichment, and persistence of real estate listing data into an enterprise GIS environment.

While implemented using ArcPy within the ArcGIS Pro ecosystem, the architecture reflects backend engineering and data pipeline design principles rather than tool-specific scripting.

Key Characteristics

Configuration isolation (no hardcoded infrastructure)

Transaction-safe database operations

Idempotent ingestion logic

Defensive error handling with rollback

Structured logging for observability

Spatial validation and enrichment

Automation-ready execution (Task Scheduler compatible)


🧠 Problem Statement

Manual ingestion of listing data into enterprise GIS systems leads to:

Duplicate records

Partial writes on failure

Inconsistent schema alignment

Lack of traceability

No spatial validation

This pipeline solves those issues with a structured, repeatable workflow.


🏗️ High-Level Architecture
Extract → Transform → Load → Enrich → Cleanup

Each phase is logically separated and executed sequentially with transactional safety.


⚙️ ETL Breakdown
1️⃣ Extract

Detects latest CSV dataset

Validates file availability

Initializes structured logging


2️⃣ Transform

Removes non-data/system rows

Deduplicates using MLS ID (set-based lookup, O(n))

Converts coordinates to spatial features (EPSG:4326)

Applies boundary-based filtering


3️⃣ Load

Opens explicit transactional edit session

Dynamically maps overlapping fields

Inserts only validated, non-duplicate records

Commits or rolls back atomically


4️⃣ Enrich

Performs spatial intersection classification

Computes nearest parcel distance

Updates derived attributes


5️⃣ Cleanup

Deletes temporary feature classes

Logs processing metrics

🛠 Engineering Concepts Demonstrated
Data Engineering

Idempotent ingestion pattern

Set-based duplicate detection

Deterministic enrichment workflow

Dynamic schema reconciliation

Backend Practices

Separation of infrastructure from logic

Explicit transaction management

Multi-level exception handling

Safe rollback strategy

Structured logging for observability

Maintainability & Scalability

Config abstraction

Automation-ready design

Modular refactor planned

Suitable for scheduled execution


📁 Project Structure (Recommended Refactor)
redfin_properties/
│
├── src/
│   ├── app.py
│   ├── etl/
│   │   ├── extract.py
│   │   ├── transform.py
│   │   ├── load.py
│   │   └── enrich.py
│   └── utils/
│       ├── logging_config.py
│       └── helpers.py
│
├── config_template.py
├── .gitignore
├── README.md
└── requirements.txt

(Current implementation exists as a single script; modularization planned.)


🔐 Configuration

Environment-specific values are abstracted into local-only files:

config.py

portal_credentials.py

These files are excluded from version control.

Example template:

MARKET_PROPS_URL = ""
PARCELS_URL = ""
RESERVATION_URL = ""

TEMP_GDB = r""
ENTERPRISE_GDB = r""
CSV_FOLDER = r""

📊 Logging & Observability

The pipeline logs:

Processing stages

Record counts

Duplicate detection metrics

Spatial filtering metrics

Insert totals

Full exception stack traces

Designed for production debugging and monitoring.


🚀 Potential Enhancements

Refactor into modular package structure

Add unit tests (pytest)

Replace credential file with environment variables

Add CLI support (argparse)

Containerize execution

Add CI workflow


👤 Author

Benito Gonzalez
GIS Professional → Software Engineering Transition
