# config.json

Copy `config.example.json` to `config.json` in the repo root and edit it (an older `Tessie/config.json` is still read when the root one is missing; `TESLA_CONFIG` names another file). `config.json` is git-ignored: keep passwords and API keys there, never in a committed file. The `app.*` keys are described at the top of `Tools/lib/app_config.py`.

| Key | What it is |
|---|---|
| `directories.archive` | Optional (null). When unset, archives go to an `archive` folder inside `directories.data` or the JSON folder. |
| `directories.external_drive` | Null or unset: drives matching `/Volumes/TESLADRIVE*` are found automatically. |
| `directories.power_invoices` | Optional. Folder with the power provider's invoice PDFs and manual usage CSVs. Unset: that provider finds nothing and is skipped. |
| `evnex.username`, `evnex.password` | Your Evnex app login (email and password), used by the community `evnex` library. Evnex gives API keys only to Enterprise customers. The env vars `EVNEX_CLIENT_USERNAME` and `EVNEX_CLIENT_PASSWORD` can be used instead. |
| `pv.provider` | Which `Tools/providers/pv/` provider is used. Default `solaredge`. |
| `solaredge.api_key`, `solaredge.site_id` | SolarEdge Monitoring API credentials (`providers/pv/pv_solaredge.py`). |
| `pvoutput.api_key`, `pvoutput.system_id` | Placeholders for PVOutput.org (`resources/PVOUTPUT_API.md`). Nothing uses them by default: the PVOutput setup here only relayed the same SolarEdge account. |
| `power.provider` | Which `Tools/providers/power/` provider is used. Default `agl`. |
| `power.usage_staleness_days` | Age in days after which the usage-export reminder starts. Default 14. A reminder only. |
| `osm.client_id`, `osm.client_secret` | OpenStreetMap OAuth2 app (register at openstreetmap.org/oauth2/applications). Used by `places_osm_editor.py`; leave `osm` out if you don't use it. |
| `abrp.target_arrival_soc` | Lowest acceptable arrival battery %. Default 10. Flag `--target-soc`. |
| `abrp.max_charge_soc` | Charge-to ceiling % at an en-route stop. Default 80 (charging tapers past it). Flag `--max-soc`. |
| `abrp.corridor_radius_km` | Largest sideways detour from the route in km. Default 5.0. Flag `--corridor-radius`. |

The old `solar` block (ROI figures) is now `Solar/solar_system.json` under `directories.data`; see `Solar/solar_system.example.json` and `lib/solar_system_config.py`.
