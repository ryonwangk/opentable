# Nobu OpenTable Protocol Analysis Notes

## Restaurant Info

| Restaurant | Slug | ID | Address |
|-----------|------|-----|--------|
| Nobu Los Angeles West Hollywood | `nobu-los-angeles-west-hollywood` | 17077 | 903 N. La Cienega Blvd, West Hollywood, CA 90069 |
| Nobu Malibu | `nobu-malibu` | 19252 | URL format: `/nobu-malibu` (no `/r/` prefix) |

## Query API (Completed, no login required)

### GraphQL Query: RestaurantsAvailability

- **Endpoint**: `POST https://www.opentable.com/dapi/fe/gql?optype=query&opname=RestaurantsAvailability`
- **Persisted Hash**: `cbcf4838a9b399f742e3741785df64560a826d8d3cc2828aa01ab09a8455e29e`

## Booking Protocol (4 steps)

```
Step 1: GET /booking/details -> SSR, parse __INITIAL_STATE__
         Get CC policy, diningAreaId, cancel policy
Step 2: POST /dapi/fe/gql?opname=BookDetailsStandardSlotLock -> slotLockId
Step 3: POST /dapi/booking/make-reservation -> confirmationNumber
Step 4: Return confirmationNumber + securityToken
```

### Apollo Persisted Query Hashes

| Operation | Hash |
|-----------|------|
| `RestaurantsAvailability` | `cbcf4838a9b399f742e3741785df64560a826d8d3cc2828aa01ab09a8455e29e` |
| `BookDetailsStandardSlotLock` | `1100bf68905fd7cb1d4fd0f4504a4954aa28ec45fb22913fa977af8b06fd97fa` |
| `CancelReservation` | `4ee53a006030f602bdeb1d751fa90ddc4240d9e17d015fb7976f8efcb80a026e` |

## Session Cookie Requirements

Required cookies (curl_cffi can generate):
- `_abck`: Akamai Bot Manager challenge response
- `bm_s`, `bm_sv`, `bm_mi`, `bm_sz`, `bm_ss`, `bm_so`, `bm_lso`: Behavior detection cookies
- `OT-SessionId`, `OT-Interactive-SessionId`, `OT-Session-Update-Date`: OT business session
- `ftc`: First-touch cookie

Additional (login required):
- `authCke`: Login auth cookie (must be obtained from browser)

## Known Issues

1. **booking/details may trigger Akamai Bot Challenge**: sometimes returns challenge page instead of normal HTML
2. **makeReservation requires authCke**: curl_cffi session cannot bypass user auth layer
3. **slotLockId validity ~90 seconds**: submit must follow quickly after query
