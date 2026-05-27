# eBay Automation Toolkit
> **Portfolio context:** Extracted from founder-led production systems — multi-marketplace inventory, orders, and warehouse execution. **[Full portfolio](https://github.com/AspiranteD/AspiranteD)** · [aspiranted.github.io](https://aspiranted.github.io)

Full-stack automation toolkit for eBay seller operations: OAuth 2.0 authentication, bulk listing via Feed API, order management via Fulfillment API, shipping fulfillment, and cross-marketplace sync orchestration.

## Architecture

```
src/
+-- auth/
¦   +-- oauth2.py              # OAuth 2.0: authorization code grant, token refresh, file persistence
+-- feed/
¦   +-- feed_client.py          # Feed API: task lifecycle (create ? upload ? poll ? download)
+-- orders/
¦   +-- status.py               # 3-field status mapping (payment × fulfillment × cancel ? 9 states)
¦   +-- orders_service.py       # Fulfillment API: pagination, buyer extraction, tracking, LPN resolution
+-- shipping/
¦   +-- shipping_service.py     # Mark shipped via API, carrier mapping, address formatting
+-- bulk/
¦   +-- category_map.py         # Parent ? leaf category mapping (17 eBay.es categories)
¦   +-- csv_generator.py        # File Exchange CSV generation (Add + End actions)
¦   +-- response_importer.py    # Response parsing with multi-language column detection
+-- sync/
    +-- sync_service.py         # Orchestrator: sync after extraction, full relist, dynamic days_back
```

## Key Technical Decisions

### OAuth 2.0 with File-Based Token Persistence
- Authorization code grant flow with 4 eBay API scopes
- Access token auto-refresh with 5-minute buffer before expiry
- Refresh token rotation support (eBay may rotate on refresh)
- File-based JSON storage (no database dependency for auth)

### Feed API Task Lifecycle
- Full create ? upload ? poll ? download pipeline
- Automatic 401 retry with token refresh on any API call
- Configurable poll interval and max wait with timeout detection
- Task ID extraction from both `Location` header and response body
- Multi-file sequential upload with result collection

### 3-Field Order Status Mapping
eBay reports order state across three independent fields:
- `orderPaymentStatus`: PENDING | PAID | FAILED | FULLY_REFUNDED
- `orderFulfillmentStatus`: NOT_STARTED | IN_PROGRESS | FULFILLED
- `cancelStatus.cancelState`: NONE_REQUESTED | CANCEL_REQUESTED | CANCELED | ...

These are mapped to 9 unified internal states via a decision tree:
1. Cancel state takes priority over everything
2. Refund + FULFILLED ? REEMBOLSADO (shipped then returned)
3. Refund without fulfillment ? CANCELADO (never shipped)
4. FULFILLED ? ENTREGADO
5. IN_PROGRESS ? ENVIADO
6. Default ? POR_ENVIAR

### Buyer Extraction from Nested JSON
eBay orders nest buyer data deeply:
- Name comes from `fulfillmentStartInstructions[0].shippingStep.shipTo.fullName`
- Email has a fallback chain: shipTo ? buyerRegistrationAddress
- Phone from `shipTo.primaryPhone.phoneNumber`
- Full address parsing with label formatting for shipping

### LPN Resolution with Legacy Suffix
SKU-to-inventory matching with fallback for pre-system listings:
- Direct match: `LPNWE001` ? found in inventory
- Legacy suffix strip: `LPNWE001AB` ? try `LPNWE001` (2-letter alpha suffix)
- Only strips if last 2 chars are alphabetic

### Tracking Extraction with Multiple Fallbacks
- Primary: `fulfillments[].shipmentTrackingNumber` (list or string)
- Fallback: `lineItems[].deliveryAddress.fulfillments[].shipmentTrackingNumber`
- Handles both list and string responses from eBay API

### Bulk CSV Generation (File Exchange Format)
- Title generation: brand + model + description, truncated to 80 chars with condition suffix and unique ID
- Price markup from source marketplace price with minimum enforcement
- Condition mapping: PERFECTO?3000 (Used), CON_TARA?3000, PARA_PIEZAS?7000 (For parts)
- Image pipe-separation (up to 12 URLs)
- Text sanitization: strips newlines/tabs/Unicode control chars that break CSV
- Business policies vs manual shipping/return/payment config
- Category mapping: Amazon department/category/subcategory ? eBay leaf categories
- Chunking: max 1000 items per file

### Response File Parsing
- Multi-language column detection (English: "ItemID", Spanish: "Resultado", etc.)
- Pattern matching with partial match fallback
- Add vs End action detection
- Batch import with JSON dedup tracking
- Source vs response file discrimination

### Sync Orchestration
- Dynamic `days_back` calculation based on last import timestamp
- If server was down, requests more history (bounded 7–90 days, +2 safety margin)
- Post-extraction sync: import orders ? end cross-channel sold items ? upload new listings
- Full relist: end all active ? wait ? re-upload all (search positioning)
- Error isolation: each step continues even if previous fails

### Carrier Mapping
Maps eBay carrier codes to internal names:
- `CORREOS_DE_ESPANA` / `CORREOS` ? `CORREOS`
- `CORREOS_EXPRESS`, `SEUR`, `MRW`, `GLS`, `DHL`, `UPS`, `NACEX`
- Unknown carriers default to `CORREOS`

## Testing

```bash
pip install -r requirements.txt
python -m pytest tests/ -v
```

**216 tests** covering:
- OAuth 2.0 flow (token persistence, refresh, expiry buffer, code extraction)
- Feed API (task creation, upload, polling, timeout, 401 retry)
- Status mapping (all cancel/refund/fulfillment combinations)
- Buyer extraction (nested JSON, fallbacks, address formatting)
- Tracking extraction (list, string, fallback paths)
- LPN resolution (exact match, legacy suffix, edge cases)
- Order parsing (fee splitting, multi-item, dates)
- CSV generation (titles, prices, conditions, sanitization, images, policies)
- Response parsing (column detection, status mapping, dedup)
- Category mapping (parent?leaf, priority, case-insensitive)
- Sync orchestration (dynamic days_back, error isolation, relist flow)

## Configuration

Copy `.env.example` to `.env` and configure:

```bash
EBAY_CLIENT_ID=...          # eBay Developer Program app credentials
EBAY_CLIENT_SECRET=...
EBAY_RUNAME=...             # Redirect URI name from eBay
EBAY_PRICE_MARKUP_PCT=15    # % markup over source price
EBAY_MIN_PRICE_EUR=5        # Minimum listing price
EBAY_SHIPPING_PROFILE=...   # eBay business policy names (optional)
```

## License

MIT
