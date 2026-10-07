import duckdb
import pandas as pd
import json
import glob
from pathlib import Path
import numpy as np
import requests
from io import StringIO

# path configuration section.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
WAREHOUSE_DIR = PROJECT_ROOT / "warehouse"
DB_PATH = WAREHOUSE_DIR / "warehouse.duckdb"

# CONFIG: Paths to raw data
TOURISM_CSV = DATA_DIR / "tourism" / "tourism_data.csv"
GROCERY_DIR = DATA_DIR / "grocery"


def get_db_connection():
    """Connects to the local DuckDB warehouse."""
    WAREHOUSE_DIR.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(DB_PATH))


def load_tourism(con):
    """Loads the Tourism CSV into a Bronze table."""
    print("Loading Tourism Data...")
    try:
        query = f"""
            CREATE OR REPLACE TABLE bronze_tourism AS 
            SELECT * FROM read_csv_auto('{TOURISM_CSV.as_posix()}', header=True);
        """
        con.execute(query)
        print("✅ Bronze Tourism loaded.")
    except Exception as e:
        print(f"❌ Error loading Tourism: {e}")


def load_grocery_reference(con):
    """Loads Stores and Products JSONs."""
    print("Loading Grocery Reference Data...")
    try:
        # Stores
        with open(GROCERY_DIR / "stores.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        df_stores = pd.json_normalize(data)
        con.execute("CREATE OR REPLACE TABLE bronze_stores AS SELECT * FROM df_stores")
        print("✅ Bronze Stores loaded.")

        # Products
        with open(GROCERY_DIR / "products.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        df_products = pd.json_normalize(data)
        con.execute(
            "CREATE OR REPLACE TABLE bronze_products AS SELECT * FROM df_products"
        )
        print("✅ Bronze Products loaded.")
    except Exception as e:
        print(f"❌ Error loading Reference Data: {e}")


def load_grocery_sales(con):
    """Loops through all yearly sales JSON files and combines them."""
    print("Loading Grocery Sales...")
    files = sorted(GROCERY_DIR.glob("grocery_sales_*.json"))
    if not files:
        print("⚠️ No grocery sales files found!")
        return

    all_dfs = []
    for file_path in files:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        all_dfs.append(pd.json_normalize(data))

    if all_dfs:
        full_df = pd.concat(all_dfs, ignore_index=True)
        con.execute(
            "CREATE OR REPLACE TABLE bronze_grocery_sales AS SELECT * FROM full_df"
        )
        print(f"✅ Bronze Grocery Sales loaded ({len(full_df)} rows).")


def extract_population_from_api(con):
    print("🌍 Extracting Population Data from ÅSUB API...")

    # Saved Query URL (returns CSV directly)
    url = "https://pxweb.asub.ax/PXWeb/sq/12095902-f8bb-4e6c-8eb2-348ac9b4e767"

    try:
        # Fetch CSV data from API        
        response = requests.get(url)
        response.raise_for_status()

        # Read CSV, skipping the title row (row 0)
        
        df_raw = pd.read_csv(StringIO(response.text), skiprows=1)

        print(f"   Raw data shape: {df_raw.shape}")
        print(f"   Columns: {df_raw.columns.tolist()[:5]}")  # Show first 5 columns

        # Save raw "wide" data to Bronze layer
        con.execute(
            "CREATE OR REPLACE TABLE bronze_population_raw AS SELECT * FROM df_raw"
        )

        # Reshape from wide to long format
        # Columns are: year, age, "Brändö Both sexes", "Brändö Females", "Brändö Males", ...
        id_cols = ["year", "age"]
        df_long = df_raw.melt(
            id_vars=id_cols, var_name="municipality_sex", value_name="population"
        )

        # Parse "Mariehamn Both sexes" into municipality_name and sex
        def parse_mun_sex(val):
            if " Both sexes" in val:
                return val.replace(" Both sexes", ""), "Both sexes"
            elif " Females" in val:
                return val.replace(" Females", ""), "Females"
            elif " Males" in val:
                return val.replace(" Males", ""), "Males"
            else:
                return val, "Unknown"

        parsed = df_long["municipality_sex"].apply(parse_mun_sex)
        df_long["municipality_name"] = parsed.apply(lambda x: x[0])
        df_long["sex"] = parsed.apply(lambda x: x[1])

        # Drop the combined column and reorder
        df_long = df_long.drop(columns=["municipality_sex"])
        df_long = df_long[["year", "age", "municipality_name", "sex", "population"]]

        # Save to Bronze layer
        con.execute(
            "CREATE OR REPLACE TABLE bronze_population AS SELECT * FROM df_long"
        )

        # Create dimension table for municipalities
        dim_mun = (
            df_long[["municipality_name"]].drop_duplicates().reset_index(drop=True)
        )
        con.execute("CREATE OR REPLACE TABLE dim_municipality AS SELECT * FROM dim_mun")

        row_count = con.execute("SELECT COUNT(*) FROM bronze_population").fetchone()[0]
        print(f"✅ Bronze Population loaded from API ({row_count} rows).")

    except Exception as e:
        print(f"❌ Error fetching from API: {e}")
        import traceback

        traceback.print_exc()


def main():
    con = get_db_connection()

    load_tourism(con)
    load_grocery_reference(con)
    load_grocery_sales(con)
    extract_population_from_api(con)

    con.close()
    print(f"🚀 Ingestion Complete! Data is in {DB_PATH}")


if __name__ == "__main__":
    main()
