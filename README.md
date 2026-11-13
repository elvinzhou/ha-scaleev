# ScaleEV for Home Assistant

Start a [ScaleEV](https://scaletech.xyz)-powered charging session automatically when you plug in. Set up entirely from the Home Assistant UI.

> Unofficial. Built on the web app's HTTP API, which may change without notice. Not affiliated with ScaleEV or any charging network.

## What it does
1. Your vehicle's **charge cable connected** sensor turns on while the vehicle is **near the charging location**. ScaleEV provides the location's coordinates, so no zone is needed; the default radius is 150 m.
2. The integration logs in, requests a remote start for your chosen station and connector, and targets the energy you configured.
3. **First session only:** ScaleEV returns a Stripe checkout link. You get a notification with a **Pay now** button, and the integration waits (default 10 minutes) for the payment hold to be authorized.
4. You get a notification when charging starts, or when it fails or times out.

## Install
**HACS (recommended):** HACS → ⋮ → *Custom repositories* → add `https://github.com/elvinzhou/ha-scaleev` as an *Integration* → install **ScaleEV** → restart Home Assistant.

**Manual:** copy `custom_components/scaleev` into your Home Assistant `custom_components` folder and restart.

## Set up
Settings → Devices & services → **Add integration** → **ScaleEV**, then:
1. **Link:** paste any link from your charging network's web app (for example `https://yournetwork.scaleev.scaletech.xyz/map`). If the API address can't be worked out from the link, enter it in the optional field.
2. **Log in** with your ScaleEV account.
3. **Pick** the location, station and connector from lists.
4. **Settings:** target energy, cable sensor, vehicle tracker, radius, notify action and payment timeout. A zone is optional. Set the target to your vehicle's **usable battery capacity** so the session isn't cut short.

Change any of the settings later with the integration's **Configure** button. Add the integration again for a second station or vehicle.

**Zone override:** if you'd rather define the area yourself, or your vehicle tracker has no GPS position (for example a router-based tracker), pick a zone in the settings. A chosen zone is used instead of the location's coordinates. If ScaleEV doesn't provide coordinates for a location, a zone is required.

## Entities
| Entity | What it is |
| --- | --- |
| Button *Start charging* | Start a session now, ignoring the cable sensor and zone |
| Switch *Auto-start on plug-in* | Turn automatic starting on or off |
| Sensor *Connector status* | OCPP status of your connector (e.g. `Available`, `Preparing`, `Charging`) |
| Sensors *Session status / energy / cost* | The live session, when there is one |
| Sensor *Start status* | What the last start attempt did, and why a plug-in was skipped (vehicle not at the charger, already charging, payment not completed, ...). The `detail` attribute has the specifics |

## Notifications
The **Notify action** setting accepts any Home Assistant notify action or notify entity, not just the companion app:

- **Companion app** (`notify.mobile_app_...`): a push notification with a **Pay now** button.
- **Anything else**, such as email ([SMTP](https://www.home-assistant.io/integrations/smtp/)), Telegram, ntfy, Pushover or Signal: a plain message with the checkout link in the text. Set that integration up in Home Assistant first, then pick it here.
- If the action can't be found or fails, the message appears in Home Assistant's own notifications (the bell icon) with a **Pay now** link.

## Troubleshooting
If plugging in does nothing, look at the **Start status** sensor first: it says whether the plug-in was seen and why it was skipped. If it never changes from *Waiting for plug-in*, the cable sensor didn't go from `off` to `on`. Only that change counts as plugging in; a sensor that was already `on` when Home Assistant started, or that comes back `on` after being `unavailable`, does not start a session. The **Start charging** button always works.

## Limitations
- **Cards can't be linked through the API.** The first session needs a one-time Stripe checkout (the **Pay now** notification). Whether a second session skips checkout depends on ScaleEV; please open an issue if it doesn't for you.
- There is no stop command yet. Stop sessions in the ScaleEV app.
- Your ScaleEV password is stored in Home Assistant's config-entry storage, like other integrations' credentials.
- If your password changes, Home Assistant will ask you to re-enter it.

## Contributing and releases
Open a pull request; don't push to `main`. PR titles must be [conventional commits](https://www.conventionalcommits.org/) and PRs are squash-merged, because the title decides the next version:

| Title | Release |
| --- | --- |
| `fix: ...` | patch (0.1.0 -> 0.1.1) |
| `feat: ...` | minor (0.1.0 -> 0.2.0) |
| `feat!: ...` or a `BREAKING CHANGE:` footer | minor while below 1.0, major after |
| `docs:`, `test:`, `ci:`, `chore:`, `refactor:` | no release |

On merge to `main`, a workflow looks at the commits since the last tag and, if any are releasable, publishes the next version as a GitHub release (tag, generated notes and a `scaleev.zip` that HACS installs). There is no version-bump commit or PR; the version is written into the zip.

## Development
```bash
pip install -r requirements_test.txt
pytest
```
Tests run the config flow and the start sequence against a mocked API. Never commit browser HAR captures; they contain credentials (`*.har` is git-ignored).

## License
MIT - see [LICENSE](LICENSE).
