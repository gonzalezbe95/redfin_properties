import arcpy
import os
import sys
import time
import logging
from config import (
    MARKET_PROPS_URL,
    HEX_LAYER_URL,
    PARCELS_URL,
    RESERVATION_URL,
    TEMP_GDB,
    ENTERPRISE_GDB,
    CSV_FOLDER,
)

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from portal_credentials import USERNAME, PASSWORD, PORTAL_URL

# ============================================
# LOGGING SETUP
# ============================================
log_file = os.path.join(os.path.dirname(__file__), "redfin_script.log")
os.makedirs(os.path.dirname(log_file), exist_ok=True)

logging.basicConfig(
    filename=log_file,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

# Also print to console
console = logging.StreamHandler()
console.setLevel(logging.INFO)
logging.getLogger("").addHandler(console)

logging.info("=" * 60)
logging.info("Script started")
logging.info("=" * 60)


# ============================================
# WAIT FOR NETWORK DRIVES
# ============================================
def wait_for_network_path(path, timeout=120):
    """Wait for network path to become available"""
    logging.info(f"Checking network path: {path}")
    elapsed = 0
    while not os.path.exists(path) and elapsed < timeout:
        logging.info(f"Waiting for network path... ({elapsed}s/{timeout}s)")
        time.sleep(5)
        elapsed += 5

    if not os.path.exists(path):
        raise Exception(f"Network path not accessible after {timeout}s: {path}")

    logging.info(f"Network path accessible: {path}")
    return True


try:
    logging.info("Connecting to Portal...")
    arcpy.SignInToPortal(PORTAL_URL, USERNAME, PASSWORD)
    logging.info("Portal connection successful")
except Exception as e:
    logging.error(f"Portal connection failed: {str(e)}")
    sys.exit(1)


def process_redfin_data(csv_file):
    """
    ETL process to load Redfin property data into Market Property Inventory

    Parameters:
    csv_file (str): Path to the Redfin CSV file to process

    Returns:
    int: Number of new properties added, or 0 if no new properties
    """

    arcpy.env.overwriteOutput = True

    # ============================================
    # CONFIGURATION - USE UNC PATHS
    # ============================================
    # Enterprise geodatabase - UNC path
    enterprise_gdb = ENTERPRISE_GDB
    # File geodatabase - UNC path
    temp_gdb = TEMP_GDB

    # Wait for network paths to be available
    wait_for_network_path(os.path.dirname(enterprise_gdb))
    wait_for_network_path(temp_gdb)

    arcpy.env.workspace = temp_gdb

    # Production feature class in enterprise GDB
    market_props = MARKET_PROPS_URL
    # Portal services
    hex_layer = HEX_LAYER_URL
    reservation_url = RESERVATION_URL
    parcels_url = PARCELS_URL

    # Temporary feature class names (created in temp_gdb)
    temp_table = "temp_redfin"
    temp_points = "temp_redfin_points"
    points_layer_name = "points_layer"
    inventory_layer_name = "inventory_layer"

    try:
        # ============================================
        # EXTRACT: Validate CSV
        # ============================================
        logging.info("Step 1: Validating CSV data...")
        if not os.path.exists(csv_file):
            raise FileNotFoundError(f"CSV file not found: {csv_file}")

        logging.info(f"Using CSV: {os.path.basename(csv_file)}")

        # ============================================
        # TRANSFORM: Clean and deduplicate
        # ============================================
        logging.info("Step 2: Cleaning data...")
        if arcpy.Exists(temp_table):
            arcpy.management.Delete(temp_table)

        arcpy.management.CopyRows(csv_file, temp_table)

        # Remove non-records
        not_record = "In accordance with local MLS rules, some MLS listings are not included in the download"
        arcpy.management.MakeTableView(
            temp_table, "temp_view", f"SALE_TYPE = '{not_record}'"
        )
        delete_count = int(arcpy.management.GetCount("temp_view")[0])
        if delete_count > 0:
            logging.info(f"Removing {delete_count} non-MLS records...")
            arcpy.management.DeleteRows("temp_view")

        # Check for MLS# duplicates
        logging.info("Step 3: Checking for duplicates...")
        existing_mls = set()
        with arcpy.da.SearchCursor(market_props, ["MLS_"]) as cursor:
            for row in cursor:
                if row[0] is not None:
                    existing_mls.add(str(row[0]).strip())

        logging.info(
            f"Found {len(existing_mls)} existing records in Market Property Inventory"
        )

        # Count and remove duplicates
        match_count = 0
        with arcpy.da.UpdateCursor(temp_table, ["MLS_"]) as cursor:
            for row in cursor:
                if row[0] and str(row[0]).strip() in existing_mls:
                    cursor.deleteRow()
                    match_count += 1

        if match_count > 0:
            logging.info(f"Removed {match_count} duplicate MLS records")
        else:
            logging.info("No duplicates found")

        # Check if any records remain after deduplication
        remaining_count = int(arcpy.management.GetCount(temp_table)[0])
        logging.info(f"{remaining_count} new records remaining after deduplication")

        if remaining_count == 0:
            logging.info("=" * 50)
            logging.info("NO NEW PROPERTIES TO APPEND")
            logging.info(
                "All records in CSV already exist in Market Property Inventory"
            )
            logging.info("=" * 50)
            # Cleanup temp table
            if arcpy.Exists(temp_table):
                arcpy.management.Delete(temp_table)
            return 0

        # ============================================
        # TRANSFORM: Convert to spatial points
        # ============================================
        logging.info("Step 4: Converting to spatial points...")
        if arcpy.Exists(temp_points):
            arcpy.management.Delete(temp_points)

        spatial_ref = arcpy.SpatialReference(4326)  # WGS 1984

        arcpy.management.XYTableToPoint(
            in_table=temp_table,
            out_feature_class=temp_points,
            x_field="LONGITUDE",
            y_field="LATITUDE",
            coordinate_system=spatial_ref,
        )

        # ============================================
        # TRANSFORM: Filter by hex boundary
        # ============================================
        logging.info("Step 5: Filtering points by Hex Community Expansion boundary...")
        points_layer = arcpy.management.MakeFeatureLayer(temp_points, points_layer_name)

        # Select points OUTSIDE hex and delete them
        arcpy.management.SelectLayerByLocation(
            points_layer_name,
            "INTERSECT",
            hex_layer,
            "",
            "NEW_SELECTION",
            "INVERT",
        )

        outside_count = int(arcpy.management.GetCount(points_layer_name)[0])
        if outside_count > 0:
            logging.info(f"Removing {outside_count} points outside hex boundary...")
            arcpy.management.DeleteFeatures(points_layer_name)

        # Clear selection to get all remaining points
        arcpy.management.SelectLayerByAttribute(points_layer_name, "CLEAR_SELECTION")

        # Check if any points remain after boundary filter
        final_points_count = int(arcpy.management.GetCount(points_layer_name)[0])
        logging.info(f"{final_points_count} points remaining after boundary filter")

        if final_points_count == 0:
            logging.info("=" * 50)
            logging.info("NO NEW PROPERTIES TO APPEND")
            logging.info(
                "All new properties are outside the Hex Community Expansion boundary"
            )
            logging.info("=" * 50)
            # Cleanup
            if arcpy.Exists(temp_table):
                arcpy.management.Delete(temp_table)
            if arcpy.Exists(temp_points):
                arcpy.management.Delete(temp_points)
            return 0

        # ============================================
        # LOAD: Insert records into enterprise feature class
        # ============================================
        logging.info("Step 6: Inserting records into Market Property Inventory...")
        append_count_before = int(arcpy.management.GetCount(market_props)[0])

        # Get field names (exclude system fields)
        source_fields = [
            f.name
            for f in arcpy.ListFields(temp_points)
            if f.type not in ["OID", "Geometry", "GlobalID"]
            and f.name.upper()
            not in ["OBJECTID", "GLOBALID", "SHAPE", "SHAPE_LENGTH", "SHAPE_AREA"]
        ]

        target_fields = [
            f.name
            for f in arcpy.ListFields(market_props)
            if f.type not in ["OID", "Geometry", "GlobalID"]
            and f.name.upper()
            not in ["OBJECTID", "GLOBALID", "SHAPE", "SHAPE_LENGTH", "SHAPE_AREA"]
        ]

        # Find common fields
        common_fields = [f for f in source_fields if f in target_fields]
        common_fields.append("SHAPE@")  # Add geometry

        logging.info(f"Copying {len(common_fields)-1} fields plus geometry...")

        # Start edit session
        edit = arcpy.da.Editor(enterprise_gdb)
        edit.startEditing(False, True)
        edit.startOperation()

        try:
            inserted = 0
            with arcpy.da.SearchCursor(
                points_layer_name, common_fields
            ) as search_cursor:
                with arcpy.da.InsertCursor(
                    market_props, common_fields
                ) as insert_cursor:
                    for row in search_cursor:
                        insert_cursor.insertRow(row)
                        inserted += 1

            edit.stopOperation()
            edit.stopEditing(True)
            logging.info(f"Successfully inserted {inserted} records")

        except Exception as e:
            edit.stopOperation()
            edit.stopEditing(False)
            raise e

        append_count_after = int(arcpy.management.GetCount(market_props)[0])
        new_records = append_count_after - append_count_before
        logging.info(f"Added {new_records} new properties to inventory")

        # ============================================
        # ENRICH: Update Within_Reservation field
        # ============================================
        logging.info("Step 7: Updating Within_Reservation field...")
        inventory_layer = arcpy.management.MakeFeatureLayer(
            market_props, inventory_layer_name
        )

        # Select records where Within_Reservation is NULL
        arcpy.management.SelectLayerByAttribute(
            inventory_layer_name, "NEW_SELECTION", "Within_Reservation IS NULL"
        )

        null_count = int(arcpy.management.GetCount(inventory_layer_name)[0])
        logging.info(f"Found {null_count} records with NULL Within_Reservation values")

        if null_count > 0:
            # Start edit session for updates
            edit = arcpy.da.Editor(enterprise_gdb)
            edit.startEditing(False, True)
            edit.startOperation()

            try:
                # Select those that intersect reservation
                arcpy.management.SelectLayerByLocation(
                    inventory_layer_name,
                    "INTERSECT",
                    reservation_url,
                    "",
                    "SUBSET_SELECTION",
                )

                intersect_count = int(
                    arcpy.management.GetCount(inventory_layer_name)[0]
                )
                logging.info(
                    f"Updating {intersect_count} records to 'Yes' (within reservation)..."
                )

                with arcpy.da.UpdateCursor(
                    inventory_layer_name, ["Within_Reservation"]
                ) as cursor:
                    for row in cursor:
                        row[0] = "Yes"
                        cursor.updateRow(row)

                # Switch to NULL records that don't intersect
                arcpy.management.SelectLayerByAttribute(
                    inventory_layer_name, "NEW_SELECTION", "Within_Reservation IS NULL"
                )

                outside_res_count = int(
                    arcpy.management.GetCount(inventory_layer_name)[0]
                )
                logging.info(
                    f"Updating {outside_res_count} records to 'No' (outside reservation)..."
                )

                with arcpy.da.UpdateCursor(
                    inventory_layer_name, ["Within_Reservation"]
                ) as cursor:
                    for row in cursor:
                        row[0] = "No"
                        cursor.updateRow(row)

                edit.stopOperation()
                edit.stopEditing(True)
                logging.info("Successfully updated Within_Reservation field")

            except Exception as e:
                edit.stopOperation()
                edit.stopEditing(False)
                raise e

        # ============================================
        # ENRICH: Calculate nearest tribal parcel for NEW records
        # ============================================
        logging.info(
            "Step 8: Calculating distance to nearest tribal parcel for new records..."
        )

        if new_records > 0:
            inventory_layer = arcpy.management.MakeFeatureLayer(
                market_props, inventory_layer_name
            )

            arcpy.management.SelectLayerByAttribute(
                inventory_layer_name,
                "NEW_SELECTION",
                f"MLS_ IS NOT NULL",
            )

            local_new_props = r"in_memory\new_market_props"
            arcpy.management.CopyFeatures(inventory_layer, local_new_props)

            arcpy.analysis.Near(
                in_features=local_new_props,
                near_features=parcels_url,
                search_radius=None,
                location="NO_LOCATION",
                angle="NO_ANGLE",
                method="PLANAR",
                distance_unit="Miles",
            )

            near_dict = {
                row[0]: (row[1], row[2])
                for row in arcpy.da.SearchCursor(
                    local_new_props, ["MLS_", "NEAR_DIST", "NEAR_FID"]
                )
            }

            with arcpy.da.UpdateCursor(
                inventory_layer, ["MLS_", "NEAR_DIST", "NEAR_FID"]
            ) as update_cursor:
                for row in update_cursor:
                    mls = row[0]
                    if mls in near_dict:
                        row[1], row[2] = near_dict[mls]
                        update_cursor.updateRow(row)

            logging.info(
                f"NEAR_DIST and NEAR_FID updated for {len(near_dict)} new properties"
            )

        else:
            logging.info("No new properties, skipping NEAR_DIST calculation")

        # ============================================
        # CLEANUP
        # ============================================
        logging.info("Step 9: Cleaning up temporary data...")
        if arcpy.Exists(temp_table):
            arcpy.management.Delete(temp_table)
        if arcpy.Exists(temp_points):
            arcpy.management.Delete(temp_points)

        logging.info("=" * 50)
        logging.info("ETL Process Complete!")
        logging.info(f"Total new properties added: {new_records}")
        logging.info(f"Total records in inventory: {append_count_after}")
        logging.info("=" * 50)

        return new_records

    except Exception as e:
        logging.error(f"ERROR in process_redfin_data: {str(e)}")
        import traceback

        logging.error(traceback.format_exc())
        # Cleanup on error
        for item in [temp_table, temp_points, points_layer_name, inventory_layer_name]:
            if arcpy.Exists(item):
                try:
                    arcpy.management.Delete(item)
                except:
                    pass
        raise


if __name__ == "__main__":
    try:
        import glob

        # UNC path instead of N:
        csv_folder = CSV_FOLDER
        # Wait for network path
        wait_for_network_path(csv_folder)

        csv_files = glob.glob(os.path.join(csv_folder, "redfin_*.csv"))
        if not csv_files:
            logging.warning("No Redfin CSV files found")
            sys.exit(0)
        else:
            # Get most recent CSV
            latest_csv = max(csv_files, key=os.path.getmtime)
            logging.info(f"Processing CSV: {latest_csv}")

            # Process the CSV
            new_properties = process_redfin_data(latest_csv)

            logging.info(f"Processing complete. {new_properties} new properties added.")
            sys.exit(0)

    except Exception as e:
        logging.error(f"FATAL ERROR: {str(e)}")
        import traceback

        logging.error(traceback.format_exc())
        sys.exit(1)
