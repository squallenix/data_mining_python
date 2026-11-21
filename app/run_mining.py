import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from pipeline.cleaner import DataCleaner
from pipeline.exporter import DatasetExporter
from pipeline.http_client import EcommerceHttpClient
from pipeline.miner import DataMiner
from pipeline.universal_scraper import UniversalScraper


async def run_pipeline(target_url: str, backend_url: str = "http://localhost:8001", max_products: int = 30, features: str = None):
    print("=" * 60)
    print(f"[PIPELINE] STARTING UNIVERSAL DATA MINING PIPELINE")
    print(f"[PIPELINE] Target URL:  {target_url}")
    print(f"[PIPELINE] Backend URL: {backend_url}")
    if features:
        print(f"[PIPELINE] Custom Features: {features}")
    print("=" * 60)

    http_client = EcommerceHttpClient()
    scraper = UniversalScraper(http_client)

    try:
        # Step 1: Universal Web Scraping
        print("\n[Stage 1/5] Universal Scraping...")
        records, domain = await scraper.scrape(target_url, max_products=max_products)
        if not records:
            raise RuntimeError(f"Universal scraper found 0 products at {target_url}")
        print(f"[SUCCESS] Scraped {len(records)} products from {domain}")

        # Step 2: Data Cleaning & Feature Derivation
        print("\n[Stage 2/5] Cleaning Data & Deriving Universal Features...")
        df_raw = DataCleaner.records_to_frame(records, domain, custom_features=features)
        df_clean, cleaning_report = DataCleaner.clean_dataset(df_raw)
        print(f"[SUCCESS] Data cleaned. Completeness: {cleaning_report['summary']['completeness']}%")

        # Step 3: Academic Data Mining & ML Models
        print("\n[Stage 3/5] Running Academic Data Mining (Regression, Classification, Clustering)...")
        df_mined, prediction_report = DataMiner.run_mining(df_clean)
        imputed_count = int(df_mined["is_price_imputed"].sum())
        print(f"[SUCCESS] ML models trained. Missing prices imputed: {imputed_count}")

        # Step 4: CSV Export
        print("\n[Stage 4/5] Exporting Timestamped CSV Dataset...")
        output_dir = Path(__file__).resolve().parent / "data"
        csv_file = DatasetExporter.export_csv(df_mined, domain, output_dir=str(output_dir))
        print(f"[SUCCESS] CSV saved at: {csv_file}")

        # Step 5: Upload Mined Products to Backend API & MongoDB
        print("\n[Stage 5/5] Synchronizing Mined Products with Backend API...")
        product_endpoint = f"{backend_url.rstrip('/')}/api/v1/product"
        sent_count = 0
        skipped_count = 0
        failed_count = 0

        for _, row in df_mined.iterrows():
            images_list = []
            if isinstance(row.get("images"), list) and row.get("images"):
                for i, img_url in enumerate(row["images"]):
                    if img_url:
                        images_list.append({"url": str(img_url), "alt": str(row["name"]), "isPrimary": i == 0, "sortOrder": i})
            elif row.get("image"):
                images_list.append({"url": str(row["image"]), "alt": str(row["name"]), "isPrimary": True, "sortOrder": 0})

            payload = {
                "name": str(row["name"]),
                "productSlug": str(row["productSlug"]),
                "price": safe_float(row.get("price_imputed"), 0.0),
                "currency": "BDT",
                "description": str(row.get("description") or ""),
                "shortDescription": str(row.get("name") or ""),
                "brand": str(row.get("brand") or "Unknown"),
                "images": images_list,
                "rating": {
                    "average": safe_float(row.get("rating"), 0.0),
                    "count": safe_int(row.get("rating_count"), 0),
                },
                "specifications": row.get("specifications") if isinstance(row.get("specifications"), dict) else {},
                "attributes": {
                    "availability": str(row.get("availability") or "InStock"),
                    "price_imputed": bool(row.get("is_price_imputed")),
                    "original_price": safe_float(row.get("price"), None),
                    "imputed_price": safe_float(row.get("price_imputed"), None) if bool(row.get("is_price_imputed")) else None,
                    "segment": str(row.get("segment") or "Cluster 0"),
                    "source_domain": domain,
                    "is_smart": bool(row.get("is_smart", False)),
                    "is_wireless": bool(row.get("is_wireless", False)),
                    "is_inverter": bool(row.get("is_inverter", False)),
                    "is_hd": bool(row.get("is_hd", False)),
                },
                "tags": [domain, str(row.get("segment") or "Cluster 0"), "mined"],
                "sources": [{
                    "name": domain,
                    "url": str(row.get("url") or target_url),
                }],
                "status": "active",
                "isVisible": True,
            }

            try:
                # Check if product exists by slug
                check_resp = await http_client.client.get(f"{product_endpoint}/slug/{payload['productSlug']}")
                if check_resp.status_code == 200:
                    skipped_count += 1
                    continue

                resp = await http_client.post(product_endpoint, json=payload)
                if resp.status_code in (200, 201):
                    sent_count += 1
                else:
                    failed_count += 1
            except Exception as e:
                failed_count += 1

        print(f"[SYNC] Backend Sync: {sent_count} created, {skipped_count} already existed, {failed_count} failed")

        # Save latest analytics JSON for Backend & Frontend
        analytics_payload = {
            "sourceDomain": domain,
            "targetUrl": target_url,
            "csvPath": csv_file,
            "cleaning": cleaning_report,
            "prediction": prediction_report,
            "timestamp": str(asyncio.get_event_loop().time()),
        }

        def clean_nan_for_json(obj):
            if isinstance(obj, float):
                import math
                return None if math.isnan(obj) else obj
            if isinstance(obj, dict):
                return {k: clean_nan_for_json(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [clean_nan_for_json(v) for v in obj]
            return obj

        sanitized_analytics = clean_nan_for_json(analytics_payload)

        # Save in Python data folder
        analytics_path = output_dir / "latest_analytics.json"
        with open(analytics_path, "w", encoding="utf-8") as f:
            json.dump(sanitized_analytics, f, indent=2)

        # Also save in Backend folder for immediate serving
        backend_env = os.environ.get("BACKEND_ANALYTICS_PATH")
        if backend_env:
            backend_analytics_path = Path(backend_env)
        else:
            candidate = Path(__file__).resolve().parent.parent.parent / "data_mining_backend" / "latest_analytics.json"
            if candidate.parent.exists():
                backend_analytics_path = candidate
            elif Path("latest_analytics.json").exists() or Path(".").resolve().name == "data_mining_backend":
                backend_analytics_path = Path("latest_analytics.json")
            else:
                backend_analytics_path = Path("e:/Code/data_mining_backend/latest_analytics.json")
        try:
            with open(backend_analytics_path, "w", encoding="utf-8") as f:
                json.dump(sanitized_analytics, f, indent=2)
            print(f"[ANALYTICS] Saved to backend at: {backend_analytics_path}")
        except Exception as e:
            print(f"Could not write to backend dir: {e}")

        print("\n[COMPLETE] UNIVERSAL MINING PIPELINE COMPLETED SUCCESSFULLY!")
        return analytics_payload

    finally:
        await http_client.close()


def safe_int(val, default=0):
    if val is None or pd_is_nan(val):
        return default
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default


def safe_float(val, default=0.0):
    if val is None or pd_is_nan(val):
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def pd_is_nan(val):
    if val is None:
        return True
    try:
        import math
        return math.isnan(float(val))
    except (ValueError, TypeError):
        return False


def main():
    parser = argparse.ArgumentParser(description="Universal Data Mining Pipeline Runner")
    parser.add_argument("--url", default="https://www.startech.com.bd/television-tv", help="Target website URL")
    parser.add_argument("--backend", default="http://localhost:8001", help="Express backend URL")
    parser.add_argument("--max-products", type=int, default=30, help="Max products to mine")
    parser.add_argument("--features", default=None, help="Comma-separated custom features to derive (e.g. gaming,ssd,oled,touchscreen)")
    args = parser.parse_args()

    asyncio.run(run_pipeline(args.url, args.backend, args.max_products, features=args.features))


if __name__ == "__main__":
    main()
