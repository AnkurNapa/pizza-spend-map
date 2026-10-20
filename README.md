# Pizza Spend Map

Ranks every MSOA in seven East London boroughs and eight Essex districts by how much takeaway pizza demand it has, how much people there can spend and how few pizza places already serve it.

Live: https://ankurnapa.github.io/pizza-spend-map/

## Build

```
pip3 install shapely pyproj openpyxl
python3 scripts/build.py                       # cached raw files, downloads only what is missing
python3 scripts/build.py --refresh-competitors # re-pull FSA and OpenStreetMap
```

`scripts/build.py` downloads, cleans and scores the data, then writes `docs/data.geojson`, `docs/competitors.json` and `docs/schools.json`. It also stamps `?v=` hashes on every asset link in `docs/index.html`. The scoring lives in `score()`. Its asserts in `_self_check()` run on every build.

A GitHub Action rebuilds and deploys on each push. On the 1st of each month it re-pulls the competitor data and commits it.

## Data

Every source, with its URL and download date, is listed in the page footer. Download dates are kept in `data/raw/fetched.json`. Council takeaway policies and the Family Spending figures were researched by hand and sit in `data/raw/policy_and_spend.json`.

No data from Just Eat, Deliveroo, Uber Eats or Google Maps is used.

## Known limits

- Competition counts outlets inside each MSOA only. A pizza place just over a boundary still serves the area next door.
- FSA names are searched for "pizza", "domino" and "papa john". A pizza place with none of those in its name is only caught if OpenStreetMap tags it.
- School zones are drawn for secondary and all-through schools. The London Plan and some councils also cover primary schools.
- CARTO Voyager tiles now need an API key, so the map uses Esri's keyless light grey basemap.
