# ScaleEV for Home Assistant

Start a ScaleEV (scaletech.xyz white-label networks such as Collectron Energy) charging session automatically when your vehicle's charge cable is connected at a location you choose.

> Unofficial. Built from the web app's HTTP API; it may change without notice. Not affiliated with ScaleEV.

## How it works
1. Cable sensor turns `on` and the vehicle is inside your chosen zone.
2. The `scaleev_start_charging` script logs in and calls remote-start.
3. **First session only:** ScaleEV returns a Stripe Checkout link. You get a notification with a **Pay now** button. The script waits (default 10 min) for the payment hold to be authorized.
4. You get a success or failure notification.

## Install
1. Copy `packages/scaleev.yaml` to `<config>/packages/` and enable packages if you haven't:
   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```
2. Add the keys from `secrets.example.yaml` to your `secrets.yaml`.
3. Copy `blueprints/automation/scaleev/` to `<config>/blueprints/automation/scaleev/`.
4. Restart Home Assistant, then create an automation from the blueprint **ScaleEV - start charging on plug-in**.

## Finding your station ID
Log in and list stations (replace the values):
```bash
API=https://api-scaleev.demo.scaletech.xyz
TOKEN=$(curl -s $API/drivers/auth/login -H 'content-type: application/json' \
  -d '{"email":"you@example.com","password":"...","tenantSlug":"your-tenant"}' | jq -r .data.access_token)
curl -s $API/drivers/locations -H "authorization: Bearer $TOKEN" | jq '.data[] | {id,name}'
curl -s $API/drivers/locations/<LOCATION_ID>/stations -H "authorization: Bearer $TOKEN" | jq '.data[] | {id,name,status}'
```
Use the station `id` (UUID) in the blueprint. Connector is usually `1`.

## Target energy
Set the target to your vehicle's usable battery capacity so the session isn't cut short.

## Not implemented
Auto-completing Stripe Checkout with a saved card when you don't respond. See the discussion in the issue tracker before adding it.

## Security
Never commit `secrets.yaml` or browser HAR captures (they contain credentials). `.gitignore` excludes both.

## License
MIT - see [LICENSE](LICENSE).
