"""
Full workflow demo: OAuth2 auth → upload listing CSV → fetch orders → ship.

This script demonstrates the complete eBay automation pipeline using all
modules in the toolkit. It requires valid eBay API credentials in a .env file.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.auth.oauth2 import EbayOAuth2Client
from src.feed.feed_client import EbayFeedClient
from src.orders.fulfillment import EbayFulfillmentClient
from src.shipping.shipping_service import EbayShippingService


def main():
    load_dotenv()

    # --- Step 1: Authenticate ---
    print("=== Step 1: OAuth2 Authentication ===")
    auth = EbayOAuth2Client()

    try:
        token = auth.get_valid_token()
        print(f"  Token valid (first 10 chars): {token[:10]}...")
    except ValueError:
        print("  No stored tokens found. Starting authorization flow...")
        auth_url = auth.get_authorization_url(state="demo")
        print(f"  Open this URL in your browser:\n  {auth_url}")
        auth_code = input("  Paste the authorization code: ").strip()
        auth.exchange_code_for_tokens(auth_code)
        print("  Tokens saved successfully.")

    # --- Step 2: Upload listings via Feed API ---
    print("\n=== Step 2: Upload Listing CSV ===")
    feed = EbayFeedClient(auth_client=auth)

    csv_path = "examples/sample_listings.csv"
    if Path(csv_path).exists():
        result = feed.upload_and_wait(csv_path)
        print(f"  Task {result.task_id}: {result.status}")
        if result.upload_summary:
            print(f"  Summary: {result.upload_summary}")
    else:
        print(f"  Skipping upload — {csv_path} not found.")
        print("  Create a CSV with eBay listing columns to test this step.")

    # --- Step 3: Fetch recent orders ---
    print("\n=== Step 3: Fetch Orders ===")
    fulfillment = EbayFulfillmentClient(auth_client=auth)
    orders = fulfillment.fetch_orders(days_back=7)
    print(f"  Found {len(orders)} orders")

    for order in orders[:5]:
        print(f"  [{order.status}] {order.order_id} — {order.total_amount} {order.currency}")
        if order.buyer:
            print(f"    Buyer: {order.buyer.name}, {order.buyer.city}")

    # --- Step 4: Ship pending orders ---
    print("\n=== Step 4: Ship Pending Orders ===")
    shipping = EbayShippingService(auth_client=auth)
    pending = [o for o in orders if o.status == "PENDING_SHIPMENT"]
    print(f"  {len(pending)} orders pending shipment")

    for order in pending[:2]:
        if order.buyer:
            label = shipping.format_address_for_label({
                "name": order.buyer.name,
                "address_line1": order.buyer.address_line1,
                "address_line2": order.buyer.address_line2,
                "city": order.buyer.city,
                "state_or_province": order.buyer.state_or_province,
                "postal_code": order.buyer.postal_code,
                "country_code": order.buyer.country_code,
            })
            print(f"\n  Shipping label for {order.order_id}:")
            for line in label.split("\n"):
                print(f"    {line}")

    print("\n=== Workflow Complete ===")


if __name__ == "__main__":
    main()
