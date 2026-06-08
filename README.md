# OpenTable Reservation Protocol Script

Pure HTTP protocol implementation for OpenTable slot query and reservation — **no browser required**.

## Quick Start

```powershell
# Query available slots
.\venv\Scripts\python.exe main.py -d 2026-06-29 -t 19:00 -p 4

# Poll mode (every 10 seconds)
.\venv\Scripts\python.exe main.py --poll --poll-interval 10 -d 2026-06-29 -t 19:00 -p 4

# Preview booking details
.\venv\Scripts\python.exe main.py --preview -d 2026-06-29 -t 19:00 -p 4 --slot-hash <hash> --slot-token <token>

# Execute reservation (requires authCke cookie)
.\venv\Scripts\python.exe main.py --book -d 2026-06-29 -t 19:00 -p 4 \
  --slot-hash <hash> --slot-token <token> \
  --auth-cke "your_authCke_cookie" \
  --first-name John --last-name Doe \
  --email john@example.com --phone "3105551234"

# Check RiskByPass balance
.\venv\Scripts\python.exe main.py --check-balance
```

## Features

### Slot Query (no login required)
- curl_cffi TLS fingerprint impersonation, direct GraphQL API requests
- Returns all slots in a 7-hour window centered on the anchor time
- Auto-fallback to RiskByPass on 403

### Booking Flow (4 steps)
```
Step 1: GET /booking/details  → Parse CC policy, diningAreaId, cancellation policy
Step 2: POST BookDetailsStandardSlotLock → slotLockId (~90s validity)
Step 3: POST /dapi/booking/make-reservation → confirmationNumber
Step 4: Return confirmation number + securityToken
```

## Configuration

Edit `config.py`:
```python
RESTAURANT_SLUG = "nobu-los-angeles-west-hollywood"
RESTAURANT_ID = 17077
RISK_TOKEN = "your_riskbypass_token"
```

## Dependencies

```
curl_cffi  # TLS fingerprint impersonation
requests   # HTTP client
riskbypass # Backup _abck generation
```

## Known Limitations

1. **makeReservation requires authCke**: curl_cffi session cannot bypass user auth layer
2. **booking/details may trigger Akamai Bot Challenge**: sometimes returns challenge page instead of normal HTML
3. **slotLockId validity ~90 seconds**: submit must follow quickly after query
