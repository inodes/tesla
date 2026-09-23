# PlugShare Discrepancy & Error Audit Report
**Generated:** 2026-09-23 01:44:23  
**Scope:** Every Tier 1 Authoritative Provider (Tesla, Chargefox/NRMA/WA EV Network/Qld Electric Highway, EVX, Evie, bp pulse, AmpCharge/Ampol Foodary) vs Tier 2 PlugShare Aggregator

## 1. Executive Summary

| Metric | Count |
| :--- | :--- |
| **Total First-Party Stations** | 687 |
| **Matched & Consolidated with PlugShare** | 149 |
| **Missing from PlugShare (New Deployments)** | 469 |
| **Network Misattributions (e.g. '3rd-Party' instead of the real network)** | 22 |
| **Missing / Stale Tariffs** | 52 |
| **Hardware / Power / Stall Mismatches** | 26 |

---
## 2. Network Misattributions on PlugShare
These stations are listed under generic or incorrect network tags on PlugShare and should be reassigned to the proper network operator:

| Station Name | State | Current PlugShare Network | Correct Network | PlugShare Edit Link |
| :--- | :---: | :--- | :--- | :--- |
| **Holiday Inn Express Sydney Macquarie Park** | NSW | `3rd-Party` | **Tesla Destination** | [Edit on PlugShare #654757](https://www.plugshare.com/location/654757) |
| **Howlong Golf Resort** | NSW | `Charge Hub` | **Tesla Destination** | [Edit on PlugShare #130644](https://www.plugshare.com/location/130644) |
| **Rhodes Waterside - Level P3** | NSW | `Chargefox` | **Tesla Destination** | [Edit on PlugShare #137100](https://www.plugshare.com/location/137100) |
| **Shaw Wines** | NSW | `Porsche Destination Charging` | **Tesla Destination** | [Edit on PlugShare #108339](https://www.plugshare.com/location/108339) |
| **Spicers Guesthouse** | NSW | `Porsche Destination Charging` | **Tesla Destination** | [Edit on PlugShare #207913](https://www.plugshare.com/location/207913) |
| **Darwin Waterfront** | NT | `Exploren` | **Tesla Destination** | [Edit on PlugShare #607138](https://www.plugshare.com/location/607138) |
| **Dalby Trailers** | QLD | `3rd-Party` | **Tesla Destination** | [Edit on PlugShare #622421](https://www.plugshare.com/location/622421) |
| **Mercure Sunshine Coast Kawana Waters** | QLD | `3rd-Party` | **Tesla Destination** | [Edit on PlugShare #554145](https://www.plugshare.com/location/554145) |
| **Sheraton Grand Mirage Resort, Port Douglas** | QLD | `3rd-Party` | **Tesla Destination** | [Edit on PlugShare #581328](https://www.plugshare.com/location/581328) |
| **Spicers Hidden Vale** | QLD | `3rd-Party` | **Tesla Destination** | [Edit on PlugShare #2028373](https://www.plugshare.com/location/2028373) |
| **StampingSimulation.com Pty Ltd** | QLD | `3rd-Party` | **Tesla Destination** | [Edit on PlugShare #555359](https://www.plugshare.com/location/555359) |
| **Sunshine Coast Airport, QLD** | QLD | `3rd-Party` | **Tesla Destination** | [Edit on PlugShare #650726](https://www.plugshare.com/location/650726) |
| **Kapunda Town Centre Public Car Park** | SA | `3rd-Party` | **Tesla Destination** | [Edit on PlugShare #177831](https://www.plugshare.com/location/177831) |
| **Two Hands Wines** | SA | `3rd-Party` | **Tesla Destination** | [Edit on PlugShare #589560](https://www.plugshare.com/location/589560) |
| **MACq 01 Hotel** | TAS | `Exploren` | **Tesla Destination** | [Edit on PlugShare #2166624](https://www.plugshare.com/location/2166624) |
| **Paterson Street West Car Park Ground Floor** | TAS | `Chargefox` | **Tesla Destination** | [Edit on PlugShare #264396](https://www.plugshare.com/location/264396) |
| **Barwon Valley Lodge** | VIC | `3rd-Party` | **Tesla Destination** | [Edit on PlugShare #708112](https://www.plugshare.com/location/708112) |
| **Billson's** | VIC | `3rd-Party` | **Tesla Destination** | [Edit on PlugShare #586181](https://www.plugshare.com/location/586181) |
| **Bridge Road Brewers** | VIC | `3rd-Party` | **Tesla Destination** | [Edit on PlugShare #299742](https://www.plugshare.com/location/299742) |
| **Geelong Healthcare Precinct** | VIC | `3rd-Party` | **Tesla Destination** | [Edit on PlugShare #589175](https://www.plugshare.com/location/589175) |
| **Mercure Bendigo Schaller** | VIC | `Noodoe EV` | **Tesla Destination** | [Edit on PlugShare #287875](https://www.plugshare.com/location/287875) |
| **Scion Wine** | VIC | `3rd-Party` | **Tesla Destination** | [Edit on PlugShare #2086726](https://www.plugshare.com/location/2086726) |

---
## 3. Missing or Stale Tariffs on PlugShare
These stations currently show a missing, unlisted, or stale rate on PlugShare that no longer matches verified First-Party pricing:

| Station Name | State | Listed on PlugShare | Authoritative Operator Tariff | PlugShare Edit Link |
| :--- | :---: | :--- | :--- | :--- |
| **Majura Park, ACT** | ACT | $0.00 / Unlisted | **$0.32 - $0.32/kWh (TOU)** | [Update Tariff #168734](https://www.plugshare.com/location/168734) |
| **Nicholls, ACT** | ACT | $0.00 / Unlisted | **$0.32 - $0.32/kWh (TOU)** | [Update Tariff #1922269](https://www.plugshare.com/location/1922269) |
| **Tuggeranong, ACT** | ACT | $0.00 / Unlisted | **$0.32 - $0.32/kWh (TOU)** | [Update Tariff #411514](https://www.plugshare.com/location/411514) |
| **Albury, NSW** | NSW | $0.00 / Unlisted | **$0.48 - $0.48/kWh (TOU)** | [Update Tariff #578320](https://www.plugshare.com/location/578320) |
| **Blaxland, NSW** | NSW | $0.00 / Unlisted | **$0.32 - $0.32/kWh (TOU)** | [Update Tariff #496710](https://www.plugshare.com/location/496710) |
| **Central Coast, NSW** | NSW | $0.00 / Unlisted | **$0.43 - $0.43/kWh (TOU)** | [Update Tariff #194828](https://www.plugshare.com/location/194828) |
| **Dubbo, NSW** | NSW | $0.00 / Unlisted | **$0.48 - $0.48/kWh (TOU)** | [Update Tariff #186029](https://www.plugshare.com/location/186029) |
| **Eastlakes, NSW** | NSW | $0.00 / Unlisted | **$0.31 - $0.31/kWh (TOU)** | [Update Tariff #1217744](https://www.plugshare.com/location/1217744) |
| **Eastwood Hotel** | NSW | $0.00 / Unlisted | **$0.40 - $0.40/kWh (TOU)** | [Update Tariff #238151](https://www.plugshare.com/location/238151) |
| **Forest Lodge, NSW** | NSW | $0.00 / Unlisted | **$0.34 - $0.34/kWh (TOU)** | [Update Tariff #571672](https://www.plugshare.com/location/571672) |
| **Holbrook, NSW** | NSW | $0.00 / Unlisted | **$0.48 - $0.48/kWh (TOU)** | [Update Tariff #568937](https://www.plugshare.com/location/568937) |
| **Holiday Inn Express Sydney Macquarie Park** | NSW | $0.00 / Unlisted | **$0.30 - $0.30/kWh (TOU)** | [Update Tariff #654757](https://www.plugshare.com/location/654757) |
| **Kirrawee, NSW** | NSW | $0.00 / Unlisted | **$0.37 - $0.37/kWh (TOU)** | [Update Tariff #360571](https://www.plugshare.com/location/360571) |
| **Macquarie, NSW** | NSW | $0.00 / Unlisted | **$0.35 - $0.59/kWh (TOU)** | [Update Tariff #239154](https://www.plugshare.com/location/239154) |
| **Parkes, NSW** | NSW | $0.00 / Unlisted | **$0.48 - $0.48/kWh (TOU)** | [Update Tariff #586889](https://www.plugshare.com/location/586889) |
| **Pokolbin, NSW** | NSW | $0.00 / Unlisted | **$0.49 - $0.49/kWh (TOU)** | [Update Tariff #1539628](https://www.plugshare.com/location/1539628) |
| **Raymond Terrace, NSW** | NSW | $0.00 / Unlisted | **$0.48 - $0.48/kWh (TOU)** | [Update Tariff #600310](https://www.plugshare.com/location/600310) |
| **South Nowra, NSW** | NSW | $0.00 / Unlisted | **$0.45 - $0.45/kWh (TOU)** | [Update Tariff #1987100](https://www.plugshare.com/location/1987100) |
| **Tweed Heads, NSW** | NSW | $0.00 / Unlisted | **$0.31 - $0.31/kWh (TOU)** | [Update Tariff #582658](https://www.plugshare.com/location/582658) |
| **Wollongong, NSW** | NSW | $0.00 / Unlisted | **$0.31 - $0.38/kWh (TOU)** | [Update Tariff #470116](https://www.plugshare.com/location/470116) |
| **Dalby Trailers** | QLD | $0.00 / Unlisted | **$0.49 - $0.49/kWh (TOU)** | [Update Tariff #622421](https://www.plugshare.com/location/622421) |
| **Gympie, QLD** | QLD | $0.00 / Unlisted | **$0.48 - $0.48/kWh (TOU)** | [Update Tariff #240959](https://www.plugshare.com/location/240959) |
| **Helidon, QLD** | QLD | $0.00 / Unlisted | **$0.32 - $0.32/kWh (TOU)** | [Update Tariff #1553149](https://www.plugshare.com/location/1553149) |
| **Mount Sheridan, QLD** | QLD | $0.00 / Unlisted | **$0.32 - $0.32/kWh (TOU)** | [Update Tariff #1027075](https://www.plugshare.com/location/1027075) |
| **Rochedale, QLD** | QLD | $0.00 / Unlisted | **$0.34 - $0.52/kWh (TOU)** | [Update Tariff #499893](https://www.plugshare.com/location/499893) |
| **Runaway Bay, QLD** | QLD | $0.00 / Unlisted | **$0.34 - $0.34/kWh (TOU)** | [Update Tariff #521111](https://www.plugshare.com/location/521111) |
| **Sunshine Coast Airport, QLD** | QLD | $0.00 / Unlisted | **$0.41 - $0.41/kWh (TOU)** | [Update Tariff #650726](https://www.plugshare.com/location/650726) |
| **Sunshine Coast, QLD** | QLD | $0.00 / Unlisted | **$0.34 - $0.34/kWh (TOU)** | [Update Tariff #1430312](https://www.plugshare.com/location/1430312) |
| **The Retreat Beach Houses** | QLD | $0.00 / Unlisted | **$0.41 - $0.41/kWh (TOU)** | [Update Tariff #238195](https://www.plugshare.com/location/238195) |
| **Toowoomba, QLD** | QLD | $0.00 / Unlisted | **$0.32 - $0.32/kWh (TOU)** | [Update Tariff #238841](https://www.plugshare.com/location/238841) |
| **Townsville, QLD** | QLD | $0.00 / Unlisted | **$0.44 - $0.44/kWh (TOU)** | [Update Tariff #726691](https://www.plugshare.com/location/726691) |
| **Yandina, QLD** | QLD | $0.00 / Unlisted | **$0.48 - $0.48/kWh (TOU)** | [Update Tariff #638840](https://www.plugshare.com/location/638840) |
| **Glenorchy, TAS** | TAS | $0.00 / Unlisted | **$0.41 - $0.41/kWh (TOU)** | [Update Tariff #1254853](https://www.plugshare.com/location/1254853) |
| **Paterson Street West Car Park Ground Floor** | TAS | $0.32/kWh (stale) | **$0.40/kWh** | [Update Tariff #264396](https://www.plugshare.com/location/264396) |
| **Ballarat, VIC** | VIC | $0.00 / Unlisted | **$0.31 - $0.31/kWh (TOU)** | [Update Tariff #108180](https://www.plugshare.com/location/108180) |
| **Bendigo, VIC** | VIC | $0.00 / Unlisted | **$0.32 - $0.32/kWh (TOU)** | [Update Tariff #210220](https://www.plugshare.com/location/210220) |
| **Braybrook, VIC** | VIC | $0.00 / Unlisted | **$0.30 - $0.30/kWh (TOU)** | [Update Tariff #719595](https://www.plugshare.com/location/719595) |
| **Bridge Road Brewers** | VIC | $0.00 / Unlisted | **$0.32 - $0.32/kWh (TOU)** | [Update Tariff #299742](https://www.plugshare.com/location/299742) |
| **Bright, VIC** | VIC | $0.00 / Unlisted | **$0.43 - $0.43/kWh (TOU)** | [Update Tariff #639155](https://www.plugshare.com/location/639155) |
| **Campbell St Motor Lodge** | VIC | $0.00 / Unlisted | **$0.40 - $0.40/kWh (TOU)** | [Update Tariff #252774](https://www.plugshare.com/location/252774) |
| **Corio, VIC** | VIC | $0.00 / Unlisted | **$0.34 - $0.34/kWh (TOU)** | [Update Tariff #321326](https://www.plugshare.com/location/321326) |
| **Geelong, VIC** | VIC | $0.70/kWh (stale) | **$0.37/kWh** | [Update Tariff #1324720](https://www.plugshare.com/location/1324720) |
| **Keysborough, VIC** | VIC | $0.00 / Unlisted | **$0.31 - $0.31/kWh (TOU)** | [Update Tariff #840623](https://www.plugshare.com/location/840623) |
| **Kilmore, VIC** | VIC | $0.00 / Unlisted | **$0.43 - $0.43/kWh (TOU)** | [Update Tariff #583267](https://www.plugshare.com/location/583267) |
| **Laverton, VIC** | VIC | $0.00 / Unlisted | **$0.30 - $0.30/kWh (TOU)** | [Update Tariff #653047](https://www.plugshare.com/location/653047) |
| **Lucas, VIC** | VIC | $0.00 / Unlisted | **$0.27 - $0.27/kWh (TOU)** | [Update Tariff #546804](https://www.plugshare.com/location/546804) |
| **Ovens, VIC** | VIC | $0.64/kWh (stale) | **$0.43/kWh** | [Update Tariff #199337](https://www.plugshare.com/location/199337) |
| **Pakenham, VIC** | VIC | $0.00 / Unlisted | **$0.34 - $0.34/kWh (TOU)** | [Update Tariff #856618](https://www.plugshare.com/location/856618) |
| **Wallan, VIC** | VIC | $0.00 / Unlisted | **$0.43 - $0.43/kWh (TOU)** | [Update Tariff #1987133](https://www.plugshare.com/location/1987133) |
| **Wangaratta, VIC** | VIC | $0.00 / Unlisted | **$0.43 - $0.43/kWh (TOU)** | [Update Tariff #567902](https://www.plugshare.com/location/567902) |
| **Wodonga, VIC** | VIC | $0.70/kWh (stale) | **$0.43/kWh** | [Update Tariff #72258](https://www.plugshare.com/location/72258) |
| **Yea, VIC** | VIC | $0.00 / Unlisted | **$0.43 - $0.43/kWh (TOU)** | [Update Tariff #314149](https://www.plugshare.com/location/314149) |

---
## 4. Hardware / Power / Stall Mismatches on PlugShare
These stations' PlugShare listing disagrees with verified First-Party hardware data:

| Station Name | State | Discrepancies | PlugShare Edit Link |
| :--- | :---: | :--- | :--- |
| **Blaxland, NSW** | NSW | Power: 250 kW ➔ 300 kW | [Update on PlugShare #496710](https://www.plugshare.com/location/496710) |
| **Eastwood Hotel** | NSW | Power: 7 kW ➔ 19 kW | [Update on PlugShare #238151](https://www.plugshare.com/location/238151) |
| **Holbrook, NSW** | NSW | Power: 250 kW ➔ 325 kW | [Update on PlugShare #568937](https://www.plugshare.com/location/568937) |
| **Mudgee Information Centre** | NSW | Power: 11.5 kW ➔ 22 kW; Stalls: 1 ➔ 2 | [Update on PlugShare #250416](https://www.plugshare.com/location/250416) |
| **Pullman Magenta Shores Resort** | NSW | Power: 11 kW ➔ 22 kW | [Update on PlugShare #78894](https://www.plugshare.com/location/78894) |
| **Raymond Terrace, NSW** | NSW | Power: 250 kW ➔ 300 kW | [Update on PlugShare #600310](https://www.plugshare.com/location/600310) |
| **Wollongong, NSW** | NSW | Power: 250 kW ➔ 300 kW | [Update on PlugShare #470116](https://www.plugshare.com/location/470116) |
| **Darwin City Edge Resort** | NT | Power: 3 kW ➔ 6 kW | [Update on PlugShare #109595](https://www.plugshare.com/location/109595) |
| **Dalby Trailers** | QLD | Stalls: 3 ➔ 4 | [Update on PlugShare #622421](https://www.plugshare.com/location/622421) |
| **Noosa Tropicana** | QLD | Power: 6 kW ➔ 10 kW | [Update on PlugShare #376985](https://www.plugshare.com/location/376985) |
| **Room Motels Gatton** | QLD | Power: 3 kW ➔ 6 kW | [Update on PlugShare #141516](https://www.plugshare.com/location/141516) |
| **Spicers Hidden Vale** | QLD | Stalls: 2 ➔ 3 | [Update on PlugShare #2028373](https://www.plugshare.com/location/2028373) |
| **Spicers Tamarind Retreat** | QLD | Power: 4 kW ➔ 6 kW | [Update on PlugShare #139147](https://www.plugshare.com/location/139147) |
| **Kapunda Town Centre Public Car Park** | SA | Stalls: 1 ➔ 2 | [Update on PlugShare #177831](https://www.plugshare.com/location/177831) |
| **MACq 01 Hotel** | TAS | Stalls: 3 ➔ 5 | [Update on PlugShare #2166624](https://www.plugshare.com/location/2166624) |
| **Willie Smith's Apple Shed** | TAS | Stalls: 1 ➔ 2 | [Update on PlugShare #119616](https://www.plugshare.com/location/119616) |
| **Bilson's Landing** | VIC | Power: 4 kW ➔ 6 kW | [Update on PlugShare #177686](https://www.plugshare.com/location/177686) |
| **Bridge Road Brewers** | VIC | Stalls: 1 ➔ 6 | [Update on PlugShare #299742](https://www.plugshare.com/location/299742) |
| **Campbell St Motor Lodge** | VIC | Power: 5 kW ➔ 19 kW | [Update on PlugShare #252774](https://www.plugshare.com/location/252774) |
| **Kennington Village Shopping Centre** | VIC | Power: 11 kW ➔ 22 kW | [Update on PlugShare #139688](https://www.plugshare.com/location/139688) |
| **Laverton, VIC** | VIC | Power: 250 kW ➔ 300 kW | [Update on PlugShare #653047](https://www.plugshare.com/location/653047) |
| **Lucas, VIC** | VIC | Stalls: 10 ➔ 20 | [Update on PlugShare #546804](https://www.plugshare.com/location/546804) |
| **Prani Glow Day Spa** | VIC | Power: 7 kW ➔ 19 kW | [Update on PlugShare #265811](https://www.plugshare.com/location/265811) |
| **Scion Wine** | VIC | Stalls: 1 ➔ 2 | [Update on PlugShare #2086726](https://www.plugshare.com/location/2086726) |
| **Wangaratta, VIC** | VIC | Stalls: 4 ➔ 8 | [Update on PlugShare #567902](https://www.plugshare.com/location/567902) |
| **Yea, VIC** | VIC | Power: 120 kW ➔ 150 kW | [Update on PlugShare #314149](https://www.plugshare.com/location/314149) |

---
## 5. First-Party Stations Missing from PlugShare

These active first-party stations are not yet registered on PlugShare:

| Station Name | Network | State | Address | Power | Coordinates |
| :--- | :--- | :---: | :--- | :--- | :--- |
| **Canberra Labor Club Car Park – Level 3** | Tesla Destination | ACT | 51 Chandler St Cameron Ave, Belconnen, Australian Capital Territory | 7.0 kW | `-35.24090, 149.07140` |
| **Chisholm Village Shopping Centre** | Tesla Destination | ACT | 42 Halley Street, Chisholm, Australian Capital Territory | 22.0 kW | `-35.41381, 149.12838` |
| **Crowne Plaza Canberra** | Tesla Destination | ACT | 1 Binara St, Canberra, Australian Capital Territory | 22.0 kW | `-35.28235, 149.13436` |
| **Hotel Kurrajong** | Tesla Destination | ACT | 8 National Cir, Barton, Australian Capital Territory | 22.0 kW | `-35.30759, 149.13448` |
| **Mercure Canberra Belconnen - Level 4** | Tesla Destination | ACT | 59 Cameron Ave, Belconnen, Australian Capital Territory | 7.0 kW | `-35.24054, 149.07136` |
| **Mode 3 Carpark Braddon (Ground level Carpark)** | Tesla Destination | ACT | 22 Lonsdale Street, Braddon, Australian Capital Territory | 22.0 kW | `-35.27277, 149.13319` |
| **National Zoo & Aquarium** | Tesla Destination | ACT | 999 Lady Denman Drive, Weston Creek, Australian Capital Territory | 7.0 kW | `-35.30910, 149.07733` |
| **Abercrombie House** | Tesla Destination | NSW | 311 Ophir Rd, Bathurst, New South Wales | 22.0 kW | `-33.39229, 149.51767` |
| **Alabaster Motor Inn** | Tesla Destination | NSW | 23-25 Oxley Street, Taree, New South Wales | 22.0 kW | `-31.90289, 152.47737` |
| **Albacore Apartments** | Tesla Destination | NSW | Market Street, Merimbula, New South Wales | 7.0 kW | `-36.89278, 149.90909` |
| **Alexandria, NSW** | Tesla | NSW | 1/118 Bourke Road, Alexandria, New South Wales | 300.0 kW | `-33.91282, 151.19334` |
| **Anchors Aweigh Narooma** | Tesla Destination | NSW | 5 Tilba Street, Narooma, New South Wales | 22.0 kW | `-36.21966, 150.13339` |
| **Armidale Motel** | Tesla Destination | NSW | 66 Glen Innes Rd, Armidale, New South Wales | 11.0 kW | `-30.49508, 151.66964` |
| **Armidale, NSW** | Tesla | NSW | 117 Jessie St, Armidale, New South Wales | 300.0 kW | `-30.51326, 151.66349` |
| **Balgowlah, NSW** | Tesla | NSW | 197-215 Condamine St, Balgowlah, New South Wales | 300.0 kW | `-33.79283, 151.26506` |
| **Balgowlah, NSW** | Tesla Destination | NSW | 197 Condamine St, Balgowlah, New South Wales | 11.0 kW | `-33.79283, 151.26506` |
| **Bangalay Luxury Villas** | Tesla Destination | NSW | 30 Staples Street, Shoalhaven Heads, New South Wales | 22.0 kW | `-34.85016, 150.74781` |
| **Bankstown, NSW** | Tesla | NSW | 173 Canterbury Road, Bankstown, New South Wales | 300.0 kW | `-33.93272, 151.02785` |
| **Bannisters by the Sea** | Tesla Destination | NSW | 191 Mitchell Parade, Mollymook, New South Wales | 22.0 kW | `-35.32272, 150.48201` |
| **Bannisters Pavilion** | Tesla Destination | NSW | 87 Tallwood Avenue, Mollymook, New South Wales | 22.0 kW | `-35.32506, 150.47638` |
| **Batemans Bay, NSW** | Tesla | NSW | 154 Beach Rd, Batemans Bay, New South Wales | 300.0 kW | `-35.71917, 150.19018` |
| **Baths Motel** | Tesla Destination | NSW | 339 Warialda Street, Moree, New South Wales | 8.0 kW | `-29.47534, 149.84563` |
| **Bathurst Central** | Tesla Destination | NSW | Level 1, 121 Howick Street, Bathurst, New South Wales | 7.0 kW | `-33.41844, 149.58286` |
| **Bathurst, NSW** | Tesla | NSW | 1 Kendall Ave, Bathurst, New South Wales | 130.0 kW | `-33.41899, 149.58644` |
| **Baulkham Hills Sports Club** | Tesla Destination | NSW | 11 Renown Road, Baulkham Hills, New South Wales | 22.0 kW | `-33.76253, 151.00447` |
| **Baulkham Hills, NSW** | Tesla | NSW | 375-383 Windsor Rd, Baulkham Hills, New South Wales | 250.0 kW | `-33.75948, 150.99225` |
| **Beach Hotel Resort** | Tesla Destination | NSW | 1 Bay Lane, Byron Bay, New South Wales | 19.0 kW | `-28.64182, 153.61363` |
| **Beach Suites Byron Bay** | Tesla Destination | NSW | 20 Bay St, Byron Bay, New South Wales | 6.0 kW | `-28.64167, 153.61468` |
| **Berida Hotel** | Tesla Destination | NSW | 6 David St, Bowral, New South Wales | 7.0 kW | `-34.48802, 150.41398` |
| **Berry, NSW** | Tesla | NSW | B640 Princes Highway, Berry, New South Wales | 130.0 kW | `-34.80987, 150.63681` |
| **Best Western PLUS Apollo International** | Tesla Destination | NSW | 290 Pacific Highway, Charlestown, New South Wales | 6.0 kW | `-32.95694, 151.70102` |
| **Best Western Plus Goulburn** | Tesla Destination | NSW | 77 Lagoon St, Goulburn, New South Wales | 22.0 kW | `-34.74773, 149.72937` |
| **Birkenhead Point Outlet Centre** | Tesla Destination | NSW | 19 Roseby Street, Drummoyne, New South Wales | 22.0 kW | `-33.85585, 151.16124` |
| **Blue Gum Motor Inn** | Tesla Destination | NSW | 109 Cobra St, Dubbo, New South Wales | 14.0 kW | `-32.25475, 148.61014` |
| **Bourke Riverside Motel** | Tesla Destination | NSW | 3/13 Mitchell Street, Bourke, New South Wales | 22.0 kW | `-30.08820, 145.93389` |
| **Bowral, NSW** | Tesla Destination | NSW | 252 Centennial Rd, Bowral, New South Wales | 7.0 kW | `-34.46304, 150.40149` |
| **Briar Ridge Vineyard** | Tesla Destination | NSW | 593 Mount View Road, Mount View, New South Wales | 22.0 kW | `-32.83938, 151.29856` |
| **Broadway Shopping Centre - Level B2** | Tesla Destination | NSW | 1 Bay Street, Broadway, New South Wales | 22.0 kW | `-33.88364, 151.19488` |
| **Broadway, NSW** | Tesla | NSW | 1 Bay Street, Broadway, New South Wales | 130.0 kW | `-33.88290, 151.19482` |
| **Brokenwood Wines** | Tesla Destination | NSW | 401-427 McDonalds Road, Pokolbin, New South Wales | 22.0 kW | `-32.77565, 151.29994` |
| **Brunswick River Inn** | Tesla Destination | NSW | 2 The Terrace, Brunswick Heads, New South Wales | 6.0 kW | `-28.53978, 153.55224` |
| **Bundanon** | Tesla Destination | NSW | 170 Riversdale Road, Illaroo, New South Wales | 22.0 kW | `-34.86498, 150.49251` |
| **Byron Bay, NSW** | Tesla | NSW | 139 Jonson St, Byron Bay, New South Wales | 250.0 kW | `-28.65026, 153.61378` |
| **Cabins in the Clouds** | Tesla Destination | NSW | 424-426 Moonabung Road, Vacy, New South Wales | 22.0 kW | `-32.54298, 151.50049` |
| **Cabramatta Hotel** | Tesla Destination | NSW | 224 Railway Parade, Cabramatta, New South Wales | 19.0 kW | `-33.89670, 150.93800` |
| **Campbelltown, NSW** | Tesla | NSW | 20-22 Camden Road, Campbelltown, New South Wales | 300.0 kW | `-34.07154, 150.80732` |
| **Casino Returned Servicemen's Memorial Club** | Tesla Destination | NSW | 162 Canterbury Street, Casino, New South Wales | 19.0 kW | `-28.86292, 153.04897` |
| **Castle Towers Red Car Park B1 Row 7** | Tesla Destination | NSW | 1 Eric Felton St, Castle Hill, New South Wales | 22.0 kW | `-33.72920, 151.00681` |
| **Central Jetty Motel** | Tesla Destination | NSW | 509 - 517 The Entrance Road, Long Jetty, New South Wales | 19.0 kW | `-33.36743, 151.47562` |
| **CH Boutique Hotel** | Tesla Destination | NSW | 52 Brisbane Street, Tamworth, New South Wales | 22.0 kW | `-31.08948, 150.92991` |

*(Truncated: 419 additional missing stations omitted from table)*
